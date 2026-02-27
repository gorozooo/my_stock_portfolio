"""
[FILE] autotrade/services/backtest/aggregate.py
[PATH] <project_root>/autotrade/services/backtest/aggregate.py

このファイルは何？
- “複数銘柄”のバックテスト結果をまとめて（集計して）返すファイルです。
- morning_prepare.py から呼ばれます。

初心者ポイント：
- エンジン（戦略ごとの計算）と、集計（合算/平均/最大）は分けると肥大化しません。

BREAKOUT一本運用：
- 集計は BREAKOUT のみ
- 互換のため戻り値に "VWAP": {} を残す（古い呼び出し/表示が KeyError で死なない安全弁）
"""

from .engine_breakout import run_breakout


def _agg_results(xs):
    if not xs:
        return {"trades": 0, "pf": 0.0, "max_dd": 1.0, "pnl": 0.0}

    trades = sum(x["trades"] for x in xs)
    pnl = sum(x["pnl"] for x in xs)
    pf = sum(x["pf"] for x in xs) / max(len(xs), 1)   # ざっくり平均
    max_dd = max(x["max_dd"] for x in xs)            # 厳しめに最大
    return {"trades": int(trades), "pf": float(pf), "max_dd": float(max_dd), "pnl": float(pnl)}


def run_backtests_for_universe(picks, windows, rr_breakout: float, rr_vwap=None):
    """
    BREAKOUT一本運用。

    戻り値（互換あり）:
    {
      "BREAKOUT": {"20": {...}, "60": {...}, "120": {...}},
      "VWAP":     {},   # ★互換のため空で残す（参照側を順次撤去するまでの安全弁）
    }
    """
    bt = {"BREAKOUT": {}, "VWAP": {}}

    for w in windows:
        res_b = []
        for t in picks:
            r1 = run_breakout(t, w, rr_breakout)
            if r1:
                res_b.append(r1)

        bt["BREAKOUT"][str(w)] = _agg_results(res_b)

    return bt