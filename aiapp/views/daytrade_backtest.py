#aiapp/views/daytrade_backtest.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from aiapp.services.daytrade.policy_loader import load_policy_yaml
from aiapp.services.daytrade.risk_math import calc_risk_budget_yen

from aiapp.services.daytrade.backtest_multi_service import (
    run_daytrade_backtest_multi,
    last_n_bdays_jst,
)

# 追加：Judge + AutoFix まで含めた“本番想定”サービス（無ければ fallback）
try:
    from aiapp.services.daytrade.backtest_multi_service import run_daytrade_backtest_multi_with_judge_autofix
except Exception:
    run_daytrade_backtest_multi_with_judge_autofix = None  # type: ignore


# 開発用：まずは「これだけで開発してOK」な固定リスト
DEV_DEFAULT_TICKERS = ["7203", "6758", "9984", "8306", "8316", "8035", "6861", "6501", "9432", "6098"]


def _parse_tickers(text: str) -> List[str]:
    s = (text or "").replace(",", " ").replace("\n", " ")
    xs = [x.strip() for x in s.split(" ") if x.strip()]
    out = []
    for x in xs:
        if x.isdigit() and (4 <= len(x) <= 5):
            out.append(x)
    seen = set()
    uniq = []
    for c in out:
        if c in seen:
            continue
        seen.add(c)
        uniq.append(c)
    return uniq


