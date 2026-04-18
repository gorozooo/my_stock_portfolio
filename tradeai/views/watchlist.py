# =========================================================
# [FILE] watchlist.py
# [PATH] <project_root>/tradeai/views/watchlist.py
#
# このファイルは何？
# - tradeai のウォッチリスト画面と登録/停止/削除を担当する view です。
#
# 今回の修正：
# - 名前が空のまま保存された場合でも、サーバー側で銘柄名を自動補完する
# - 一覧表示で、日本語名優先 + 33業種セクターを表示できるようにする
# =========================================================

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from tradeai.forms import TradeaiWatchlistForm
from tradeai.models.watchlist import WatchlistItem
from tradeai.services.common.sector33_service import enrich_model_items_with_profile
from tradeai.services.common.stock_lookup import lookup_stock_name_and_sector
from tradeai.services.common.ticker_normalizer import normalize_ticker


@login_required
def watchlist_page(request):
    user = request.user

    if request.method == "POST":
        form = TradeaiWatchlistForm(request.POST)
        if form.is_valid():
            ticker = normalize_ticker(form.cleaned_data["ticker"])
            name = (form.cleaned_data["name"] or "").strip()
            long_enabled = form.cleaned_data["long_enabled"]
            short_enabled = form.cleaned_data["short_enabled"]
            notify_enabled = form.cleaned_data["notify_enabled"]
            priority = form.cleaned_data["priority"]
            memo = (form.cleaned_data["memo"] or "").strip()

            if ticker and not name:
                lookup = lookup_stock_name_and_sector(ticker)
                auto_name = (lookup.get("name") or "").strip()
                if auto_name:
                    name = auto_name

            item, created = WatchlistItem.objects.update_or_create(
                user=user,
                ticker=ticker,
                defaults={
                    "name": name,
                    "market": "JP",
                    "long_enabled": long_enabled,
                    "short_enabled": short_enabled,
                    "notify_enabled": notify_enabled,
                    "is_active": True,
                    "priority": priority,
                    "memo": memo,
                },
            )

            if created:
                messages.success(request, f"{ticker} をウォッチリストに追加しました。")
            else:
                messages.success(request, f"{ticker} のウォッチ設定を更新しました。")

            return redirect("tradeai_watchlist")
    else:
        form = TradeaiWatchlistForm()

    items = list(WatchlistItem.objects.filter(user=user).order_by("priority", "ticker"))
    enrich_model_items_with_profile(items)

    context = {
        "page_title": "ウォッチリスト",
        "form": form,
        "items": items,
    }
    return render(request, "tradeai/watchlist.html", context)


@login_required
def watchlist_toggle_active(request, pk: int):
    if request.method != "POST":
        return redirect("tradeai_watchlist")

    item = get_object_or_404(WatchlistItem, pk=pk, user=request.user)
    item.is_active = not item.is_active
    item.save()

    if item.is_active:
        messages.success(request, f"{item.ticker} の監視を再開しました。")
    else:
        messages.success(request, f"{item.ticker} の監視を停止しました。")

    return redirect("tradeai_watchlist")


@login_required
def watchlist_delete(request, pk: int):
    if request.method != "POST":
        return redirect("tradeai_watchlist")

    item = get_object_or_404(WatchlistItem, pk=pk, user=request.user)
    ticker = item.ticker
    item.delete()
    messages.success(request, f"{ticker} をウォッチリストから削除しました。")
    return redirect("tradeai_watchlist")