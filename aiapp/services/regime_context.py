# -*- coding: utf-8 -*-
"""
aiapp.services.regime_context

MacroRegimeSnapshot から「今日のレジーム」を取り出して、
picks_build / scoring_service / テンプレート などから
使いやすい dict 形式に変換するヘルパ群。

想定ユースケース:
    from aiapp.services.regime_context import build_regime_context

    ctx = build_regime_context()
    print(ctx["summary"])        # "日本株: UP / 米国株: FLAT / ..."
    print(ctx["jp"]["label"])    # "UP"
    print(ctx["regime"])         # MacroRegimeSnapshot インスタンス
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from typing import Any, Dict, Optional

from django.utils import timezone

from ..models.macro import MacroRegimeSnapshot


@dataclass(frozen=True)
class RegimeContext:
    """
    Python 側で扱いやすいようにしたレジーム情報のコンテナ。

    - snapshot: MacroRegimeSnapshot インスタンス（無い場合は None）
    - summary:  テロップ用 1行サマリ（無い場合は ""）
    - jp/us/fx/vol/regime: 各ゾーンごとの score / label
    """

    snapshot: Optional[MacroRegimeSnapshot]
    summary: str

    jp: Dict[str, Any]
    us: Dict[str, Any]
    fx: Dict[str, Any]
    vol: Dict[str, Any]
    regime: Dict[str, Any]


def _find_snapshot(target_date: Optional[_date] = None) -> Optional[MacroRegimeSnapshot]:
    """
    指定日の MacroRegimeSnapshot を返す。
    無ければ「その日以前で一番新しいもの」を 1 件返す。
    それも無ければ None。
    """
    if target_date is None:
        target_date = timezone.localdate()

    qs = MacroRegimeSnapshot.objects
    try:
        return qs.get(date=target_date)
    except MacroRegimeSnapshot.DoesNotExist:
        # その日が無ければ「直近過去」を拾う
        return qs.filter(date__lte=target_date).order_by("-date").first()


def build_regime_context(target_date: Optional[_date] = None) -> RegimeContext:
    """
    picks_build / scoring_service / view / テンプレート から
    共通で使える「レジーム情報コンテキスト」を生成して返す。

    返り値は RegimeContext dataclass。
    """
    snap = _find_snapshot(target_date)

    if snap is None:
        # まだレジームが一度も計算されていないケース用の空コンテキスト
        return RegimeContext(
            snapshot=None,
            summary="",
            jp={"score": None, "label": ""},
            us={"score": None, "label": ""},
            fx={"score": None, "label": ""},
            vol={"level": None, "label": ""},
            regime={"score": None, "label": ""},
        )

    return RegimeContext(
        snapshot=snap,
        summary=snap.summary or "",
        jp={"score": snap.jp_trend_score, "label": snap.jp_trend_label or ""},
        us={"score": snap.us_trend_score, "label": snap.us_trend_label or ""},
        fx={"score": snap.fx_trend_score, "label": snap.fx_trend_label or ""},
        vol={"level": snap.vol_level, "label": snap.vol_label or ""},
        regime={"score": snap.regime_score, "label": snap.regime_label or ""},
    )


def get_regime_summary_text(target_date: Optional[_date] = None) -> str:
    """
    「テロップ用の 1 行サマリ文字列だけ欲しい」とき用の薄いヘルパ。

        from aiapp.services.regime_context import get_regime_summary_text
        text = get_regime_summary_text()
    """
    ctx = build_regime_context(target_date=target_date)
    return ctx.summary or ""