# aiapp/views/daytrade_backtest.py
# -*- coding: utf-8 -*-
from __future__ import annotations

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
    uniq = []
    for c in out:
        if c not in seen:
            uniq.append(c)
            seen.add(c)
    return uniq


def _judge_to_dict(j) -> Dict[str, Any]:
    if not j:
        return {"decision": "", "reasons": [], "metrics": {}, "mode": ""}
    return {
        "decision": j.decision,
        "reasons": list(j.reasons or []),
        "metrics": dict(j.metrics or {}),
        "mode": j.mode,
    }


def daytrade_backtest_view(request: HttpRequest) -> HttpResponse:
    # -------------------------
    # defaults（重要）
    # -------------------------
    form_n = 20
    form_mode = "dev_default"
    form_tickers = ""
    form_judge_mode = "dev"   # ★ デフォルトは dev にする

    run_log_lines: List[str] = []

    selected_tickers: List[str] = []
    rows: List[Dict[str, Any]] = []
    exit_rows: List[Dict[str, Any]] = []

    kpi_total_pnl = 0
    kpi_trades = 0
    kpi_winrate = "-"
    kpi_avg_r = "-"
    kpi_max_dd = 0

    base_judge = None
    applied_judge = None

    fix_summary = None
    fix_candidates: List[Dict[str, Any]] = []
    fix_best_name = ""

    # -------------------------
    # policy load
    # -------------------------
    try:
        loaded = load_policy_yaml()
        policy = loaded.policy
        policy_id = loaded.policy_id
    except Exception as e:
        policy = {}
        policy_id = ""
        run_log_lines.append(f"[error] policy load failed: {e}")

    capital_cfg = policy.get("capital", {})
    risk_cfg = policy.get("risk", {})

    base_capital = int(capital_cfg.get("base_capital", 0) or 0)
    trade_loss_pct = float(risk_cfg.get("trade_loss_pct", 0.0) or 0.0)
    day_loss_pct = float(risk_cfg.get("day_loss_pct", 0.0) or 0.0)

    budget = calc_risk_budget_yen(base_capital, trade_loss_pct, day_loss_pct)
    budget_trade_loss_yen = max(int(budget.trade_loss_yen), 1)

    # -------------------------
    # POST
    # -------------------------
    if request.method == "POST":
        form_n = int(request.POST.get("n") or form_n)
        form_mode = str(request.POST.get("mode") or form_mode)
        form_tickers = str(request.POST.get("tickers") or "")

        # ★ ここが重要：dev / prod は明示指定のみ許可
        raw_mode = request.POST.get("judge_mode")
        if raw_mode in ("dev", "prod"):
            form_judge_mode = raw_mode
        else:
            form_judge_mode = "dev"

        if form_mode == "manual":
            selected_tickers = _parse_tickers(form_tickers)
            if not selected_tickers:
                selected_tickers = DEV_DEFAULT_TICKERS[:]
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
                judge_mode=form_judge_mode,
                enable_autofix=True,
            )

            applied = outx["applied"]
            rows = applied.get("rows", [])
            exit_rows = applied.get("exit_rows", [])

            kpi = applied.get("kpi", {})
            kpi_total_pnl = kpi.get("total_pnl", 0)
            kpi_trades = kpi.get("trades", 0)
            kpi_winrate = kpi.get("winrate", "-")
            kpi_avg_r = kpi.get("avg_r", "-")
            kpi_max_dd = kpi.get("max_dd_yen", 0)

            base_judge = _judge_to_dict(outx.get("base_judge"))
            applied_judge = _judge_to_dict(outx.get("applied_judge"))

            autofix = outx.get("autofix_dict")
            if autofix:
                fix_candidates = autofix.get("candidates", [])
                fix_best_name = autofix.get("best_name", "")
                fix_summary = {
                    "used": True,
                    "candidates_count": autofix.get("candidates_count", 0),
                }

    ctx = {
        "form_n": form_n,
        "form_mode": form_mode,
        "form_tickers": form_tickers,
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
        "base_judge": base_judge,
        "applied_judge": applied_judge,
        "fix_summary": fix_summary,
        "fix_candidates": fix_candidates,
        "fix_best_name": fix_best_name,
    }

    return render(request, "aiapp/daytrade_backtest.html", ctx)