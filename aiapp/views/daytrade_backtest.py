# aiapp/views/daytrade_backtest.py
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

try:
    from aiapp.services.daytrade.backtest_multi_service import (
        run_daytrade_backtest_multi_with_judge_autofix,
    )
except Exception:
    run_daytrade_backtest_multi_with_judge_autofix = None  # type: ignore


DEV_DEFAULT_TICKERS = [
    "7203", "6758", "9984", "8306", "8316",
    "8035", "6861", "6501", "9432", "6098",
]


def _parse_tickers(text: str) -> List[str]:
    s = (text or "").replace(",", " ").replace("\n", " ")
    xs = [x.strip() for x in s.split(" ") if x.strip()]
    out: List[str] = []
    for x in xs:
        if x.isdigit() and (4 <= len(x) <= 5):
            out.append(x)
    seen = set()
    uniq: List[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def _judge_to_dict(j) -> Dict[str, Any]:
    if not j:
        return {"decision": "", "reasons": [], "metrics": {}, "mode": ""}
    return {
        "decision": str(getattr(j, "decision", "") or ""),
        "reasons": list(getattr(j, "reasons", []) or []),
        "metrics": dict(getattr(j, "metrics", {}) or {}),
        "mode": str(getattr(j, "mode", "") or ""),
    }


def daytrade_backtest_view(request: HttpRequest) -> HttpResponse:
    # ---------- defaults ----------
    form_n = 20
    form_mode = "dev_default"
    form_tickers = ""
    form_top = 40
    form_scan_limit = 2000
    form_pre_rank_pool = 400

    # ★ stateとして扱う（重要）
    form_judge_mode = "prod"

    run_log_lines: List[str] = []

    selected_tickers: List[str] = []
    rows: List[Dict[str, Any]] = []
    exit_rows: List[Dict[str, Any]] = []

    kpi_total_pnl = 0
    kpi_trades = 0
    kpi_winrate = "-"
    kpi_avg_r = "-"
    kpi_max_dd = 0

    base_kpi: Dict[str, Any] | None = None

    judge_enabled = True
    autofix_enabled = run_daytrade_backtest_multi_with_judge_autofix is not None

    base_judge: Dict[str, Any] | None = None
    applied_judge: Dict[str, Any] | None = None

    fix_summary: Dict[str, Any] | None = None
    fix_candidates: List[Dict[str, Any]] = []
    fix_best_name: str = ""

    # ---------- policy ----------
    try:
        loaded = load_policy_yaml()
        policy = loaded.policy
        policy_id = loaded.policy_id
    except Exception as e:
        policy = {}
        policy_id = ""
        run_log_lines.append(f"[error] policy load failed: {e}")

    capital_cfg = (policy or {}).get("capital", {})
    risk_cfg = (policy or {}).get("risk", {})
    base_capital = int(capital_cfg.get("base_capital", 0) or 0)
    trade_loss_pct = float(risk_cfg.get("trade_loss_pct", 0.0) or 0.0)
    day_loss_pct = float(risk_cfg.get("day_loss_pct", 0.0) or 0.0)
    budget = calc_risk_budget_yen(base_capital, trade_loss_pct, day_loss_pct)
    budget_trade_loss_yen = max(int(getattr(budget, "trade_loss_yen", 1)), 1)

    if request.method == "POST":
        # ---------- form ----------
        form_n = int(request.POST.get("n") or form_n)
        form_mode = str(request.POST.get("mode") or form_mode).strip()
        form_tickers = str(request.POST.get("tickers") or "")

        # ★ここが修正点（最重要）
        jm = request.POST.get("judge_mode")
        if jm is not None:
            jm = str(jm).strip().lower()
            if jm in ("dev", "prod"):
                form_judge_mode = jm
        # POSTに無ければ「前回値を維持」

        if form_n not in (20, 60, 120):
            form_n = 20

        if form_mode == "manual":
            selected_tickers = _parse_tickers(form_tickers) or DEV_DEFAULT_TICKERS[:]
        else:
            selected_tickers = DEV_DEFAULT_TICKERS[:]

        selected_tickers = selected_tickers[:10]
        dates = last_n_bdays_jst(form_n)

        if run_daytrade_backtest_multi_with_judge_autofix and policy:
            outx = run_daytrade_backtest_multi_with_judge_autofix(
                n=form_n,
                tickers=selected_tickers,
                policy=policy,
                budget_trade_loss_yen=budget_trade_loss_yen,
                dates=dates,
                verbose_log=True,
                enable_autofix=True,
                autofix_max_candidates=10,
                judge_mode=form_judge_mode,  # ★正しく維持される
            )

            applied = dict(outx.get("applied", {}) or {})
            rows = list(applied.get("rows", []) or [])
            exit_rows = list(applied.get("exit_rows", []) or [])
            run_log_lines.extend(list(applied.get("run_log_lines", []) or []))

            kpi = dict(applied.get("kpi", {}) or {})
            kpi_total_pnl = int(kpi.get("total_pnl", 0) or 0)
            kpi_trades = int(kpi.get("trades", 0) or 0)
            kpi_winrate = str(kpi.get("winrate", "-") or "-")
            kpi_avg_r = str(kpi.get("avg_r", "-") or "-")
            kpi_max_dd = int(kpi.get("max_dd_yen", 0) or 0)

            base_judge = _judge_to_dict(outx.get("base_judge"))
            applied_judge = _judge_to_dict(outx.get("applied_judge"))

            fx = outx.get("autofix_dict") or {}
            fix_candidates = list(fx.get("candidates", []) or [])
            fix_best_name = str(fx.get("best_name", "") or "")

    ctx = {
        "form_n": form_n,
        "form_mode": form_mode,
        "form_tickers": form_tickers,
        "form_top": form_top,
        "form_scan_limit": form_scan_limit,
        "form_pre_rank_pool": form_pre_rank_pool,
        "form_judge_mode": form_judge_mode,
        "policy_id": policy_id,
        "budget_trade_loss_yen": budget_trade_loss_yen,
        "selected_tickers": selected_tickers,
        "rows": rows,
        "exit_rows": exit_rows,
        "run_log": "\n".join(run_log_lines),
        "kpi_total_pnl": kpi_total_pnl,
        "kpi_trades": kpi_trades,
        "kpi_winrate": kpi_winrate,
        "kpi_avg_r": kpi_avg_r,
        "kpi_max_dd": kpi_max_dd,
        "base_kpi": base_kpi,
        "judge_enabled": judge_enabled,
        "autofix_enabled": autofix_enabled,
        "base_judge": base_judge,
        "applied_judge": applied_judge,
        "fix_summary": fix_summary,
        "fix_candidates": fix_candidates,
        "fix_best_name": fix_best_name,
    }
    return render(request, "aiapp/daytrade_backtest.html", ctx)