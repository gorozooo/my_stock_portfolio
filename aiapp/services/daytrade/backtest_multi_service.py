# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/backtest_multi_service.py

目的
- CLI(script) と Web(UI) が “同じ処理” を使うための共通サービス。
- これにより「画面とscriptで結果が違う」事故を防ぐ。

追加（重要）
- Judge で NO_GO のときに auto_fix を回して、GO になる案を探す（本番想定）。
- judge_mode="dev"/"prod" を UI から指定できるようにする。
- auto_fix の candidates 一覧を UI に渡せる形式で返す。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

from aiapp.services.daytrade.bars_5m_daytrade import load_daytrade_5m_bars
from aiapp.services.daytrade.bar_adapter_5m import df_to_bars_5m
from aiapp.services.daytrade.backtest_runner import run_backtest_one_day

from aiapp.services.daytrade.judge import JudgeResult, judge_backtest_results

# auto_fix は存在前提（あなたが貼ってくれたやつ）
from aiapp.services.daytrade.auto_fix import AutoFixResult, auto_fix_policy


# =========================
# 表示用（日本語ラベル）
# =========================

EXIT_REASON_LABEL = {
    "time_limit": "時間切れ（時間で終了）",
    "stop_loss": "損切り（ストップ）",
    "take_profit": "利確（利益確定）",
    "force_close_end_of_day": "引け強制決済（終了時刻）",
    "unknown": "不明",
}


# =========================
# 集計データ構造
# =========================

@dataclass
class Agg:
    days: int = 0
    traded_days: int = 0
    total_trades: int = 0
    total_pnl: int = 0
    sum_r: float = 0.0
    wins: int = 0
    losses: int = 0
    max_dd_yen: int = 0  # min(負の値)を保持


def last_n_bdays_jst(n: int, end_d: Optional[date] = None) -> List[date]:
    """過去N営業日（簡易：平日のみ）"""
    if end_d is None:
        end_d = date.today()
    ds = pd.bdate_range(end=end_d, periods=n).to_pydatetime()
    return [d.date() for d in ds]


def fmt_pct(x: float) -> str:
    return f"{x*100:.1f}%"


