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

# portfolio/views.py から ohlc_api を丸ごと差し替え
@require_GET
def ohlc_api(request):
    """
    折れ線チャート/ローソクどちらでも使える OHLC + MA API
    返却:
    {
      ok: true,
      labels: ["YYYY-MM-DD", ...],        # 折れ線用
      close: [number,...],                # 1次元
      ma10:  [number|null,...],           # 1次元
      ma30:  [number|null,...],           # 1次元
      ohlc:  [{x:"YYYY-MM-DD",o:..,h:..,l:..,c:..}, ...]  # ローソク用（おまけ）
    }
    """
    def tolist1d(series: pd.Series) -> list:
        """Series/ndarray を 1 次元 list[float|None] に正規化"""
        arr = np.ravel(series.to_numpy())        # (N,1) -> (N,)
        out = []
        for v in arr:
            if pd.isna(v):
                out.append(None)
            else:
                try:
                    out.append(float(v))
                except Exception:
                    out.append(None)
        return out

    # ---- 入力 ----
    raw = (request.GET.get("ticker") or "").strip()
    if not raw:
        return JsonResponse({"ok": False, "error": "ticker required"})
    ticker = raw if "." in raw else (raw.upper() + ".T" if 4 <= len(raw) <= 5 else raw)
    days = int(request.GET.get("days") or 180)

    try:
        df = yf.download(str(ticker), period=f"{days}d", interval="1d", progress=False)
        if df is None or df.empty:
            return JsonResponse({"ok": False, "error": "no data"})

        # ---- 列名ゆらぎ / MultiIndex 対応で OHLC を取り出す ----
        def pick(df_: pd.DataFrame, name: str) -> pd.Series:
            if isinstance(df_.columns, pd.MultiIndex):
                # level=0 の "Close" などを xs
                if name in df_.columns.get_level_values(0):
                    obj = df_.xs(name, axis=1, level=0, drop_level=True)
                    s = obj.iloc[:, 0] if isinstance(obj, pd.DataFrame) else obj
                    return pd.to_numeric(s, errors="coerce")
                # 最後の列を保険で使う
                return pd.to_numeric(df_.iloc[:, -1], errors="coerce")
            # 通常の単一列
            for c in df_.columns:
                if str(c).lower() == name.lower():
                    return pd.to_numeric(df_[c], errors="coerce")
            raise KeyError(name)

        o = pick(df, "Open")
        h = pick(df, "High")
        l = pick(df, "Low")
        c = pick(df, "Close")

        base = pd.concat([o, h, l, c], axis=1, join="inner").dropna()
        base.columns = ["Open", "High", "Low", "Close"]
        if base.empty:
            return JsonResponse({"ok": False, "error": "no aligned data"})

        # ---- MA 計算（NaN は None にして長さを合わせる）----
        ma10_s = base["Close"].rolling(10).mean()
        ma30_s = base["Close"].rolling(30).mean()

        labels = base.index.strftime("%Y-%m-%d").tolist()
        close  = tolist1d(base["Close"])
        ma10   = tolist1d(ma10_s)
        ma30   = tolist1d(ma30_s)

        # candlestick 用（参考：使わなくても OK）
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

        return JsonResponse({
            "ok": True,
            "labels": labels,
            "close": close,
            "ma10":  ma10,
            "ma30":  ma30,
            "ohlc":  ohlc,
        })
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
        