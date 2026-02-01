"""
[FILE] autotrade/services/decision/rules.py
[PATH] <project_root>/autotrade/services/decision/rules.py

このファイルは何？
- iPhone 1画面に表示する「今日の運用ルール要約」を作る部品です。
- jobs や views は、この関数が返した JSON をそのまま保存/表示します。

初心者ポイント：
- ルールは“設定の断片”が増えがちなので、ここで1つにまとめます。
- 画面に出す文言もここで作ると、あとで直すのが楽です。
"""

from __future__ import annotations

from typing import Dict, Any, Optional
from django.conf import settings


def build_rules_for_today(
    *,
    equity_yen: Optional[int] = None,
    strategy: Optional[str] = None,
) -> Dict[str, Any]:
    """
    今日のルール要約を作る（DB保存・画面表示用）

    引数:
      equity_yen: 総資産（省略なら settings の既定値）
      strategy: "BREAKOUT" / "VWAP"（省略なら空）

    返り値（例）:
      {
        "capital": {...},
        "risk": {...},
        "limits": {...},
        "time": {...},
        "notes": [...]
      }
    """

    base_equity = int(equity_yen or getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000))

    # 仕様（あなたが確定したやつ）
    trade_loss_pct = float(getattr(settings, "AUTOTRADE_TRADE_LOSS_PCT", 0.0015))  # 総資産×0.15%
    day_loss_pct = float(getattr(settings, "AUTOTRADE_DAY_LOSS_PCT", 0.01))        # 総資産×1%
    max_positions = int(getattr(settings, "AUTOTRADE_MAX_POSITIONS", 2))           # 同時2
    max_trades = int(getattr(settings, "AUTOTRADE_MAX_TRADES_PER_DAY", 6))         # 1日6
    session_start = str(getattr(settings, "AUTOTRADE_SESSION_START", "09:00"))
    session_end = str(getattr(settings, "AUTOTRADE_SESSION_END", "14:30"))
    force_close = str(getattr(settings, "AUTOTRADE_FORCE_CLOSE", "15:00"))

    trade_loss_yen = int(round(base_equity * trade_loss_pct))
    day_loss_yen = int(round(base_equity * day_loss_pct))

    notes = [
        "1回の負け上限に達したら、その取引は必ず終了します。",
        "1日の負け上限に達したら、その日は自動売買を停止します。",
        "15:00 に必ず全決済します（持ち越し禁止）。",
    ]

    if strategy:
        if strategy == "BREAKOUT":
            notes.append("今日の戦略は『レンジブレイク』です（勢いに乗る）。")
        elif strategy == "VWAP":
            notes.append("今日の戦略は『VWAP押し目』です（行き過ぎの戻り）。")
        else:
            notes.append(f"今日の戦略は『{strategy}』です。")

    return {
        "capital": {
            "equity_yen": base_equity,
        },
        "risk": {
            "trade_loss_pct": trade_loss_pct,
            "trade_loss_yen": trade_loss_yen,
            "day_loss_pct": day_loss_pct,
            "day_loss_yen": day_loss_yen,
            "max_positions": max_positions,
        },
        "limits": {
            "max_trades_per_day": max_trades,
        },
        "time": {
            "session_start": session_start,
            "session_end": session_end,
            "force_close": force_close,
        },
        "notes": notes,
    }