# [FILE] margin_to_spot.py
# [PATH] portfolio/views/margin_to_spot.py
#
# このファイルは何？
# - 現引専用の表示・送信ビュー
# - 信用保有だけを対象に、現引シート表示と submit を担当する
#
# 今回の方針
# - 一覧テンプレはまだ触らず、まずは専用URLで安全に動かす
# - HTMX でも通常POSTでも動くようにする

# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..models import Holding
from ..services.trades.margin_to_spot_service import execute_margin_to_spot


@login_required
@require_GET
def margin_to_spot_sheet(request, pk: int):
    """
    現引シート表示。
    対象は MARGIN + BUY の Holding のみ。
    """
    h = get_object_or_404(Holding, pk=pk, user=request.user)

    if h.account != "MARGIN":
        return HttpResponse(
            "<div style='padding:16px;color:#fca5a5'>この保有は信用ではないため、現引できません。</div>"
        )

    if (h.side or "").upper() != "BUY":
        return HttpResponse(
            "<div style='padding:16px;color:#fca5a5'>今回は信用買い建玉の現引のみ対応です。信用売りは将来『現渡』で対応します。</div>"
        )

    ctx = {
        "h": h,
        "today": timezone.localdate().isoformat(),
    }
    html = render_to_string("realized/_margin_to_spot_sheet.html", ctx, request=request)
    return HttpResponse(html)


@login_required
@require_POST
@transaction.atomic
def margin_to_spot_submit(request, pk: int):
    """
    現引 submit。
    """
    h = Holding.objects.select_for_update().filter(pk=pk, user=request.user).first()
    if not h:
        return JsonResponse({"ok": False, "error": "対象保有が見つかりません。"}, status=404)

    try:
        date_raw = (request.POST.get("date") or "").strip()
        if date_raw:
            trade_at = datetime.fromisoformat(date_raw).date()
        else:
            trade_at = timezone.localdate()
    except Exception:
        trade_at = timezone.localdate()

    try:
        qty = int(request.POST.get("qty") or 0)
    except Exception:
        qty = 0

    memo = (request.POST.get("memo") or "").strip()

    try:
        res = execute_margin_to_spot(
            source_holding=h,
            trade_at=trade_at,
            qty=qty,
            memo=memo,
        )
    except Exception as e:
        if request.headers.get("HX-Request") == "true":
            return JsonResponse({"ok": False, "error": str(e)}, status=400)
        return HttpResponse(f"<div style='padding:16px;color:#fca5a5'>{e}</div>", status=400)

    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=204)
        response["HX-Redirect"] = reverse("holding_list")
        return response

    return redirect("holding_list")