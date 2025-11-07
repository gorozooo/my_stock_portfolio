from __future__ import annotations
from django.shortcuts import render
from django.utils.timezone import now
from django.conf import settings
from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_short_aggr
from aiapp.services.reasons import make_reasons
from aiapp.services.sizing import size_aggressive_short

# 安全弁（全銘柄は負荷が高いので、初期は上位Nに制限）
UNIVERSE_LIMIT = int(getattr(settings, "AIAPP_UNIVERSE_LIMIT", 120))
EQUITY_DEFAULT = float(getattr(settings, "AIAPP_EQUITY", 3_000_000.0))  # 口座資産の既定（円）
LOT_DEFAULT = int(getattr(settings, "AIAPP_LOT", 100))
BENCH_CODE = getattr(settings, "AIAPP_BENCH_CODE", "^nikkei")  # stooqの指数記号（取得不可なら無視）

def _fetch_benchmark():
    try:
        return get_prices(BENCH_CODE, lookback_days=220)
    except Exception:
        return None

def _build_item(row, bench_df):
    code = str(row.code)
    name = row.name
    sector = row.sector33

    df = get_prices(code, lookback_days=220)
    if df is None or df.empty:
        return None

    feat = compute_features(df, benchmark_df=bench_df)
    if not feat.get("ok"):
        return None

    # 目安価格（短期×攻め：ATRを利用してEntry/TP/SLを設計）
    close = float(df["Close"].iloc[-1])
    atr = feat.get("atr14") or 10.0  # fallback
    entry = close  # シンプル：成行目安
    sl = max(1.0, entry - 1.0 * atr)
    tp = entry + 2.0 * atr

    score = score_short_aggr(feat, regime=None)
    reasons, concern = make_reasons(feat)

    # 数量計算（2%）
    sizing = size_aggressive_short(entry=entry, tp=tp, sl=sl, equity=EQUITY_DEFAULT, lot=LOT_DEFAULT)

    stars = 1 + int(min(4, max(0, (score - 60) // 8)))  # 60↑で★2〜5の雑スケール
    rcp = 60 + int(min(35, max(0, (score - 60))))       # 仮のRCP近似（M2で学習値に置換）

    item = {
        "name": name,
        "code": code,
        "sector": sector,
        "score": score,
        "stars": stars,
        "rcp": rcp,
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "qty": sizing.shares,
        "funds": sizing.funds,
        "pl_gain": sizing.pl_gain,
        "pl_loss": sizing.pl_loss,
        "reasons": reasons,
        "concern": concern or "—",
    }
    return item

def picks(request):
    mode = request.session.get("aiapp_mode") or "LIVE"
    # ユニバース：まずはマスタの先頭からN件（将来はスクリーナで候補抽出）
    qs = StockMaster.objects.all().order_by("code")[:UNIVERSE_LIMIT]

    bench_df = _fetch_benchmark()

    items = []
    for row in qs:
        it = _build_item(row, bench_df)
        if it:
            items.append(it)

    # スコア上位10件
    items = sorted(items, key=lambda x: x["score"], reverse=True)[:10]

    context = {
        "last_updated": now(),
        "mode": mode,
        "items": items,
    }
    return render(request, "aiapp/picks.html", context)
