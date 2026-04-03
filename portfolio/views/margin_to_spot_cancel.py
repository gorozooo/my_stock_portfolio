# [FILE] margin_to_spot_cancel.py
# [PATH] portfolio/views/margin_to_spot_cancel.py
#
# このファイルは何？
# - 現引取消の POST 専用ビュー
# - 現引イベントの取消を service に委譲する
#
# 今回の方針
# - 現引は編集ではなく取消のみ
# - 取消後は元の画面へ戻す

# -*- coding: utf-8 -*-
from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from ..models import TradeEvent
from ..services.trades.margin_to_spot_cancel_service import cancel_margin_to_spot as cancel_margin_to_spot_service


@require_POST
def cancel_margin_to_spot_view(request, event_id: int):
    event = get_object_or_404(
        TradeEvent.objects.select_related("user"),
        pk=event_id,
        user=request.user,
    )

    next_url = (
        request.POST.get("next")
        or request.META.get("HTTP_REFERER")
        or reverse("cash_history")
    ).strip()

    try:
        cancel_margin_to_spot_service(event)
        messages.success(request, f"現引を取り消しました：{event.ticker}")
    except ValidationError as e:
        msg = " / ".join(str(x) for x in e.messages) if getattr(e, "messages", None) else str(e)
        messages.error(request, f"現引取消に失敗しました：{msg}")
    except Exception as e:
        messages.error(request, f"現引取消に失敗しました：{e}")

    return redirect(next_url)