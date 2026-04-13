"""
[FILE] autotrade/services/decision/rules.py
[PATH] <project_root>/autotrade/services/decision/rules.py

このファイルは何？
- iPhone 1画面に表示する「今日の運用ルール要約」を作る部品です。
- gate_level（FULL/LIGHT/STOP）によって、運用の強さを変えます。

今回の変更：
- recent diagnosis による「実運用だけの弱気調整」を rules に持たせる
- LONG_WEAK / OVERTRADING / TIME_BIASED などを runtime と notes に反映する
- intraday_trade.py が rules だけ見れば実運用制御できるようにする
"""

from __future__ import annotations

from typing import Dict, Any, Optional, List
from django.conf import settings


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def build_rules_for_today(
    *,
    gate_level: str,
    strategy: Optional[str],
    equity_yen: int,
    diagnosis: Optional[Dict[str, Any]] = None,
    runtime_control: Optional[Dict[str, Any]] = None,
    original_gate_level: Optional[str] = None,
) -> Dict[str, Any]:
    """
    今日のルール要約を作る（DB保存・画面表示用）
    """

    diagnosis = diagnosis if isinstance(diagnosis, dict) else {}
    runtime_control = runtime_control if isinstance(runtime_control, dict) else {}

    base_equity = int(equity_yen or getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000))

    # 既存設定名 / 新設定名どちらでも読めるようにする
    trade_loss_pct = float(
        getattr(
            settings,
            "AUTOTRADE_TRADE_LOSS_PCT",
            getattr(settings, "AUTOTRADE_RISK_TRADE_PCT", 0.0015),
        )
    )
    day_loss_pct = float(
        getattr(
            settings,
            "AUTOTRADE_DAY_LOSS_PCT",
            getattr(settings, "AUTOTRADE_RISK_DAY_PCT", 0.01),
        )
    )
    max_positions = int(
        getattr(
            settings,
            "AUTOTRADE_MAX_POSITIONS",
            getattr(settings, "AUTOTRADE_MAX_POSITIONS_FULL", 2),
        )
    )
    max_trades = int(
        getattr(
            settings,
            "AUTOTRADE_MAX_TRADES_PER_DAY",
            getattr(settings, "AUTOTRADE_MAX_TRADES_FULL", 6),
        )
    )
    session_start = str(getattr(settings, "AUTOTRADE_SESSION_START", "09:00"))
    session_end = str(getattr(settings, "AUTOTRADE_SESSION_END", "14:30"))
    force_close = str(getattr(settings, "AUTOTRADE_FORCE_CLOSE", "15:00"))

    effective_gate_level = str(gate_level or "STOP").upper().strip()
    original_gate = str(original_gate_level or effective_gate_level).upper().strip()

    # gate による基本運用強度
    if effective_gate_level == "FULL":
        lot_multiplier = 1.0
        pos_limit = max_positions
        trade_limit = max_trades
        mode_label = "通常運用"
        gate_note = "バックテストが安定しているので、通常どおり動かします。"
    elif effective_gate_level == "LIGHT":
        lot_multiplier = 0.5
        pos_limit = max(1, int(getattr(settings, "AUTOTRADE_MAX_POSITIONS_LIGHT", 1)))
        trade_limit = max(1, int(getattr(settings, "AUTOTRADE_MAX_TRADES_LIGHT", max(1, max_trades // 2))))
        mode_label = "軽い運用（慎重）"
        gate_note = "中長期の成績に不安があるので、枚数と回数を減らして慎重に動かします。"
    else:  # STOP
        lot_multiplier = 0.0
        pos_limit = 0
        trade_limit = 0
        mode_label = "停止"
        gate_note = "直近の成績が不安定なので、今日は自動売買しません。"

    # --------------------------------------------------------
    # recent diagnosis による runtime 補正
    # --------------------------------------------------------
    runtime_notes: List[str] = []

    max_positions_override = runtime_control.get("max_positions_override")
    max_trades_override = runtime_control.get("max_trades_override")
    max_hold_bars_cap = runtime_control.get("max_hold_bars_cap")

    allow_long = bool(runtime_control.get("allow_long", True))
    allow_short = bool(runtime_control.get("allow_short", True))

    if max_positions_override is not None:
        pos_limit = min(int(pos_limit), max(0, _safe_int(max_positions_override, pos_limit)))

    if max_trades_override is not None:
        trade_limit = min(int(trade_limit), max(0, _safe_int(max_trades_override, trade_limit)))

    if original_gate != effective_gate_level:
        runtime_notes.append(
            f"直近悪化を考慮して、実運用は {original_gate} → {effective_gate_level} に調整します。"
        )

    if not allow_long and allow_short:
        runtime_notes.append("直近ではロング側が弱いため、今日はロング新規を止めます。")
    elif allow_long and not allow_short:
        runtime_notes.append("直近ではショート側が弱いため、今日はショート新規を止めます。")
    elif not allow_long and not allow_short:
        runtime_notes.append("方向優位が見えないため、今日は新規建てを止めます。")

    if max_trades_override is not None:
        runtime_notes.append(f"直近の回しすぎ傾向を考慮して、当日回数上限を {trade_limit} 回に絞ります。")

    if max_hold_bars_cap is not None:
        runtime_notes.append(
            f"時間切れ負けが多いため、最大保有は {int(max_hold_bars_cap)} 本（5分足）までに短縮します。"
        )

    trade_loss_yen = int(round(base_equity * trade_loss_pct))
    day_loss_yen = int(round(base_equity * day_loss_pct))

    notes = [
        gate_note,
        "1回の負け上限に達したら、その取引は必ず終了します。",
        "1日の負け上限に達したら、その日は自動売買を停止します。",
        "15:00 に必ず全決済します（持ち越し禁止）。",
    ]

    # BREAKOUT一本運用
    s = (str(strategy).upper() if strategy is not None else "")
    if s and s != "BREAKOUT":
        notes.append("（互換）strategy が別名で渡されましたが、現在は BREAKOUT 一本運用です。")

    notes.append("今日の戦略は『BREAKOUT（レンジブレイク）』です（勢いに乗る）。")

    diagnosis_tags = [str(x) for x in (diagnosis.get("diagnosis_tags") or []) if str(x).strip()]
    regime_warning = str(diagnosis.get("regime_warning") or "").strip()

    if regime_warning:
        notes.append(f"直近診断：{regime_warning}")

    if diagnosis_tags:
        notes.append("直近タグ：" + " / ".join(diagnosis_tags))

    notes.extend(runtime_notes)

    return {
        "mode": {
            "gate_level": effective_gate_level,
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
            "max_positions": int(pos_limit),
        },
        "limits": {
            "max_trades_per_day": int(trade_limit),
        },
        "time": {
            "session_start": session_start,
            "session_end": session_end,
            "force_close": force_close,
        },
        "runtime": {
            "original_gate_level": original_gate,
            "effective_gate_level": effective_gate_level,
            "regime_warning": regime_warning,
            "diagnosis_tags": diagnosis_tags,
            "allow_long": bool(allow_long),
            "allow_short": bool(allow_short),
            "max_positions_override": (
                None if max_positions_override is None else int(_safe_int(max_positions_override, pos_limit))
            ),
            "max_trades_override": (
                None if max_trades_override is None else int(_safe_int(max_trades_override, trade_limit))
            ),
            "max_hold_bars_cap": (
                None if max_hold_bars_cap is None else int(_safe_int(max_hold_bars_cap, 0))
            ),
        },
        "diagnosis": diagnosis,
        "notes": notes,
    }