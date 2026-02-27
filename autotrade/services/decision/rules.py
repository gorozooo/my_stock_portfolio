"""
[FILE] autotrade/services/decision/rules.py
[PATH] <project_root>/autotrade/services/decision/rules.py

このファイルは何？
- iPhone 1画面に表示する「今日の運用ルール要約」を作る部品です。
- gate_level（FULL/LIGHT/STOP）によって、運用の強さを変えます。

BREAKOUT一本運用：
- strategy が "VWAP" で来ても、表示・文言は BREAKOUT に寄せる（互換・一貫性）
"""

from __future__ import annotations

from typing import Dict, Any, Optional
from django.conf import settings


def build_rules_for_today(
    *,
    gate_level: str,
    strategy: Optional[str],
    equity_yen: int,
) -> Dict[str, Any]:
    """
    今日のルール要約を作る（DB保存・画面表示用）
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

    # gate による運用強度（初心者向けに分かりやすく）
    if gate_level == "FULL":
        lot_multiplier = 1.0
        pos_limit = max_positions
        trade_limit = max_trades
        mode_label = "通常運用"
        gate_note = "バックテストが安定しているので、通常どおり動かします。"
    elif gate_level == "LIGHT":
        lot_multiplier = 0.5
        pos_limit = 1
        trade_limit = max(1, max_trades // 2)
        mode_label = "軽い運用（慎重）"
        gate_note = "中長期の成績に不安があるので、枚数と回数を減らして慎重に動かします。"
    else:  # STOP
        lot_multiplier = 0.0
        pos_limit = 0
        trade_limit = 0
        mode_label = "停止"
        gate_note = "直近の成績が不安定なので、今日は自動売買しません。"

    trade_loss_yen = int(round(base_equity * trade_loss_pct))
    day_loss_yen = int(round(base_equity * day_loss_pct))

    notes = [
        gate_note,
        "1回の負け上限に達したら、その取引は必ず終了します。",
        "1日の負け上限に達したら、その日は自動売買を停止します。",
        "15:00 に必ず全決済します（持ち越し禁止）。",
    ]

    # ---------------------------------------------
    # BREAKOUT一本運用：strategy 表示は必ず BREAKOUT へ寄せる
    # ---------------------------------------------
    s = (str(strategy).upper() if strategy is not None else "")
    if s and s != "BREAKOUT":
        # 互換で "VWAP" 等が来ても、運用はBREAKOUT一本という説明に統一
        notes.append("（互換）strategy が別名で渡されましたが、現在は BREAKOUT 一本運用です。")

    notes.append("今日の戦略は『BREAKOUT（レンジブレイク）』です（勢いに乗る）。")

    return {
        "mode": {
            "gate_level": gate_level,
            "label": mode_label,
            "lot_multiplier": lot_multiplier,
        },
        "capital": {
            "equity_yen": base_equity,
        },
        "risk": {
            "trade_loss_pct": trade_loss_pct,
            "trade_loss_yen": trade_loss_yen,
            "day_loss_pct": day_loss_pct,
            "day_loss_yen": day_loss_yen,
            "max_positions": pos_limit,
        },
        "limits": {
            "max_trades_per_day": trade_limit,
        },
        "time": {
            "session_start": session_start,
            "session_end": session_end,
            "force_close": force_close,
        },
        "notes": notes,
    }