"""
[FILE] autotrade/services/rules_service.py
[PATH] <project_root>/autotrade/services/rules_service.py

このファイルは何？
- 今日のゲート（FULL/LIGHT/STOP）と戦略に応じて、
  “今日の運用ルール要約” を作るサービスです。

初心者ポイント：
- 画面に出す「今日のルール」はここで確定します。
- ルールが散らばると後で必ず迷うので、ここに集約します。
"""

from django.conf import settings


def build_rules_for_today(gate_level: str, strategy: str, equity_yen: int) -> dict:
    rules = {
        "equity_yen": int(equity_yen),
        "risk_trade_pct": float(settings.AUTOTRADE_RISK_TRADE_PCT),
        "risk_day_pct": float(settings.AUTOTRADE_RISK_DAY_PCT),
        "session": {"start": settings.AUTOTRADE_SESSION_START, "end": settings.AUTOTRADE_SESSION_END},
        "force_close": settings.AUTOTRADE_FORCE_CLOSE,
        "strategy": strategy,
        "rr_breakout": float(settings.AUTOTRADE_RR_BREAKOUT),
        "rr_vwap": float(settings.AUTOTRADE_RR_VWAP),
    }

    if gate_level == "FULL":
        rules["max_positions"] = int(settings.AUTOTRADE_MAX_POSITIONS_FULL)
        rules["max_trades"] = int(settings.AUTOTRADE_MAX_TRADES_FULL)
        rules["trade_caps"] = {"BREAKOUT": int(settings.AUTOTRADE_MAX_TRADES_BREAKOUT), "VWAP": int(settings.AUTOTRADE_MAX_TRADES_VWAP)}
        rules["risk_trade_pct_effective"] = float(settings.AUTOTRADE_RISK_TRADE_PCT)
    elif gate_level == "LIGHT":
        rules["max_positions"] = int(settings.AUTOTRADE_MAX_POSITIONS_LIGHT)
        rules["max_trades"] = int(settings.AUTOTRADE_MAX_TRADES_LIGHT)
        rules["trade_caps"] = {"BREAKOUT": 2, "VWAP": 1}
        rules["risk_trade_pct_effective"] = float(settings.AUTOTRADE_RISK_TRADE_PCT) * 0.5
    else:
        rules["max_positions"] = 0
        rules["max_trades"] = 0
        rules["trade_caps"] = {"BREAKOUT": 0, "VWAP": 0}
        rules["risk_trade_pct_effective"] = 0.0

    return rules