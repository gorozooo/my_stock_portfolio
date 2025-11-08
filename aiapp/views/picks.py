# aiapp/views/picks.py
from __future__ import annotations
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_batch

# 既定：短期×攻め
DEF_HORIZON = "short"       # short / mid / long
DEF_MODE = "aggressive"     # aggressive / normal / defensive
TOPN = 10

def _build_universe(n_max: int = 400) -> list[StockMaster]:
    # 流動性の高いもの優先（簡易：コード昇順の頭からn_max。必要なら将来は出来高でソート）
    return list(StockMaster.objects.all().order_by("code")[:n_max])

def _make_item(code: str, name: str, feat_df, scored: dict) -> dict:
    last = feat_df.iloc[-1]
    close = float(last.get("close", 0.0))
    atr = float(last.get("atr", 0.0))
    atr_pct = float(last.get("atr_pct", 0.0))
    vwap_dev = float(last.get("vwap_dev_pct", 0.0))
    ret5 = float(last.get("ret_5d_pct", 0.0))

    # 価格帯：Entry/TP/SL（既存ルールを尊重・控えめ）
    entry = round(close * (1 - min(abs(vwap_dev)/300, 0.015)), 1)  # 乖離が大きいほど控えめ
    tp = round(entry * 1.07, 1)                                    # +7% 目安（短期）
    sl = round(entry * (1 - min(max(atr_pct, 1.5)/100*2.0, 0.05)), 1)  # ATR比で可変, 最大-5%

    return {
        "code": code,
        "name": name,
        "close": close,
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "atr": atr,
        "atr_pct": atr_pct,
        "vwap_dev_pct": vwap_dev,
        "ret_5d_pct": ret5,
        # 新スコア
        "score_points": scored["points"],            # 表示: 総合得点 99 点
        "confidence_stars": scored["stars"],         # 表示: ★★★★☆ (4.7)
        "score_raw": scored["raw"],
        "score_meta": scored["extras"],
    }

def picks(request: HttpRequest) -> HttpResponse:
    mode = request.GET.get("mode", "live").lower()
    horizon = request.GET.get("horizon", DEF_HORIZON)
    style = request.GET.get("style", DEF_MODE)  # aggressive/normal/defensive

    # 1) ユニバース収集（軽量版）
    uni = _build_universe(n_max=400)
    # 2) 特徴量（必要分だけ）
    feat_map = {}
    name_map = {}
    for row in uni:
        code = row.code
        try:
            df = get_prices(code, 180)
            if len(df) < 60:
                continue
            feat = compute_features(df)
            if feat is None or len(feat) == 0:
                continue
            feat_map[code] = feat
            name_map[code] = row.name
        except Exception:
            continue

    if not feat_map:
        ctx = {"items": [], "now": timezone.now(), "mode": mode.upper()}
        return render(request, "aiapp/picks.html", ctx)

    # 3) 一括スコア（相対校正＋減点＋⭐️ゲートまで）
    scored_map = score_batch(feat_map, mode=style, horizon=horizon)

    # 4) 並べ替え（高得点→上）
    ranking = sorted(scored_map.items(), key=lambda kv: (kv[1]["points"], kv[1]["stars"]), reverse=True)[:TOPN]

    # 5) 表示アイテム化
    items = []
    for code, scored in ranking:
        name = name_map.get(code, code)
        item = _make_item(code, name, feat_map[code], scored)
        # 33業種の解決（StockMasterのどれかがあれば表示）
        try:
            sm = next(x for x in uni if x.code == code)
            sector = getattr(sm, "sector_name", None) or getattr(sm, "sector33", None) or ""
        except StopIteration:
            sector = ""
        item["sector"] = sector
        items.append(item)

    ctx = {
        "items": items,
        "now": timezone.now(),
        "mode": mode.upper(),
    }
    return render(request, "aiapp/picks.html", ctx)