# advisor/management/commands/advisor_build_trend.py
from __future__ import annotations
from django.core.management.base import BaseCommand
from django.utils.timezone import now as dj_now
from datetime import timedelta
import math

from advisor.models_trend import TrendResult
from advisor.models import WatchEntry
from portfolio.models import Holding

# ▼価格取得：どれか1つがあればOK（優先順）
#   1) portfolio側に日足テーブルがあればそこから
#   2) advisor側のPriceCacheがあればそこから
#   3) 最後の手段で yfinance（導入済み前提）
try:
    from portfolio.models_prices import DailyPrice  # あれば使う
    HAS_DAILY = True
except Exception:
    HAS_DAILY = False
    DailyPrice = None

try:
    from advisor.models_prices import PriceCache   # あれば使う
    HAS_CACHE = True
except Exception:
    HAS_CACHE = False
    PriceCache = None

class Command(BaseCommand):
    help = "保有＋ウォッチ銘柄のトレンドを日次で生成（線形回帰の傾き→週足向き/信頼度）"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=60)
        parser.add_argument("--extra", nargs="*", default=[], help="追加ティッカー（空白区切り）")
        parser.add_argument("--since", type=str, default=None, help="as_of日（YYYY-MM-DD）。省略で今日。")

    def handle(self, *args, **opts):
        as_of = (dj_now().date() if not opts["since"] else
                 __import__("datetime").date.fromisoformat(opts["since"]))
        days = int(opts["days"])
        tickers = set()

        # 対象抽出：ウォッチ＋保有
        for w in WatchEntry.objects.filter(status=WatchEntry.STATUS_ACTIVE)[:200]:
            tickers.add((w.ticker or "").upper())
        for h in Holding.objects.all()[:500]:
            tickers.add((h.ticker or "").upper())
        for t in opts["extra"]:
            if t: tickers.add(t.upper())

        tickers = sorted([t for t in tickers if t])

        self.stdout.write(f"[trend] as_of={as_of} days={days} targets={len(tickers)}")

        for tkr in tickers:
            px = self._load_prices(tkr, days, as_of)
            if len(px) < max(12, days//3):
                # データが少なすぎる
                TrendResult.objects.update_or_create(
                    ticker=tkr, as_of=as_of,
                    defaults=dict(weekly_trend="flat", slope_annual=None, confidence=0.0,
                                  window_days=days, note="insufficient data"),
                )
                continue

            # 対数価格の線形回帰 y = a + b*x
            ys = [math.log(max(1e-6, p)) for p in px]
            n = len(ys)
            xs = list(range(n))
            mean_x = sum(xs)/n
            mean_y = sum(ys)/n
            sxy = sum((x-mean_x)*(y-mean_y) for x,y in zip(xs,ys))
            sxx = sum((x-mean_x)**2 for x in xs) or 1e-6
            b = sxy / sxx  # 1日あたりのlog傾き
            # 年率換算（営業日 ~ 250）
            slope_annual = b * 250.0

            # 簡易R^2
            sst = sum((y-mean_y)**2 for y in ys) or 1e-6
            r2 = max(0.0, min(1.0, (sxy**2)/(sxx*sst)))
            conf = (0.4 + 0.6*r2) * min(1.0, n/float(days))  # 窓を満たすほど加点

            # しきい値で3分類（必要なら微調整）
            if slope_annual >= 0.15:
                wk = "up"
            elif slope_annual <= -0.15:
                wk = "down"
            else:
                wk = "flat"

            TrendResult.objects.update_or_create(
                ticker=tkr, as_of=as_of,
                defaults=dict(weekly_trend=wk, slope_annual=slope_annual,
                              confidence=conf, window_days=days, note="lr60"),
            )

        self.stdout.write("[trend] done.")

    # ---- 価格のロード（古い→新しいの順で終値配列を返す） ----
    def _load_prices(self, tkr: str, days: int, as_of):
        if HAS_DAILY:
            qs = (DailyPrice.objects
                  .filter(ticker=tkr, date__lte=as_of)
                  .order_by("-date")
                  .values_list("close", flat=True)[:days])
            if qs:
                return list(reversed([float(x) for x in qs if x is not None]))

        if HAS_CACHE:
            # PriceCache 側に履歴を持っているならここで復元（設計に合わせて適宜変更）
            try:
                row = PriceCache.objects.filter(ticker=tkr).order_by("-updated_at").first()
                if row and getattr(row, "history_json", None):
                    hx = [float(v) for v in row.history_json][-days:]
                    return hx
            except Exception:
                pass

        # 最後の手段：yfinance（ネットワークNG環境なら空を返す）
        try:
            import yfinance as yf
            df = yf.download(tkr, period=f"{max(days+10, 70)}d", interval="1d", progress=False)
            closes = [float(x) for x in (df["Close"].dropna().tolist())][-days:]
            return closes
        except Exception:
            return []