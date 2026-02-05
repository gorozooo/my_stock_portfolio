"""
[FILE] autotrade/services/live/guards_intraday.py
[PATH] <project_root>/autotrade/services/live/guards_intraday.py

設計D-1〜D-3：場中ガード（LIVE/PAPER共通）

やること：
- 当日損失が閾値に達したら即STOP（新規禁止）
- 連敗が閾値に達したら当日STOP（翌朝リセット）
- 最大取引回数に達したら当日STOP
- 時間ガード（14:30以降は新規禁止 / 15:00は強制クローズ“指示”）

注意：
- ここは「判断」だけ。DB更新は job 側がやる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, time as dt_time

from django.conf import settings
from django.utils import timezone

from autotrade.models_backtest import AutoTradeExecution


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    stop_now: bool
    stop_reason: str
    forbid_new_entries: bool
    force_close_now: bool
    meta: Dict[str, Any]


def _parse_hhmm(x: str, default: str) -> dt_time:
    s = str(x or "").strip() or default
    hh, mm = s.split(":")
    return dt_time(int(hh), int(mm))


def _now_jst() -> datetime:
    return timezone.localtime(timezone.now())


def _calc_consecutive_losses(execs: List[AutoTradeExecution]) -> int:
    """
    exit_at順で見て、末尾から連続で負けている回数を数える。
    pnl_yen が 0 未満を負け扱い。
    """
    streak = 0
    for e in reversed(execs or []):
        try:
            pnl = int(getattr(e, "pnl_yen", 0) or 0)
        except Exception:
            pnl = 0
        if pnl < 0:
            streak += 1
        else:
            break
    return streak


def evaluate_intraday_guards(
    *,
    gate_level: str,
    equity_yen: int,
    execs_today: List[AutoTradeExecution],
    now: Optional[datetime] = None,
) -> GuardResult:
    """
    入力：
      - gate_level: "FULL" / "LIGHT" / "STOP"
      - equity_yen: 総資産（ベース）
      - execs_today: 今日のExecution（PAPER/LIVE想定、BACKTESTは除外して渡す）
      - now: 現在時刻（テスト用）

    出力：
      - 止めるべきか / 新規禁止か / 強制クローズか / 理由
    """
    gate = str(gate_level or "STOP").upper().strip()
    base_equity = int(equity_yen or getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000))

    # 仕様（君の確定版）
    day_loss_pct = float(getattr(settings, "AUTOTRADE_DAY_LOSS_PCT", 0.01))  # 1%
    max_trades_full = int(getattr(settings, "AUTOTRADE_MAX_TRADES_PER_DAY", 6))  # FULL
    max_trades_light = max(1, max_trades_full // 2)  # LIGHTは半分（=3想定）

    # 設計D：連敗ガード（3連敗）
    max_lose_streak = int(getattr(settings, "AUTOTRADE_MAX_LOSE_STREAK", 3))

    # 時間ガード
    session_end = _parse_hhmm(getattr(settings, "AUTOTRADE_SESSION_END", "14:30"), "14:30")
    force_close = _parse_hhmm(getattr(settings, "AUTOTRADE_FORCE_CLOSE", "15:00"), "15:00")

    now = now or _now_jst()
    now_t = now.time()

    # gateがSTOPなら、そもそも新規禁止
    if gate == "STOP":
        return GuardResult(
            ok=True,
            stop_now=True,
            stop_reason="gateがSTOPのため、場中は取引しません。",
            forbid_new_entries=True,
            force_close_now=(now_t >= force_close),
            meta={"gate_level": gate, "now": now.isoformat()},
        )

    # 今日の集計（Executionは“事実”）
    execs = list(execs_today or [])
    trades = len(execs)

    pnl_day = 0
    for e in execs:
        try:
            pnl_day += int(getattr(e, "pnl_yen", 0) or 0)
        except Exception:
            pass

    lose_streak = _calc_consecutive_losses(execs)

    # gate別の最大取引回数
    max_trades = max_trades_full if gate == "FULL" else max_trades_light

    # 時間による新規禁止
    forbid_by_time = (now_t >= session_end)

    # 15:00 強制クローズ（ここでは「指示」を返すだけ）
    force_close_now = (now_t >= force_close)

    # ① 日次損失ガード（最優先）
    day_loss_yen = int(round(base_equity * day_loss_pct))
    if pnl_day <= -day_loss_yen:
        return GuardResult(
            ok=False,
            stop_now=True,
            stop_reason=f"【場中ガード】当日損失が上限に到達（{pnl_day:,}円 <= -{day_loss_yen:,}円）",
            forbid_new_entries=True,
            force_close_now=True,
            meta={
                "rule": "day_loss",
                "pnl_day_yen": pnl_day,
                "day_loss_yen": day_loss_yen,
                "trades": trades,
                "lose_streak": lose_streak,
                "now": now.isoformat(),
            },
        )

    # ② 連敗ガード
    if lose_streak >= max_lose_streak:
        return GuardResult(
            ok=False,
            stop_now=True,
            stop_reason=f"【場中ガード】連敗が上限に到達（{lose_streak}連敗）",
            forbid_new_entries=True,
            force_close_now=True,
            meta={
                "rule": "lose_streak",
                "lose_streak": lose_streak,
                "limit": max_lose_streak,
                "trades": trades,
                "pnl_day_yen": pnl_day,
                "now": now.isoformat(),
            },
        )

    # ③ 取引回数ガード
    if trades >= max_trades:
        return GuardResult(
            ok=False,
            stop_now=True,
            stop_reason=f"【場中ガード】取引回数が上限に到達（{trades}回 >= {max_trades}回）",
            forbid_new_entries=True,
            force_close_now=force_close_now,
            meta={
                "rule": "max_trades",
                "trades": trades,
                "limit": max_trades,
                "pnl_day_yen": pnl_day,
                "now": now.isoformat(),
            },
        )

    # 時間ガード：新規禁止だけ（停止ではない）
    if forbid_by_time:
        return GuardResult(
            ok=True,
            stop_now=False,
            stop_reason="【時間ガード】14:30以降は新規エントリーしません。",
            forbid_new_entries=True,
            force_close_now=force_close_now,
            meta={
                "rule": "time_forbid_entry",
                "trades": trades,
                "pnl_day_yen": pnl_day,
                "lose_streak": lose_streak,
                "now": now.isoformat(),
            },
        )

    # 通常：OK
    return GuardResult(
        ok=True,
        stop_now=False,
        stop_reason="",
        forbid_new_entries=False,
        force_close_now=force_close_now,
        meta={
            "rule": "ok",
            "trades": trades,
            "pnl_day_yen": pnl_day,
            "lose_streak": lose_streak,
            "now": now.isoformat(),
        },
    )