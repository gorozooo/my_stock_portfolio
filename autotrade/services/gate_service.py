"""
[FILE] autotrade/services/gate_service.py
[PATH] <project_root>/autotrade/services/gate_service.py

このファイルは何？
- 3段階ゲート（FULL / LIGHT / STOP）を判定するサービスです。
- 20/60/120 のバックテスト結果（DD/PF/回数）を見て決めます。

初心者ポイント：
- “今日は動いていい日か？” を決める心臓部分です。
"""

from django.conf import settings


def gate_from_backtests(bt_by_window: dict):
    """
    bt_by_window: {20: {...}, 60: {...}, 120: {...}}
    必要キー：max_dd, pf, trades
    """
    cfg = settings.AUTOTRADE_GATE
    dd_limits = cfg["dd_limits"]
    pf_min = cfg["pf_min"]
    min_trades = cfg["min_trades"]

    def ok(window: int):
        r = bt_by_window.get(window) or {}
        if not r:
            return False, f"{window}日データなし"
        if r.get("max_dd", 1.0) > dd_limits[window]:
            return False, f"{window}日DD超過"
        if r.get("pf", 0.0) < pf_min:
            return False, f"{window}日PF不足"
        if r.get("trades", 0) < min_trades[window]:
            return False, f"{window}日取引回数不足"
        return True, ""

    ok20, r20 = ok(20)
    if not ok20:
        return "STOP", f"20日NG: {r20}"

    ok60, r60 = ok(60)
    if not ok60:
        return "STOP", f"60日NG: {r60}"

    ok120, r120 = ok(120)
    if ok120:
        return "FULL", "20/60/120すべてOK"
    return "LIGHT", f"120日NG: {r120}"