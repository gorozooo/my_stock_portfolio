# aiapp/views/settings.py
from __future__ import annotations
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db import transaction
from portfolio.models import UserSetting  # 既存の UserSetting を利用
from . import settings as _  # noqa: keep
from aiapp.services.broker_summary import compute_broker_summaries

def _get_tab(request: HttpRequest) -> str:
    t = (request.GET.get("tab") or request.POST.get("tab") or "basic").lower()
    return "basic" if t not in ("basic", "summary", "advanced") else t

@login_required
@transaction.atomic
def settings_view(request: HttpRequest) -> HttpResponse:
    user = request.user
    tab = _get_tab(request)

    # UserSetting を取得/作成（倍率・ヘアカットを拡張）
    us, _created = UserSetting.objects.get_or_create(user=user, defaults={
        "risk_pct": 1.0,
    })
    # 後方互換：存在しない属性は一時的にデフォルトで埋める（DB列追加後は自然に保存）
    # 既存UserSettingに以下の属性を追加してください（migration）。無い間は getで暫定値。
    def get_attr(obj, name, default):
        return getattr(obj, name, default)

    rakuten_leverage = get_attr(us, "rakuten_leverage", 2.9)
    rakuten_haircut  = get_attr(us, "rakuten_haircut", 0.30)
    matsui_leverage  = get_attr(us, "matsui_leverage", 2.8)
    matsui_haircut   = get_attr(us, "matsui_haircut", 0.00)

    if request.method == "POST":
        action = request.POST.get("action") or "save_basic"
        # 基本設定の保存
        us.risk_pct = float(request.POST.get("risk_pct") or us.risk_pct or 1.0)

        # 倍率/ヘアカット保存（丸タブ「基本設定」にまとめる）
        rakuten_leverage = float(request.POST.get("rakuten_leverage") or rakuten_leverage)
        rakuten_haircut  = float(request.POST.get("rakuten_haircut")  or rakuten_haircut)
        matsui_leverage  = float(request.POST.get("matsui_leverage")  or matsui_leverage)
        matsui_haircut   = float(request.POST.get("matsui_haircut")   or matsui_haircut)

        # UserSetting に属性が無い古いDBでも .save() は動作します（列追加後に反映）
        setattr(us, "rakuten_leverage", rakuten_leverage)
        setattr(us, "rakuten_haircut",  rakuten_haircut)
        setattr(us, "matsui_leverage",  matsui_leverage)
        setattr(us, "matsui_haircut",   matsui_haircut)

        us.save()
        messages.success(request, "保存しました")
        # 現在のタブ維持
        return redirect(f"{request.path}?tab={_get_tab(request)}")

    # 証券会社サマリ（概算）計算
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

        # 倍率/ヘアカット（表示・編集用）
        "rakuten_leverage": rakuten_leverage,
        "rakuten_haircut":  rakuten_haircut,
        "matsui_leverage":  matsui_leverage,
        "matsui_haircut":   matsui_haircut,

        "brokers": brokers,
    }
    return render(request, "aiapp/settings.html", ctx)