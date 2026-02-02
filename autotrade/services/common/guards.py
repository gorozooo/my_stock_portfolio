"""
[FILE] autotrade/services/common/guards.py
[PATH] <project_root>/autotrade/services/common/guards.py

このファイルは何？
- autotrade の全ジョブ共通で使う「安全装置（ガード）」です。
- emergency_stop=True の日は、戦略決定・銘柄生成・発注など“すべて”を確実に止めるために使います。

初心者ポイント：
- 各ジョブの冒頭に1行入れるだけで止まるので、実装ミスが減ります。
"""

from __future__ import annotations

from datetime import date
from typing import Optional, Tuple

from django.utils import timezone

from autotrade.models import AutoTradeDailyState


def get_today_state() -> AutoTradeDailyState:
    """
    今日の AutoTradeDailyState を必ず返す（無ければ作る）。
    """
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)
    return state


def is_emergency_stopped(state: Optional[AutoTradeDailyState]) -> bool:
    """
    非常停止かどうか（Trueならジョブは即returnすべき）
    """
    if state is None:
        return False
    return bool(getattr(state, "emergency_stop", False))


def emergency_stop_info(state: Optional[AutoTradeDailyState]) -> Tuple[bool, str]:
    """
    UI/ログ用：停止フラグと理由文字列を返す。
    """
    if state is None:
        return False, ""
    if not is_emergency_stopped(state):
        return False, ""

    reason = getattr(state, "emergency_stop_reason", "") or ""
    stopped_at = getattr(state, "emergency_stopped_at", None)

    if stopped_at:
        try:
            jst = timezone.localtime(stopped_at).isoformat()
        except Exception:
            jst = str(stopped_at)
        if reason:
            return True, f"{reason} @ {jst}"
        return True, f"@ {jst}"

    return True, reason or "stopped"