def _diff_policy_simple(base: Dict[str, Any], cand: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    auto_fix の “何が変わったか” を初心者向けに出す（安全な範囲だけ）。
    いまの auto_fix が触るのは exit.take_profit_r / exit.max_hold_minutes なのでそこだけ見る。
    """
    out: List[Dict[str, Any]] = []

    def getp(d: Dict[str, Any], path: List[str], default=None):
        cur: Any = d
        for k in path:
            if not isinstance(cur, dict):
                return default
            if k not in cur:
                return default
            cur = cur[k]
        return cur

    watch = [
        (["exit", "take_profit_r"], "利確ライン（R）"),
        (["exit", "max_hold_minutes"], "最大保有時間（分）"),
    ]

    for path, label in watch:
        b = getp(base, path, None)
        c = getp(cand, path, None)
        if b != c:
            out.append(
                {
                    "label": label,
                    "path": ".".join(path),
                    "before": b,
                    "after": c,
                }
            )
    return out


def _judge_to_dict(j) -> Dict[str, Any]:
    if not j:
        return {"decision": "", "reasons": [], "metrics": {}}
    return {
        "decision": str(getattr(j, "decision", "") or ""),
        "reasons": list(getattr(j, "reasons", []) or []),
        "metrics": dict(getattr(j, "metrics", {}) or {}),
    }


def daytrade_backtest_view(request: HttpRequest) -> HttpResponse:
    # ---------- form defaults ----------
    form_n = 20
    form_mode = "dev_default"  # dev_default / manual / auto
    form_tickers = ""
    form_top = 40
    form_scan_limit = 2000
    form_pre_rank_pool = 400

    run_log_lines: List[str] = []

    selected_tickers: List[str] = []
    rows: List[Dict[str, Any]] = []
    exit_rows: List[Dict[str, Any]] = []

    # KPI（表示は “採用後(applied)” を優先）
    kpi_total_pnl = 0
    kpi_trades = 0
    kpi_winrate = "-"
    kpi_avg_r = "-"
    kpi_max_dd = 0

    # base（参考表示）
    base_kpi: Dict[str, Any] | None = None

    # ---------- judge / autofix outputs ----------
    judge_enabled = True
    autofix_enabled = run_daytrade_backtest_multi_with_judge_autofix is not None

    base_judge: Dict[str, Any] | None = None
    applied_judge: Dict[str, Any] | None = None

    fix_summary: Dict[str, Any] | None = None

    # ---------- policy ----------
    try:
        loaded = load_policy_yaml()
        policy = loaded.policy
        policy_id = loaded.policy_id
    except Exception as e:
        policy = {}
        policy_id = ""
        run_log_lines.append(f"[error] policy load failed: {e}")

    # budget (for R conversion)
    capital_cfg = (policy or {}).get("capital", {})
    risk_cfg = (policy or {}).get("risk", {})
    base_capital = int(capital_cfg.get("base_capital", 0) or 0)
    trade_loss_pct = float(risk_cfg.get("trade_loss_pct", 0.0) or 0.0)
    day_loss_pct = float(risk_cfg.get("day_loss_pct", 0.0) or 0.0)
    budget = calc_risk_budget_yen(base_capital, trade_loss_pct, day_loss_pct)
    budget_trade_loss_yen = max(int(getattr(budget, "trade_loss_yen", 1)), 1)

    if request.method == "POST":
        # ---------- read form ----------
        try:
            form_n = int(request.POST.get("n") or 20)
        except Exception:
            form_n = 20

        form_mode = str(request.POST.get("mode") or "dev_default").strip()
        form_tickers = str(request.POST.get("tickers") or "")

        try:
            form_top = int(request.POST.get("top") or 40)
        except Exception:
            form_top = 40

        try:
            form_scan_limit = int(request.POST.get("scan_limit") or 2000)
        except Exception:
            form_scan_limit = 2000

        try:
            form_pre_rank_pool = int(request.POST.get("pre_rank_pool") or 400)
        except Exception:
            form_pre_rank_pool = 400

        if form_n not in (20, 60, 120):
            form_n = 20

        # ---------- select tickers ----------
        if form_mode == "manual":
            selected_tickers = _parse_tickers(form_tickers)
            if not selected_tickers:
                selected_tickers = DEV_DEFAULT_TICKERS[:]
                run_log_lines.append("[warn] 手動指定が空 → 開発おすすめ10銘柄に戻した")
        elif form_mode == "auto":
            # いまは速度優先で dev_default に落とす（候補JSON連携は次工程）
            selected_tickers = DEV_DEFAULT_TICKERS[:]
            run_log_lines.append("[info] 自動選定は未接続 → いったん開発おすすめ10銘柄で実行")
        else:
            selected_tickers = DEV_DEFAULT_TICKERS[:]

        # 安全装置：画面からの実行は最大10銘柄まで（開発速度最優先）
        if len(selected_tickers) > 10:
            selected_tickers = selected_tickers[:10]
            run_log_lines.append("[info] 銘柄数が多いので10銘柄に制限（開発速度優先）")

        dates = last_n_bdays_jst(form_n)

        # =========================
        # ここが肝：UIもCLIも “同じ1本” を呼ぶ（Judge + AutoFix込み）
        # =========================
        if run_daytrade_backtest_multi_with_judge_autofix is not None and policy:
            try:
                outx = run_daytrade_backtest_multi_with_judge_autofix(
                    n=form_n,
                    tickers=selected_tickers,
                    policy=policy,
                    budget_trade_loss_yen=budget_trade_loss_yen,
                    dates=dates,
                    verbose_log=True,
                    enable_autofix=True,
                    autofix_max_candidates=10,
                )

                base = dict(outx.get("base", {}) or {})
                applied = dict(outx.get("applied", {}) or {})

                # 表示は applied を優先（auto_fix無しなら applied=base 相当）
                rows = list(applied.get("rows", []) or [])
                exit_rows = list(applied.get("exit_rows", []) or [])
                run_log_lines.extend(list(applied.get("run_log_lines", []) or []))

                kpi = dict(applied.get("kpi", {}) or {})
                kpi_total_pnl = int(kpi.get("total_pnl", 0) or 0)
                kpi_trades = int(kpi.get("trades", 0) or 0)
                kpi_winrate = str(kpi.get("winrate", "-") or "-")
                kpi_avg_r = str(kpi.get("avg_r", "-") or "-")
                kpi_max_dd = int(kpi.get("max_dd_yen", 0) or 0)

                base_kpi = dict(base.get("kpi", {}) or {})

                base_judge = _judge_to_dict(outx.get("base_judge"))
                applied_judge = _judge_to_dict(outx.get("applied_judge"))

                fx = outx.get("autofix", None)

                # auto_fix の要約（テンプレで見せる用）
                if fx is not None:
                    try:
                        best = getattr(fx, "best", None)
                        best_policy = getattr(best, "policy", None) or {}
                        fix_summary = {
                            "used": True,
                            "best_name": str(getattr(best, "name", "") or ""),
                            "candidates_count": int(len(list(getattr(fx, "candidates", []) or []))),
                            "diffs": _diff_policy_simple(policy, best_policy),
                        }
                    except Exception:
                        fix_summary = {"used": True, "best_name": "", "candidates_count": 0, "diffs": []}
                else:
                    fix_summary = {"used": False, "best_name": "", "candidates_count": 0, "diffs": []}

            except Exception as e:
                # ここで落ちるなら、安全に通常 backtest へ退避
                run_log_lines.append(f"[warn] judge/autofix service failed → fallback: {e}")
                autofix_enabled = False

        # ---- fallback：古いサービスのみ（最低限動かす） ----
        if not rows:
            out = run_daytrade_backtest_multi(
                n=form_n,
                tickers=selected_tickers,
                policy=policy,
                budget_trade_loss_yen=budget_trade_loss_yen,
                dates=dates,
                verbose_log=True,
            )

            rows = list(out.get("rows", []) or [])
            exit_rows = list(out.get("exit_rows", []) or [])
            run_log_lines.extend(list(out.get("run_log_lines", []) or []))

            kpi = dict(out.get("kpi", {}) or {})
            kpi_total_pnl = int(kpi.get("total_pnl", 0) or 0)
            kpi_trades = int(kpi.get("trades", 0) or 0)
            kpi_winrate = str(kpi.get("winrate", "-") or "-")
            kpi_avg_r = str(kpi.get("avg_r", "-") or "-")
            kpi_max_dd = int(kpi.get("max_dd_yen", 0) or 0)

            base_kpi = None
            base_judge = None
            applied_judge = None
            fix_summary = None

    ctx = {
        # form echo
        "form_n": form_n,
        "form_mode": form_mode,
        "form_tickers": form_tickers,
        "form_top": form_top,
        "form_scan_limit": form_scan_limit,
        "form_pre_rank_pool": form_pre_rank_pool,
        # meta
        "policy_id": policy_id,
        "budget_trade_loss_yen": budget_trade_loss_yen,
        # outputs
        "selected_tickers": selected_tickers,
        "rows": rows,
        "exit_rows": exit_rows,
        "run_log": "\n".join(run_log_lines) if run_log_lines else "（ここにログが出る）",
        # KPIs（applied）
        "kpi_total_pnl": kpi_total_pnl,
        "kpi_trades": kpi_trades,
        "kpi_winrate": kpi_winrate,
        "kpi_avg_r": kpi_avg_r,
        "kpi_max_dd": kpi_max_dd,
        # base KPI（参考）
        "base_kpi": base_kpi,
        # judge / auto_fix
        "judge_enabled": judge_enabled,
        "autofix_enabled": autofix_enabled,
        "base_judge": base_judge,
        "applied_judge": applied_judge,
        "fix_summary": fix_summary,
    }
    return render(request, "aiapp/daytrade_backtest.html", ctx)