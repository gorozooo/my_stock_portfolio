import datetime as dt
from typing import Dict, List
from django.http import JsonResponse
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.utils import timezone
import yfinance as yf

from .models import PortfolioSnapshot, Stock
from .views_main import compute_portfolio_totals
from .views import _safe_float, _safe_int, _get_current_price_cached

@login_required
def pro_panel(request):
    """テンプレに最小データだけ載せてレンダリング。中身は JS が JSON API を叩く設計"""
    return render(request, "pro_panel.html", {})

# ① ベンチ比較 + 最大DD
@login_required
def api_bench_and_dd(request):
    # 期間：過去180日
    today = timezone.localdate()
    start = today - dt.timedelta(days=200)

    # portfolio series（スナップショット無い日は当日値で補完しない＝欠損はスキップ）
    qs = PortfolioSnapshot.objects.filter(user=request.user, date__gte=start, date__lte=today).order_by("date")
    dates, vals = [], []
    for s in qs:
        dates.append(s.date.isoformat())
        vals.append(float(s.total_assets))

    # TWR & DD
    def series_to_metrics(vals: List[float]):
        if not vals: return dict(twr=0.0, maxdd=0.0)
        rets = []
        for i in range(1, len(vals)):
            if vals[i-1] != 0:
                rets.append((vals[i]/vals[i-1]) - 1.0)
        twr = 1.0
        for r in rets: twr *= (1.0 + r)
        twr -= 1.0
        # Max DD
        peak = vals[0]; maxdd = 0.0
        for v in vals:
            if v > peak: peak = v
            dd = (v/peak) - 1.0
            if dd < maxdd: maxdd = dd
        return dict(twr=twr, maxdd=maxdd)

    port = series_to_metrics(vals)

    # ベンチ：NIKKEI / TOPIX
    out_bench = {}
    for name, tkr in getattr(settings, "BENCH_TICKERS", {}).items():
        try:
            hist = yf.Ticker(tkr).history(start=start.isoformat(), end=(today+dt.timedelta(days=1)).isoformat(), interval="1d")["Close"].dropna()
            bvals = [float(x) for x in hist.values.tolist()]
            out_bench[name] = series_to_metrics(bvals)
        except Exception:
            out_bench[name] = dict(twr=None, maxdd=None)

    return JsonResponse({
        "dates": dates,
        "portfolio": port,
        "bench": out_bench,
    })

# ② セクター乖離（現在の保有から）
@login_required
def api_sector_drift(request):
    targets: Dict[str, float] = getattr(settings, "SECTOR_TARGETS", {})
    tgt_sum = sum(targets.values()) or 100.0
    targets = {k: (v/tgt_sum)*100.0 for k,v in targets.items()}

    # 現在のセクター配分
    qs = Stock.objects.all()
    try:
        if "user" in {f.name for f in Stock._meta.get_fields()}:
            qs = qs.filter(user=request.user)
    except Exception:
        pass

    mv_by = {}
    total_mv = 0.0
    for s in qs:
        shares = _safe_int(getattr(s, "shares", 0))
        unit   = _safe_float(getattr(s, "unit_price", 0.0))
        try:
            current = _get_current_price_cached(getattr(s, "ticker",""), fallback=unit)
        except Exception:
            current = unit
        used = current if _safe_float(current)>0 else unit
        mv = float(shares)*float(used)
        sec = (getattr(s, "sector", "") or "その他").strip()
        mv_by[sec] = mv_by.get(sec, 0.0) + mv
        total_mv += mv

    now = {k: (v/total_mv*100.0) if total_mv else 0.0 for k,v in mv_by.items()}

    # 乖離 = now - target（対象セクターが無ければ target=0 とみなす）
    sectors = sorted(set(list(now.keys()) + list(targets.keys())))
    drift = []
    for sec in sectors:
        cur = now.get(sec, 0.0)
        tgt = targets.get(sec, 0.0)
        drift.append({
            "sector": sec,
            "current": cur,
            "target": tgt,
            "diff": cur - tgt,
        })
    # 大きい乖離順
    drift.sort(key=lambda x: abs(x["diff"]), reverse=True)
    return JsonResponse({"items": drift})

# ③ 日次アトリビューション（簡易：セクター別寄与）
@login_required
def api_daily_attribution(request):
    """yfinance で前日終値→今日終値を引き、セクター別に寄与を集計（買い=正、売り=逆）"""
    today = timezone.localdate()
    yday  = today - dt.timedelta(days=1)

    qs = Stock.objects.all()
    try:
        if "user" in {f.name for f in Stock._meta.get_fields()}:
            qs = qs.filter(user=request.user)
    except Exception:
        pass

    contrib = {}  # sector -> JPY contribution
    for s in qs:
        ticker = str(getattr(s, "ticker","") or "")
        shares = _safe_int(getattr(s, "shares", 0))
        unit   = _safe_float(getattr(s, "unit_price", 0.0))
        pos    = (getattr(s, "position","買い") or "買い").strip()
        sec    = (getattr(s, "sector","") or "その他").strip()

        if not ticker or shares == 0:
            continue
        symbol = f"{ticker}.T" if not ticker.endswith(".T") else ticker
        try:
            hist = yf.Ticker(symbol).history(start=yday.isoformat(), end=(today+dt.timedelta(days=1)).isoformat(), interval="1d")["Close"].dropna()
            closes = hist.values.tolist()
            if len(closes) >= 2:
                prev, last = float(closes[-2]), float(closes[-1])
            elif len(closes) == 1:
                prev, last = float(closes[0]), float(closes[0])
            else:
                prev = last = unit
        except Exception:
            prev = last = unit

        diff = (last - prev) * shares
        if pos == "売り":
            diff = -diff  # 空売りは逆方向
        contrib[sec] = contrib.get(sec, 0.0) + diff

    # ソートして上位だけ返す
    items = [{"sector": k, "contribution": v} for k,v in contrib.items()]
    items.sort(key=lambda x: abs(x["contribution"]), reverse=True)
    return JsonResponse({"items": items[:10]})