def safe_float(x, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def safe_int(x, default: int = 0) -> int:
    try:
        if x is None:
            return int(default)
        return int(x)
    except Exception:
        return int(default)


def get_exit_reason(tr) -> str:
    r = getattr(tr, "exit_reason", None)
    if r is None:
        return "unknown"
    s = str(r).strip()
    return s if s else "unknown"


def slice_bars_for_trade(bars, entry_dt: datetime, exit_dt: datetime):
    """entry_dt〜exit_dt の間のバーを抽出"""
    if not bars or entry_dt is None or exit_dt is None:
        return []
    out = []
    for b in bars:
        try:
            if b.dt >= entry_dt and b.dt <= exit_dt:
                out.append(b)
        except Exception:
            continue
    return out


def trade_mfe_mae_yen_long(tr, bars_slice) -> Tuple[int, int]:
    """ロング前提で MFE/MAE を円で計算"""
    entry_price = safe_float(getattr(tr, "entry_price", 0.0))
    qty = safe_int(getattr(tr, "qty", 0))
    if qty <= 0 or entry_price <= 0:
        return (0, 0)

    if not bars_slice:
        exit_price = safe_float(getattr(tr, "exit_price", entry_price))
        pnl = int((exit_price - entry_price) * qty)
        return (max(pnl, 0), min(pnl, 0))

    highs = []
    lows = []
    for b in bars_slice:
        highs.append(safe_float(getattr(b, "high", np.nan), np.nan))
        lows.append(safe_float(getattr(b, "low", np.nan), np.nan))

    highs = [x for x in highs if np.isfinite(x)]
    lows = [x for x in lows if np.isfinite(x)]

    if not highs or not lows:
        exit_price = safe_float(getattr(tr, "exit_price", entry_price))
        pnl = int((exit_price - entry_price) * qty)
        return (max(pnl, 0), min(pnl, 0))

    max_high = float(max(highs))
    min_low = float(min(lows))

    mfe_yen = int((max_high - entry_price) * qty)
    mae_yen = int((min_low - entry_price) * qty)
    return (mfe_yen, mae_yen)


def update_agg(agg: Agg, day_res) -> None:
    agg.days += 1
    agg.total_pnl += int(getattr(day_res, "pnl_yen", 0) or 0)

    day_trades = list(getattr(day_res, "trades", []) or [])
    agg.total_trades += int(len(day_trades))
    if len(day_trades) > 0:
        agg.traded_days += 1

    for tr in day_trades:
        r = safe_float(getattr(tr, "r", 0.0) or 0.0)
        agg.sum_r += r
        if safe_int(getattr(tr, "pnl_yen", 0) or 0) >= 0:
            agg.wins += 1
        else:
            agg.losses += 1

    try:
        agg.max_dd_yen = min(int(agg.max_dd_yen), int(getattr(day_res, "max_drawdown_yen", 0) or 0))
    except Exception:
        pass


def _judge_to_dict(j: Optional[JudgeResult]) -> Dict[str, Any]:
    if not j:
        return {"decision": "", "reasons": [], "metrics": {}, "mode": ""}
    return {
        "decision": str(getattr(j, "decision", "") or ""),
        "reasons": list(getattr(j, "reasons", []) or []),
        "metrics": dict(getattr(j, "metrics", {}) or {}),
        "mode": str(getattr(j, "mode", "") or ""),
    }


def _pick_best_candidate_simple(cands: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    candidates を UI 表示用dictで受け取り、ベストを決める。
    ルール：
      1) GO があれば最初の GO
      2) なければ avg_r が高い方
      3) 同点なら max_dd_pct が小さい方
    """
    if not cands:
        return None
    for c in cands:
        if str(c.get("decision")) == "GO":
            return c

    def keyfn(c: Dict[str, Any]):
        m = dict(c.get("metrics", {}) or {})
        avg_r = safe_float(m.get("avg_r", -999), -999)
        max_dd_pct = safe_float(m.get("max_dd_pct", 9), 9)
        return (avg_r, -max_dd_pct)

    return max(cands, key=keyfn)


def run_daytrade_backtest_multi(
    *,
    n: int,
    tickers: List[str],
    policy: Dict[str, Any],
    budget_trade_loss_yen: int,
    dates: Optional[List[date]] = None,
    verbose_log: bool = True,
) -> Dict[str, Any]:
    """
    共通処理：複数銘柄 × 過去N営業日で backtest を回して集計する。
    """
    if dates is None:
        dates = last_n_bdays_jst(n)

    tickers = [str(x).strip() for x in (tickers or []) if str(x).strip()]
    budget_trade_loss_yen = max(int(budget_trade_loss_yen), 1)

    run_log_lines: List[str] = []
    rows: List[Dict[str, Any]] = []
    exit_rows: List[Dict[str, Any]] = []
    exit_stats: Dict[str, Any] = {}
    collected_day_results: List[Any] = []

    total = Agg(max_dd_yen=0)

    if verbose_log:
        run_log_lines.append("=== daytrade backtest multi (service) ===")
        run_log_lines.append(f"days (bday approx) = {n}")
        run_log_lines.append(f"tickers = {tickers}")
        run_log_lines.append("")

    for t in tickers:
        agg = Agg(max_dd_yen=0)

        for d in dates:
            df = load_daytrade_5m_bars(t, d, force_refresh=False)
            if df is None or df.empty:
                continue

            bars = df_to_bars_5m(df)
            if not bars:
                continue

            res = run_backtest_one_day(bars=bars, policy=policy)
            collected_day_results.append(res)

            update_agg(agg, res)

            for tr in list(getattr(res, "trades", []) or []):
                reason = get_exit_reason(tr)
                pnl = safe_int(getattr(tr, "pnl_yen", 0) or 0)
                r = safe_float(getattr(tr, "r", 0.0) or 0.0)

                entry_dt = getattr(tr, "entry_dt", None)
                exit_dt = getattr(tr, "exit_dt", None)

                held_min = 0.0
                try:
                    if entry_dt is not None and exit_dt is not None:
                        held_min = float((exit_dt - entry_dt).total_seconds() / 60.0)
                except Exception:
                    held_min = 0.0

                bars_slice = []
                try:
                    if entry_dt is not None and exit_dt is not None:
                        bars_slice = slice_bars_for_trade(bars, entry_dt, exit_dt)
                except Exception:
                    bars_slice = []

                mfe_yen, mae_yen = trade_mfe_mae_yen_long(tr, bars_slice)

                denom = float(max(int(budget_trade_loss_yen), 1))
                mfe_r = float(mfe_yen) / denom
                mae_r = float(mae_yen) / denom

                slot = exit_stats.setdefault(
                    reason,
                    {
                        "trades": 0,
                        "wins": 0,
                        "pnl": 0,
                        "sum_r": 0.0,
                        "held_minutes": [],
                        "mfe_r": [],
                        "mae_r": [],
                    },
                )
                slot["trades"] += 1
                slot["pnl"] += int(pnl)
                slot["sum_r"] += float(r)
                if pnl >= 0:
                    slot["wins"] += 1
                slot["held_minutes"].append(float(held_min))
                slot["mfe_r"].append(float(mfe_r))
                slot["mae_r"].append(float(mae_r))

        trades = agg.total_trades
        avg_r = (agg.sum_r / trades) if trades > 0 else 0.0
        winrate = (agg.wins / trades) if trades > 0 else 0.0

        rows.append(
            {
                "ticker": t,
                "used_days": agg.days,
                "traded_days": agg.traded_days,
                "trades": trades,
                "pnl": agg.total_pnl,
                "winrate": fmt_pct(winrate) if trades > 0 else "0.0%",
                "avg_r": f"{avg_r:.4f}",
                "avg_r_num": float(avg_r),
                "max_dd_yen": int(agg.max_dd_yen),
            }
        )

        if verbose_log:
            run_log_lines.append(
                f"[{t}] used_days={agg.days} traded_days={agg.traded_days} trades={trades} pnl={agg.total_pnl} "
                f"winrate={fmt_pct(winrate)} avg_r={avg_r:.4f} max_dd_yen={agg.max_dd_yen}"
            )

        total.days += agg.days
        total.traded_days += agg.traded_days
        total.total_trades += agg.total_trades
        total.total_pnl += agg.total_pnl
        total.sum_r += agg.sum_r
        total.wins += agg.wins
        total.losses += agg.losses
        total.max_dd_yen = min(total.max_dd_yen, agg.max_dd_yen)

    total_trades = total.total_trades
    total_avg_r = (total.sum_r / total_trades) if total_trades > 0 else 0.0
    total_winrate = (total.wins / total_trades) if total_trades > 0 else 0.0

    if verbose_log:
        run_log_lines.append("")
        run_log_lines.append("---- total ----")
        run_log_lines.append(
            f"used_days={total.days} traded_days={total.traded_days} trades={total_trades} pnl={total.total_pnl} "
            f"winrate={fmt_pct(total_winrate)} avg_r={total_avg_r:.4f} max_dd_yen={total.max_dd_yen}"
        )
        run_log_lines.append("=== done ===")

    # exit_rows（取引回数の多い順）
    items: List[Tuple[int, str, Dict[str, Any]]] = []
    for reason, st in exit_stats.items():
        tcnt = safe_int(st.get("trades", 0) or 0)
        if tcnt <= 0:
            continue
        items.append((tcnt, reason, st))
    items.sort(reverse=True, key=lambda x: x[0])

    for tcnt, reason, st in items:
        wins_r = safe_int(st.get("wins", 0) or 0)
        pnl_r = safe_int(st.get("pnl", 0) or 0)
        sum_r_reason = safe_float(st.get("sum_r", 0.0) or 0.0)

        winrate_r = (wins_r / tcnt) if tcnt > 0 else 0.0
        avg_r_reason = (sum_r_reason / tcnt) if tcnt > 0 else 0.0

        held = list(st.get("held_minutes", []) or [])
        mfe_r = list(st.get("mfe_r", []) or [])
        mae_r = list(st.get("mae_r", []) or [])

        avg_held = float(np.mean(held)) if held else 0.0
        avg_mfe_r = float(np.mean(mfe_r)) if mfe_r else 0.0
        avg_mae_r = float(np.mean(mae_r)) if mae_r else 0.0

        exit_rows.append(
            {
                "exit_reason": reason,
                "exit_reason_label": EXIT_REASON_LABEL.get(reason, reason),
                "trades": int(tcnt),
                "wins": int(wins_r),
                "winrate": fmt_pct(float(winrate_r)),
                "pnl": int(pnl_r),
                "avg_r": float(round(avg_r_reason, 4)),
                "avg_r_num": float(avg_r_reason),
                "avg_hold_min": float(round(avg_held, 1)),
                "avg_mfe_r": float(round(avg_mfe_r, 3)),
                "avg_mae_r": float(round(avg_mae_r, 3)),
            }
        )

    return {
        "rows": rows,
        "exit_rows": exit_rows,
        "kpi": {
            "total_pnl": int(total.total_pnl),
            "trades": int(total_trades),
            "winrate": fmt_pct(float(total_winrate)) if total_trades > 0 else "0.0%",
            "avg_r": f"{float(total_avg_r):.4f}",
            "max_dd_yen": int(total.max_dd_yen),
        },
        "run_log_lines": run_log_lines,
        "collected_day_results": collected_day_results,
    }


def run_daytrade_backtest_multi_with_judge_autofix(
    *,
    n: int,
    tickers: List[str],
    policy: Dict[str, Any],
    budget_trade_loss_yen: int,
    dates: Optional[List[date]] = None,
    verbose_log: bool = True,
    enable_autofix: bool = True,
    autofix_max_candidates: int = 10,
    judge_mode: str = "prod",   # ★追加：dev/prod
) -> Dict[str, Any]:
    """
    追加の“本番想定”版：
    1) まず通常 backtest を回す
    2) Judge する（judge_mode=dev/prod）
    3) NO_GO なら auto_fix を挟んで GO 案（または最良案）を探す
    4) 採用案でもう一度 backtest を回して、UI/CLI に分かりやすく返す

    Returns dict:
      - base: run_daytrade_backtest_multi の結果
      - base_judge: JudgeResult
      - applied: 採用後の結果（auto_fix無しなら base と同じ）
      - applied_policy: 採用された policy dict（auto_fix無しなら base policy）
      - applied_judge: 採用後 JudgeResult（auto_fix無しなら base_judge）
      - autofix: AutoFixResult or None（内部表現）
      - autofix_candidates: UI用候補一覧（list[dict]）
      - autofix_best: UI用ベスト候補（dict or None）
    """
    jm = (judge_mode or "prod").strip().lower()
    if jm not in ("dev", "prod"):
        jm = "prod"

    # ---- base run ----
    base = run_daytrade_backtest_multi(
        n=n,
        tickers=tickers,
        policy=policy,
        budget_trade_loss_yen=budget_trade_loss_yen,
        dates=dates,
        verbose_log=verbose_log,
    )

    base_judge = judge_backtest_results(base.get("collected_day_results", []) or [], policy, mode=jm)

    # デフォは “そのまま採用（= base）”
    applied = base
    applied_policy = policy
    applied_judge = base_judge

    autofix: Optional[AutoFixResult] = None
    autofix_candidates: List[Dict[str, Any]] = []
    autofix_best: Optional[Dict[str, Any]] = None

    if (base_judge.decision == "NO_GO") and bool(enable_autofix):

        # day_results_provider: policy -> DayResult配列 を返す
        def _provider(p: Dict[str, Any]) -> List[Any]:
            out = run_daytrade_backtest_multi(
                n=n,
                tickers=tickers,
                policy=p,
                budget_trade_loss_yen=budget_trade_loss_yen,
                dates=dates,
                verbose_log=False,  # autofix内部は静かに
            )
            return list(out.get("collected_day_results", []) or [])

        # ---- auto_fix（内部のjudgeは auto_fix.py 側に依存するが、UI表示はここで judge_mode を適用して作り直す） ----
        autofix = auto_fix_policy(
            base_policy=policy,
            day_results_provider=_provider,
            max_candidates=int(autofix_max_candidates),
        )

        # ---- UI用 candidates を作る（judge_mode で再評価して正規化） ----
        try:
            raw_candidates = list(getattr(autofix, "candidates", []) or [])
        except Exception:
            raw_candidates = []

        for i, c in enumerate(raw_candidates, 1):
            try:
                name = str(getattr(c, "name", "") or "")
                p2 = getattr(c, "policy", None) or {}
            except Exception:
                name = ""
                p2 = {}

            # この候補の backtest 日次結果を再取得し、指定 judge_mode で判定
            dr2 = _provider(p2) if isinstance(p2, dict) and p2 else []
            j2 = judge_backtest_results(dr2, p2 if isinstance(p2, dict) else {}, mode=jm)

            autofix_candidates.append(
                {
                    "index": int(i),
                    "name": name,
                    "policy": p2,
                    "judge": _judge_to_dict(j2),
                    "decision": _judge_to_dict(j2).get("decision", ""),
                    "reasons": _judge_to_dict(j2).get("reasons", []),
                    "metrics": _judge_to_dict(j2).get("metrics", {}),
                    "judge_mode": jm,
                }
            )

        autofix_best = _pick_best_candidate_simple(
            [
                {
                    "index": c.get("index"),
                    "name": c.get("name"),
                    "policy": c.get("policy"),
                    "decision": c.get("decision"),
                    "reasons": c.get("reasons"),
                    "metrics": c.get("metrics"),
                    "judge_mode": c.get("judge_mode"),
                    "judge": c.get("judge"),
                }
                for c in autofix_candidates
            ]
        )

        # ---- 採用案（best）で再集計 ----
        if autofix_best and isinstance(autofix_best.get("policy"), dict):
            applied_policy = dict(autofix_best.get("policy") or {})
            applied_judge = JudgeResult(
                decision=str(autofix_best.get("decision") or ""),
                reasons=list(autofix_best.get("reasons") or []),
                metrics=dict(autofix_best.get("metrics") or {}),
                mode=jm,
            )

            applied = run_daytrade_backtest_multi(
                n=n,
                tickers=tickers,
                policy=applied_policy,
                budget_trade_loss_yen=budget_trade_loss_yen,
                dates=dates,
                verbose_log=verbose_log,
            )

    return {
        "base": base,
        "base_judge": base_judge,
        "base_judge_dict": _judge_to_dict(base_judge),
        "applied": applied,
        "applied_policy": applied_policy,
        "applied_judge": applied_judge,
        "applied_judge_dict": _judge_to_dict(applied_judge),
        "autofix": autofix,
        "autofix_candidates": autofix_candidates,
        "autofix_best": autofix_best,
        "judge_mode": jm,
    }