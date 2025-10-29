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
    """
    yfinanceの返りがMultiIndex(DataFrame)でも必ず単一Seriesに正規化して
    EMA/RSI/ATR/ADXを計算する、堅牢版。
    """
    if df is None or df.empty:
        return pd.DataFrame()

    # ---- 列取り出しを Series に強制変換するヘルパ ----
    def as_series(frame: pd.DataFrame, col: str) -> pd.Series:
        if col not in frame:
            return pd.Series(dtype=float)
        s = frame[col]
        # yfinanceがMultiIndex列を返すケース対策：最初の列を採用
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        return pd.to_numeric(s, errors="coerce")

    close = as_series(df, "Close")
    high  = as_series(df, "High")
    low   = as_series(df, "Low")

    out = pd.DataFrame(index=df.index.copy())
    out["Close"] = close

    if close.isna().all() or high.isna().all() or low.isna().all():
        return pd.DataFrame()  # 必須データ無し

    # ===== EMA =====
    out["ema20"] = close.ewm(span=20, adjust=False).mean()
    out["ema50"] = close.ewm(span=50, adjust=False).mean()
    out["ema75"] = close.ewm(span=75, adjust=False).mean()

    # ===== RSI(14) =====
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    out["rsi14"] = 100 - (100 / (1 + rs))

    # ===== ATR(14) =====
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    out["atr14"] = tr.rolling(14).mean()

    # ===== ADX(14) =====
    up_move   = high.diff()
    down_move = (-low.diff())
    plus_dm  = up_move.where(up_move > 0, 0.0)
    minus_dm = down_move.where(down_move > 0, 0.0)

    tr14      = tr.rolling(14).sum()
    plus_di   = 100.0 * (plus_dm.rolling(14).sum()  / (tr14 + 1e-9))
    minus_di  = 100.0 * (minus_dm.rolling(14).sum() / (tr14 + 1e-9))
    dx        = ( (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9) ) * 100.0
    out["adx14"] = dx.rolling(14).mean()

    return out


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
                df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
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