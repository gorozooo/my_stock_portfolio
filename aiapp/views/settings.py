# aiapp/views/settings.py
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render, redirect

from portfolio.models import UserSetting  # 既存の UserSetting を利用
from . import settings as _  # noqa: F401, keep
from aiapp.services.broker_summary import compute_broker_summaries


def _get_tab(request: HttpRequest) -> str:
    """
    ?tab=basic / summary / advanced を取得。
    想定外の値が来たときは basic にフォールバック。
    """
    t = (request.GET.get("tab") or request.POST.get("tab") or "basic").lower()
    return "basic" if t not in ("basic", "summary", "advanced") else t


@login_required
@transaction.atomic
def settings_view(request: HttpRequest) -> HttpResponse:
    user = request.user
    tab = _get_tab(request)

    # --- UserSetting を取得/作成 --------------------------------------------
    us, _created = UserSetting.objects.get_or_create(
        user=user,
        defaults={
            "risk_pct": 1.0,
        },
    )

    # 後方互換用: DB 列がまだ無い場合でも一旦デフォルトを使えるようにする
    def get_attr(obj, name: str, default):
        return getattr(obj, name, default)

    rakuten_leverage = get_attr(us, "rakuten_leverage", 2.9)
    rakuten_haircut = get_attr(us, "rakuten_haircut", 0.30)
    matsui_leverage = get_attr(us, "matsui_leverage", 2.8)
    matsui_haircut = get_attr(us, "matsui_haircut", 0.00)

    # ------------------------------------------------------------------ POST
    if request.method == "POST":
        # どのタブからPOSTされたか（hidden で送っている）
        tab = _get_tab(request)

        # リスク％
        try:
            us.risk_pct = float(request.POST.get("risk_pct") or us.risk_pct or 1.0)
        except ValueError:
            # 変な値が来たら前回値を維持
            pass

        # 倍率 / ヘアカット
        def parse_float(name: str, current: float) -> float:
            v = request.POST.get(name)
            if v in (None, ""):
                return current
            try:
                return float(v)
            except ValueError:
                return current

        rakuten_leverage = parse_float("rakuten_leverage", rakuten_leverage)
        rakuten_haircut = parse_float("rakuten_haircut", rakuten_haircut)
        matsui_leverage = parse_float("matsui_leverage", matsui_leverage)
        matsui_haircut = parse_float("matsui_haircut", matsui_haircut)

        # UserSetting に保存（※ モデルに列追加 + migrate しておくこと）
        setattr(us, "rakuten_leverage", rakuten_leverage)
        setattr(us, "rakuten_haircut", rakuten_haircut)
        setattr(us, "matsui_leverage", matsui_leverage)
        setattr(us, "matsui_haircut", matsui_haircut)

        us.save()
        messages.success(request, "保存しました")

        # 同じタブに戻る
        return redirect(f"{request.path}?tab={tab}")

    # ----------------------------------------------------------------- GET
    brokers = compute_broker_summaries(
        user=user,
        risk_pct=float(us.risk_pct or 1.0),
        rakuten_leverage=rakuten_leverage,
        rakuten_haircut=rakuten_haircut,
        matsui_leverage=matsui_leverage,
        matsui_haircut=matsui_haircut,
    )

    ctx = {
        "tab": tab,
        "risk_pct": float(us.risk_pct or 1.0),
        "rakuten_leverage": rakuten_leverage,
        "rakuten_haircut": rakuten_haircut,
        "matsui_leverage": matsui_leverage,
        "matsui_haircut": matsui_haircut,
        "brokers": brokers,
    }
    return render(request, "aiapp/settings.html", ctx)