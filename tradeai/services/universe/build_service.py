# =========================================================
# [FILE] build_service.py
# [PATH] <project_root>/tradeai/services/universe/build_service.py
#
# このファイルは何？
# - ユーザーごとの監視対象ユニバースを構築するサービスです。
# - 保有銘柄 / ウォッチリスト / 日経225 / TOPIX を統合して
#   UniverseTicker へ反映します。
# =========================================================

from __future__ import annotations

from django.conf import settings
from django.db import transaction

from portfolio.models import Holding
from tradeai.models.universe import UniverseTicker
from tradeai.models.watchlist import WatchlistItem
from tradeai.services.common.ticker_normalizer import normalize_ticker
from tradeai.services.universe.source_loader import (
    load_nikkei225_tickers,
    load_topix_tickers,
)


def _best_name(holding_name: str, watch_name: str) -> str:
    if holding_name:
        return holding_name
    if watch_name:
        return watch_name
    return ""


def _priority_for(
    in_holding: bool,
    in_watchlist: bool,
    watch_priority: int | None,
    in_nikkei225: bool,
    in_topix: bool,
) -> int:
    candidates: list[int] = []

    if in_holding:
        candidates.append(10)

    if in_watchlist:
        if watch_priority is not None:
            candidates.append(watch_priority)
        else:
            candidates.append(20)

    if in_nikkei225:
        candidates.append(50)

    if in_topix:
        candidates.append(80)

    if not candidates:
        return 999

    return min(candidates)


@transaction.atomic
def build_universe_for_user(user) -> dict:
    holdings = Holding.objects.filter(user=user, quantity__gt=0)
    watchlist_items = WatchlistItem.objects.filter(user=user, is_active=True)

    nikkei225_tickers = load_nikkei225_tickers(settings.BASE_DIR)
    topix_tickers = load_topix_tickers(settings.BASE_DIR)

    holding_map: dict[str, Holding] = {}
    for holding in holdings:
        ticker = normalize_ticker(holding.ticker)
        if not ticker:
            continue
        holding_map[ticker] = holding

    watchlist_map: dict[str, WatchlistItem] = {}
    for item in watchlist_items:
        ticker = normalize_ticker(item.ticker)
        if not ticker:
            continue
        watchlist_map[ticker] = item

    target_tickers = set()
    target_tickers |= set(holding_map.keys())
    target_tickers |= set(watchlist_map.keys())
    target_tickers |= nikkei225_tickers
    target_tickers |= topix_tickers

    existing_by_ticker = {
        obj.ticker: obj
        for obj in UniverseTicker.objects.filter(user=user)
    }

    created_count = 0
    updated_count = 0
    deactivated_count = 0

    for ticker in sorted(target_tickers):
        holding = holding_map.get(ticker)
        watch_item = watchlist_map.get(ticker)

        in_holding = holding is not None
        in_watchlist = watch_item is not None
        in_nikkei225 = ticker in nikkei225_tickers
        in_topix = ticker in topix_tickers

        name = _best_name(
            holding.name if holding else "",
            watch_item.name if watch_item else "",
        )
        priority = _priority_for(
            in_holding=in_holding,
            in_watchlist=in_watchlist,
            watch_priority=(watch_item.priority if watch_item else None),
            in_nikkei225=in_nikkei225,
            in_topix=in_topix,
        )

        obj = existing_by_ticker.get(ticker)

        if obj is None:
            UniverseTicker.objects.create(
                user=user,
                ticker=ticker,
                name=name,
                market="JP",
                in_nikkei225=in_nikkei225,
                in_topix=in_topix,
                from_watchlist=in_watchlist,
                from_holding=in_holding,
                is_active=True,
                priority=priority,
            )
            created_count += 1
            continue

        changed = False

        if obj.name != name:
            obj.name = name
            changed = True

        if obj.market != "JP":
            obj.market = "JP"
            changed = True

        if obj.in_nikkei225 != in_nikkei225:
            obj.in_nikkei225 = in_nikkei225
            changed = True

        if obj.in_topix != in_topix:
            obj.in_topix = in_topix
            changed = True

        if obj.from_watchlist != in_watchlist:
            obj.from_watchlist = in_watchlist
            changed = True

        if obj.from_holding != in_holding:
            obj.from_holding = in_holding
            changed = True

        if obj.is_active is not True:
            obj.is_active = True
            changed = True

        if obj.priority != priority:
            obj.priority = priority
            changed = True

        if changed:
            obj.save()
            updated_count += 1

    active_target_tickers = set(target_tickers)

    stale_rows = UniverseTicker.objects.filter(user=user, is_active=True).exclude(
        ticker__in=active_target_tickers
    )

    for row in stale_rows:
        row.is_active = False
        row.in_nikkei225 = False
        row.in_topix = False
        row.from_watchlist = False
        row.from_holding = False
        row.priority = 999
        row.save()
        deactivated_count += 1

    return {
        "created_count": created_count,
        "updated_count": updated_count,
        "deactivated_count": deactivated_count,
        "holding_count": len(holding_map),
        "watchlist_count": len(watchlist_map),
        "nikkei225_count": len(nikkei225_tickers),
        "topix_count": len(topix_tickers),
        "total_active_count": UniverseTicker.objects.filter(user=user, is_active=True).count(),
    }