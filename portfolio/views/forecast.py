# portfolio/views/forecast.py
from __future__ import annotations
from collections import defaultdict
from datetime import date
from typing import Dict, List, Tuple

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.timezone import now

from ..models import Holding, Dividend
from ..services import dividends as S


def _year_options() -> List[int]:
    y = now().year
    return [y - 2, y - 1, y, y + 1]


def _last_div_by_ticker(user) -> Dict[str, Dividend]:
    """
    ユーザーのティッカー別・最新配当を辞書化
    """
    latest: Dict[str, Dividend] = {}
    qs = (
        Dividend.objects.select_related("holding")
        .filter((S.Q(holding__user=user) | S.Q(holding__isnull=True)))
        .order_by("ticker", "-date", "-id")
    )
    for d in qs:
        key = (d.display_ticker or d.ticker or "").upper()
        if key and key not in latest:
            latest[key] = d
    return latest


def _infer_schedule_months(user, ticker: str) -> List[int]:
    """
    そのティッカーの過去支払月から {1,2,4} 個を推定。
    """
    qs = (
        Dividend.objects.select_related("holding")
        .filter((S.Q(holding__user=user) | S.Q(holding__isnull=True)),
                ticker__iexact=ticker)
        .order_by("-date")
    )[:24]  # 直近24件で十分
    months = []
    for d in qs:
        if d.date:
            m = int(d.date.month)
            if m not in months:
                months.append(m)
    months.sort()
    # 1/2/4 個に丸める
    if len(months) >= 4:
        # できるだけ四半期っぽい 4 ヶ月を選ぶ
        # 例: 3,6,9,12 / 2,5,8,11 / 1,4,7,10 あたり
        for base in (1, 2, 3):
            cand = {base, (base + 3 - 1) % 12 + 1, (base + 6 - 1) % 12 + 1, (base + 9 - 1) % 12 + 1}
            if cand.issubset(set(months)):
                return sorted(list(cand))
        return months[:4]
    if len(months) == 3:
        return months[:2]  # 半期相当へ
    if len(months) == 2:
        return months
    if len(months) == 1:
        return months
    return []


@login_required
def dividends_forecast(request):
    """
    予測画面（テンプレ描画）
    """
    y = int(request.GET.get("year") or now().year)
    ctx = {
        "flt": {"year": y},
        "year_options": _year_options(),
    }
    return render(request, "dividends/forecast.html", ctx)


@login_required
def dividends_forecast_json(request):
    """
    超シンプル予測:
      - 各 Holding について、同ティッカーの直近配当から「1株あたり配当(税引後)」を取得
      - 過去の支払月または freq_hint から「今後12ヶ月の支払月」を推定
      - ＝> 株数 × 1株配当 × 該当月回数 を、その月の見込みに加算
    返却:
      {
        "asof": "YYYY-MM-DD",
        "monthly": [{"ym":"YYYY-MM","net": 12345.67}, ...],  # 今月から 12 ヶ月
      }
    """
    user = request.user
    today = now().date()
    start_y, start_m = today.year, today.month

    # 12 ヶ月のキー
    ym_keys: List[Tuple[int, int]] = []
    y, m = start_y, start_m
    for _ in range(12):
        ym_keys.append((y, m))
        m += 1
        if m > 12:
            y += 1
            m = 1

    # ティッカー別の最新配当
    last_map = _last_div_by_ticker(user)

    # 見込みを集計
    month_sum = defaultdict(float)

    for h in Holding.objects.filter(user=user):
        ticker = (h.ticker or "").upper()
        if not ticker or not h.quantity:
            continue

        last = last_map.get(ticker)
        if not last:
            continue

        # 1株あたり配当（税引後）
        per_share = last.per_share_dividend_net() or 0.0
        if per_share <= 0:
            continue

        # 支払月の推定
        sched = _infer_schedule_months(user, ticker)
        if not sched:
            # freq_hint があれば均等割り（例: 年2 → 直近月と+6ヶ月）
            if last.freq_hint in (1, 2, 4):
                base_m = last.date.month if last.date else start_m
                if last.freq_hint == 1:
                    sched = [base_m]
                elif last.freq_hint == 2:
                    sched = sorted([base_m, ((base_m + 6 - 1) % 12) + 1])
                else:  # 4
                    sched = sorted([base_m,
                                    ((base_m + 3 - 1) % 12) + 1,
                                    ((base_m + 6 - 1) % 12) + 1,
                                    ((base_m + 9 - 1) % 12) + 1])
            else:
                # どうしても分からなければ直近月のみ
                sched = [last.date.month if last.date else start_m]

        # 今後12ヶ月に割り当て
        for (yy, mm) in ym_keys:
            if mm in sched:
                month_sum[(yy, mm)] += float(h.quantity) * float(per_share)

    monthly = [
        {"ym": f"{yy:04d}-{mm:02d}", "net": round(month_sum[(yy, mm)], 2)}
        for (yy, mm) in ym_keys
    ]

    return JsonResponse({"asof": today.strftime("%Y-%m-%d"), "monthly": monthly})