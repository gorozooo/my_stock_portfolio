from __future__ import annotations
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.db import transaction

from advisor.models_indicator import IndicatorSnapshot


# ====== テクニカル指標の計算関数 ======
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """EMA, RSI, ADX, ATRなどを計算して返す"""
    if df.empty or "Close" not in df:
        return pd.DataFrame()

    df["ema20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["ema75"] = df["Close"].ewm(span=75, adjust=False).mean()

    # RSI14
    delta = df["Close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    df["rsi14"] = 100 - (100 / (1 + rs))

    # TR, ATR14
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()

    # ADX14
    plus_dm = df["High"].diff()
    minus_dm = df["Low"].diff().abs()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0

    tr14 = tr.rolling(14).sum()
    plus_di = 100 * (plus_dm.rolling(14).sum() / tr14)
    minus_di = 100 * (minus_dm.rolling(14).sum() / tr14)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)) * 100
    df["adx14"] = dx.rolling(14).mean()

    return df


# ====== メインコマンド ======
class Command(BaseCommand):
    help = "全銘柄のテクニカル指標を取得して IndicatorSnapshot に保存します"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=60, help="取得日数（過去◯日）")
        parser.add_argument("--limit", type=int, default=100, help="最大銘柄数（テスト用）")

    def handle(self, *args, **options):
        days = options["days"]
        limit = options["limit"]

        # 本番ではここでTSE銘柄一覧をロード（仮で主要銘柄）
        tickers = [
            "7203.T", "6758.T", "8035.T", "9984.T", "6861.T",
            "4063.T", "2413.T", "6954.T", "8316.T", "4502.T"
        ][:limit]

        start = datetime.now() - timedelta(days=days)
        end = datetime.now()
        success, fail = 0, 0

        for ticker in tickers:
            try:
                df = yf.download(ticker, start=start, end=end, progress=False)
                df = compute_indicators(df)
                if df.empty:
                    self.stdout.write(self.style.WARNING(f"{ticker} → データなし"))
                    fail += 1
                    continue

                last = df.iloc[-1]
                regime = IndicatorSnapshot.infer_regime(
                    ema20=last.get("ema20"), ema50=last.get("ema50"), adx14=last.get("adx14")
                )

                with transaction.atomic():
                    IndicatorSnapshot.objects.update_or_create(
                        ticker=ticker,
                        asof=df.index[-1].date(),
                        defaults={
                            "close": float(last["Close"]),
                            "ema20": float(last["ema20"]),
                            "ema50": float(last["ema50"]),
                            "ema75": float(last["ema75"]),
                            "rsi14": float(last["rsi14"]),
                            "adx14": float(last["adx14"]),
                            "atr14": float(last["atr14"]),
                            "regime_hint": regime,
                        },
                    )
                self.stdout.write(self.style.SUCCESS(f"✅ {ticker} {int(last['Close'])} ({regime})"))
                success += 1
            except Exception as e:
                fail += 1
                self.stdout.write(self.style.ERROR(f"⚠️ {ticker} failed: {e}"))

        self.stdout.write(self.style.SUCCESS(f"\n完了: 成功 {success} / 失敗 {fail}"))