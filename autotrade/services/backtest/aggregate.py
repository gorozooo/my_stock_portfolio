"""
[FILE] autotrade/services/backtest/aggregate.py
[PATH] <project_root>/autotrade/services/backtest/aggregate.py

このファイルは何？
- “複数銘柄”のバックテスト結果をまとめて（集計して）返すファイルです。
- morning_prepare.py から呼ばれます。

初心者ポイント：
- エンジン（戦略ごとの計算）と、集計（合算/平均/最大）は分けると肥大化しません。
"""

from .engine_breakout import run_breakout
from .engine_vwap import run_vwap


def _agg_results(xs):
    if not xs:
        return {"trades": 0, "pf": 0.0, "max_dd": 1.0, "pnl": 0.0}

    trades = sum(x["trades"] for x in xs)
    pnl = sum(x["pnl"] for x in xs)
    pf = sum(x["pf"] for x in xs) / max(len(xs), 1)   # ざっくり平均
    max_dd = max(x["max_dd"] for x in xs)            # 厳しめに最大
    return {"trades": int(trades), "pf": float(pf), "max_dd": float(max_dd), "pnl": float(pnl)}


def run_backtests_for_universe(picks, windows, rr_breakout: float, rr_vwap: float):
    """
    戻り値:
    {
      "BREAKOUT": {"20": {...}, "60": {...}, "120": {...}},
      "VWAP":     {"20": {...}, "60": {...}, "120": {...}},
    }
    """
    bt = {"BREAKOUT": {}, "VWAP": {}}

    for w in windows:
        res_b = []
        res_v = []
        for t in picks:
            r1 = run_breakout(t, w, rr_breakout)
            r2 = run_vwap(t, w, rr_vwap)
            if r1:
                res_b.append(r1)
            if r2:
                res_v.append(r2)

        bt["BREAKOUT"][str(w)] = _agg_results(res_b)
        bt["VWAP"][str(w)] = _agg_results(res_v)

    return bt