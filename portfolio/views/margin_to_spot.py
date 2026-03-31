# [FILE] margin_to_spot.py
# [PATH] portfolio/views/margin_to_spot.py
#
# このファイルは何？
# - 現引専用の表示・送信ビュー
# - 信用保有だけを対象に、現引ページ表示と submit を担当する
#
# 今回の改善
# - 現引ページを開く前の「戻り先」を session に保存
# - 現引完了後は holding_list 固定ではなく、元いた一覧画面へ自然に戻す
# - 成功/失敗メッセージを Django messages で表示
# - 外部URLへは飛ばないように、戻り先は同一ホストの相対URLだけ許可

# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..models import Holding
from ..services.trades.margin_to_spot_service import execute_margin_to_spot


def _session_return_key(pk: int) -> str:
    return f"margin_to_spot_return_to:{pk}"


def _normalize_return_to(request: HttpRequest, raw_url: str | None) -> str | None:
    """
    戻り先URLを安全な相対URLへ正規化する。
    許可:
    - /holdings/?... のような相対URL
    - 同一ホストの絶対URL
    不許可:
    - 外部ホスト
    - //example.com のようなスキーム相対URL
    """
    if not raw_url:
        return None

    raw_url = str(raw_url).strip()
    if not raw_url:
        return None

    if raw_url.startswith("//"):
        return None

    if raw_url.startswith("/"):
        return raw_url

    try:
        p = urlparse(raw_url)
    except Exception:
        return None

    req_host = request.get_host()
    if p.netloc and p.netloc != req_host:
        return None

    path = p.path or "/"
    if not path.startswith("/"):
        path = "/" + path

    normalized = path
    if p.query:
        normalized += f"?{p.query}"
    if p.fragment:
        normalized += f"#{p.fragment}"
    return normalized


def _fallback_return_to() -> str:
    return reverse("holding_list")


def _store_return_to_in_session(request: HttpRequest, pk: int) -> str:
    """
    現引ページへ来る直前の一覧画面を session に保存する。
    referer が無い/不正な場合は holding_list を使う。
    """
    referer = request.META.get("HTTP_REFERER")
    current_path = request.get_full_path()

    normalized = _normalize_return_to(request, referer)

    # 自分自身（現引ページ）を referer として保存しない
    if normalized == current_path:
        normalized = None

    if not normalized:
        normalized = _fallback_return_to()

    request.session[_session_return_key(pk)] = normalized
    request.session.modified = True
    return normalized


def _get_return_to_from_session(request: HttpRequest, pk: int) -> str:
    value = request.session.get(_session_return_key(pk))
    normalized = _normalize_return_to(request, value)
    return normalized or _fallback_return_to()


def _pop_return_to_from_session(request: HttpRequest, pk: int) -> str:
    value = request.session.pop(_session_return_key(pk), None)
    request.session.modified = True
    normalized = _normalize_return_to(request, value)
    return normalized or _fallback_return_to()


@login_required
@require_GET
def margin_to_spot_sheet(request, pk: int):
    """
    現引ページ表示。
    対象は MARGIN + BUY の Holding のみ。
    """
    h = get_object_or_404(Holding, pk=pk, user=request.user)

    if h.account != "MARGIN":
        messages.error(request, "この保有は信用ではないため、現引できません。")
        return redirect(_fallback_return_to())

    if (h.side or "").upper() != "BUY":
        messages.error(request, "今回は信用買い建玉の現引のみ対応です。信用売りは将来『現渡』で対応します。")
        return redirect(_fallback_return_to())

    back_url = _store_return_to_in_session(request, pk)

    ctx = {
        "h": h,
        "today": timezone.localdate().isoformat(),
        "back_url": back_url,
    }
    html = render_to_string("realized/_margin_to_spot_sheet.html", ctx, request=request)
    return HttpResponse(html)


@login_required
@require_POST
@transaction.atomic
def margin_to_spot_submit(request, pk: int):
    """
    現引 submit。
    成功時は、現引ページへ来る前の一覧画面へ自然に戻す。
    """
    h = Holding.objects.select_for_update().filter(pk=pk, user=request.user).first()
    if not h:
        if request.headers.get("HX-Request") == "true":
            return JsonResponse({"ok": False, "error": "対象保有が見つかりません。"}, status=404)
        messages.error(request, "対象保有が見つかりません。")
        return redirect(_fallback_return_to())

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

        messages.error(request, f"現引に失敗しました：{e}")
        # 失敗時は現引ページへ戻す。戻り先sessionは維持する。
        return redirect("holding_margin_to_spot_sheet", pk=pk)

    ticker = getattr(res.trade_event, "ticker", "") or getattr(h, "ticker", "") or ""
    messages.success(
        request,
        f"{ticker} を {qty:,} 株 現引しました。"
    )

    return_to = _pop_return_to_from_session(request, pk)

    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=204)
        response["HX-Redirect"] = return_to
        return response

    return redirect(return_to)