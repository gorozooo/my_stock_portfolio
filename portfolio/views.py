# portfolio/views.py
from __future__ import annotations

from django.shortcuts import render
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET

from .services.trend import detect_trend
from .services.metrics import get_metrics

import re, pandas as pd, yfinance as yf

# ========= 共通: ティッカー正規化（日本株 4〜5桁は .T を付与） =========
_JP_ALNUM = re.compile(r"^[0-9A-Z]{4,5}$")

def _normalize_ticker(raw: str) -> str:
    t = (raw or "").strip().upper()
    if not t:
        return t
    if "." in t:
        return t
    if _JP_ALNUM.match(t):
        return f"{t}.T"
    return t


# ========= 画面 =========
def main(request):
    # 必要ならトップページ（ダミー）
    cards = [
        {"name": "トヨタ自動車", "ticker": "7203.T", "trend": "UP", "proba": 62.5},
        {"name": "ソニーグループ", "ticker": "6758.T", "trend": "FLAT", "proba": None},
    ]
    return render(request, "main.html", {"cards": cards})


def trend_page(request):
    # スマホファーストのシンプル画面
    return render(request, "portfolio/trend.html")


def trend_card_partial(request):
    """
    HTMX が差し替えるカード断片
    """
    ticker_raw = (request.GET.get("ticker") or "").strip()
    ticker = _normalize_ticker(ticker_raw)
    ctx = {"error": None, "res": None}

    if not ticker:
        ctx["error"] = "ティッカーを入力してください（例：AAPL, MSFT, 7203 など。日本株は .T 不要）"
        return render(request, "portfolio/_trend_card.html", ctx)

    try:
        ctx["res"] = detect_trend(ticker)
    except Exception as e:
        ctx["error"] = str(e)

    return render(request, "portfolio/_trend_card.html", ctx)


# ========= API =========
@require_GET
def trend_api(request):
    ticker_raw = (request.GET.get("ticker") or "").strip()
    if not ticker_raw:
        return HttpResponseBadRequest("ticker is required")

    ticker = _normalize_ticker(ticker_raw)

    try:
        result = detect_trend(ticker)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

    data = {
        "ok": True,
        "ticker": result.ticker,
        "name": result.name,  # 日本語名（なければ英語/コード）
        "asof": result.asof,
        "days": result.days,
        "signal": result.signal,
        "reason": result.reason,
        "slope": result.slope,
        "slope_annualized_pct": result.slope_annualized_pct,
        "ma_short": result.ma_short,
        "ma_long": result.ma_long,
    }
    return JsonResponse(data)


@require_GET
def ohlc_api(request):
    """
    Chart.js Financial（candlestick）用の OHLC + MA API
    返却形式:
    {
      ok: true,
      ohlc: [{x:"YYYY-MM-DD", o:..., h:..., l:..., c:...}, ...],
      ma10: [null/number,...],   # ohlc と同じ長さ
      ma30: [null/number,...]
    }
    """
    ticker_raw = (request.GET.get("ticker") or "").strip()
    ticker = _normalize_ticker(ticker_raw)
    days = int(request.GET.get("days") or 180)

    if not ticker:
        return JsonResponse({"ok": False, "error": "ticker required"})

    try:
        # ローソクは調整前のOHLCが必要なので auto_adjust=False を明示
        df = yf.download(
            str(ticker),
            period=f"{days}d",
            interval="1d",
            auto_adjust=False,
            progress=False,
        )
        if df is None or df.empty:
            return JsonResponse({"ok": False, "error": "no data"})

        # -------- 列名ゆらぎ & MultiIndex 対応で OHLC を取り出す --------
        def _pick(colname: str) -> pd.Series:
            if isinstance(df.columns, pd.MultiIndex):
                # level 0 に目的の列があれば cross-section で取得
                if colname in df.columns.get_level_values(0):
                    obj = df.xs(colname, axis=1, level=0, drop_level=True)
                    # 複数列（複数ティッカー）の場合は先頭列を採用
                    if isinstance(obj, pd.DataFrame):
                        obj = obj.iloc[:, 0]
                    return pd.to_numeric(obj, errors="coerce")
                # 最後の保険：最終列を採用
                return pd.to_numeric(df.iloc[:, -1], errors="coerce")
            else:
                # 単一列DataFrame
                lower = {c.lower(): c for c in df.columns}
                use = lower.get(colname.lower())
                if not use:
                    raise KeyError(colname)
                return pd.to_numeric(df[use], errors="coerce")

        o = _pick("Open")
        h = _pick("High")
        l = _pick("Low")
        c = _pick("Close")

        # 共通インデックスにアラインし、NaNを落とす
        base = pd.concat([o, h, l, c], axis=1, join="inner").dropna()
        base.columns = ["Open", "High", "Low", "Close"]
        if base.empty:
            return JsonResponse({"ok": False, "error": "no aligned data"})

        # 移動平均（Closeベース）— candlestick と同一長さになるように None 埋め
        ma10_full = base["Close"].rolling(10).mean()
        ma30_full = base["Close"].rolling(30).mean()

        # candlestick 用データ
        ohlc = [
            {
                "x": idx.strftime("%Y-%m-%d"),
                "o": float(row["Open"]),
                "h": float(row["High"]),
                "l": float(row["Low"]),
                "c": float(row["Close"]),
            }
            for idx, row in base.iterrows()
        ]

        payload = {
            "ok": True,
            "ohlc": ohlc,
            "ma10": [float(v) if pd.notna(v) else None for v in ma10_full],
            "ma30": [float(v) if pd.notna(v) else None for v in ma30_full],
        }
        return JsonResponse(payload)

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})
        
@require_GET
def metrics_api(request):
    """プロ向け軽量根拠セット"""
    ticker_raw = (request.GET.get("ticker") or "").strip()
    bench = (request.GET.get("bench") or "^TOPX").strip()
    if not ticker_raw:
        return HttpResponseBadRequest("ticker is required")
    ticker = _normalize_ticker(ticker_raw)
    try:
        metrics = get_metrics(ticker, bench=bench)
        metrics["ticker"] = ticker
        metrics["bench"] = bench
        return JsonResponse(metrics)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
        