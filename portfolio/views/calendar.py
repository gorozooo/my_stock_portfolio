# portfolio/views/calendar.py
from __future__ import annotations
from datetime import date
from typing import Dict, List

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.timezone import now

from ..services import dividends as S


def _year_options() -> List[int]:
    y = now().year
    # 過去3年〜翌年くらいまで
    return [y - 2, y - 1, y, y + 1]


@login_required
def dividends_calendar(request):
    """
    カレンダー画面（テンプレを描画するだけ。データは .json で取得）
    """
    y = int(request.GET.get("year") or now().year)
    ctx = {
        "flt": {"year": y},
        "year_options": _year_options(),
    }
    return render(request, "dividends/calendar.html", ctx)


@login_required
def dividends_calendar_json(request):
    """
    支払日(date)ベースの日別集計JSON。
    {
      kpi: {...},
      days: {
        "YYYY-MM-DD": {
          "gross": 0.0, "net": 0.0, "tax": 0.0,
          "items": [{id, date, ticker, name, gross, net, tax}]
        },
        ...
      }
    }
    """
    user = request.user
    year  = request.GET.get("year")
    month = request.GET.get("month")
    broker = request.GET.get("broker") or None
    account = request.GET.get("account") or None
    q = request.GET.get("q") or None

    year_i  = int(year) if year else None
    month_i = int(month) if month else None

    qs = S.build_user_dividend_qs(user)
    qs = S.apply_filters(qs, year=year_i, month=month_i, broker=broker, account=account, q=q)
    rows = S.materialize(qs)

    # KPI
    kpi = S.sum_kpis(rows)

    days: Dict[str, Dict] = {}
    for d in rows:
        if not d.date:
            continue
        key = d.date.strftime("%Y-%m-%d")
        rec = days.setdefault(key, {"gross": 0.0, "net": 0.0, "tax": 0.0, "items": []})
        try:
            gross = float(d.gross_amount() or 0)
            net   = float(d.net_amount() or 0)
            tax   = float(d.tax or 0)
        except Exception:
            gross = net = tax = 0.0

        rec["gross"] += gross
        rec["net"]   += net
        rec["tax"]   += tax
        rec["items"].append({
            "id": d.id,
            "date": key,
            "ticker": d.display_ticker or d.ticker,
            "name": d.display_name or d.name,
            "gross": round(gross, 2),
            "net": round(net, 2),
            "tax": round(tax, 2),
        })

    # 並びはキー（=日付文字列）で OK
    return JsonResponse({"kpi": kpi, "days": days})