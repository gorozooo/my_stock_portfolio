# advisor/management/commands/advisor_update_indicators.py
from __future__ import annotations

import argparse
import csv
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import transaction

import pandas as pd
import numpy as np
import yfinance as yf

# === あなたの既存モデル（必要に応じて import パスを調整）===
try:
    from advisor.models_trend import TrendResult
except Exception:
    TrendResult = None  # type: ignore

JST = timezone(timedelta(hours=9))


# ---------------------------
# ユニバース（銘柄集合）の選択
# ---------------------------
DEMO10 = [
    "7203.T", "6758.T", "8035.T", "9984.T", "6861.T",
    "4063.T", "2413.T", "6954.T", "8316.T", "4502.T",
]

# ※N225の“最小動作セット”（50本）。本番は --universe file で全件CSVに切替推奨。
N225_SAMPLE50 = [
    "7203.T","6758.T","8035.T","9984.T","6861.T","4063.T","2413.T","6954.T","8316.T","4502.T",
    "9432.T","9433.T","9434.T","9983.T","9101.T","9104.T","9107.T","8058.T","8031.T","8001.T",
    "8002.T","8053.T","8766.T","6752.T","6971.T","6367.T","6594.T","6501.T","6301.T","3382.T",
    "2914.T","4503.T","4519.T","5020.T","7751.T","6902.T","6753.T","6723.T","9020.T","9022.T",
    "9021.T","4061.T","4151.T","7731.T","4901.T","8036.T","6645.T","6098.T","2801.T","4507.T",
]


# 置き換え
def _clean_ticker_str(s: str) -> str:
    """
    - 大文字化・BOM/空白除去
    - 4〜5桁の数字だけなら『.T』を付与（東証現物）
    - 既に .T などがあればそのまま
    """
    t = str(s or "").replace("\ufeff","").strip().upper()
    if not t:
        return t
    # 4-5 桁の純数字だけ
    if t.isdigit() and 4 <= len(t) <= 5:
        return f"{t}.T"
    return t


def load_universe(kind: str, *, user_id: Optional[int], file: Optional[str]) -> List[str]:
    kind = (kind or "demo10").lower()
    if kind == "demo10":
        return DEMO10[:]
    if kind == "n225":
        # 本番はCSVでフル225に差し替え推奨（--universe file --file data/universe/n225.csv）
        return N225_SAMPLE50[:]
    if kind == "watch":
        if user_id is None:
            raise CommandError("--universe watch を使う場合は --user-id が必要です")
        try:
            from advisor.models import WatchEntry
        except Exception:
            raise CommandError("WatchEntry が見つかりません（advisor.models.WatchEntry）")
        # ステータス定数があれば優先し、無ければ 'active'
        status_active = getattr(WatchEntry, "STATUS_ACTIVE", "active")
        qs = WatchEntry.objects.filter(user_id=user_id, status=status_active).values_list("ticker", flat=True)
        tickers = [_clean_ticker_str(t) for t in qs if t and str(t).strip()]
        if not tickers:
            raise CommandError(f"ユーザー{user_id}のアクティブWatchが空です")
        return tickers
    if kind == "file":
        if not file:
            raise CommandError("--universe file では --file <path> が必要です（1行1ティッカー）")
        tickers: List[str] = []
        with open(file, "r", newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if not row:
                    continue
                t = _clean_ticker_str(row[0])
                # 空行と # から始まるコメント行はスキップ
                if not t or t.startswith("#"):
                    continue
                tickers.append(t)
        if not tickers:
            raise CommandError(f"CSVが空です: {file}")
        return tickers
    raise CommandError(f"未知の --universe: {kind}（demo10 / n225 / watch / file を指定）")


# ---------------------------
# 指標計算（自己完結）
# ---------------------------
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def true_range(high, low, close):
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr

def atr(high, low, close, n: int = 14):
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def adx(high, low, close, n: int = 14):
    # +DM/-DM
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=high.index)

    atr_n = atr(high, low, close, n)
    plus_di = 100 * (plus_dm.ewm(alpha=1/n, adjust=False).mean() / atr_n)
    minus_di = 100 * (minus_dm.ewm(alpha=1/n, adjust=False).mean() / atr_n)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_line = dx.ewm(alpha=1/n, adjust=False).mean()
    return adx_line, plus_di, minus_di

def slope_annualized(close: pd.Series, days_window: int = 60) -> float:
    """
    価格の対数に直線当てはめ→日次リターン→年率換算（≈営業日240で近似）
    """
    s = close.dropna().tail(days_window)
    if len(s) < 10:
        return 0.0
    y = np.log(s.values)
    x = np.arange(len(y))
    A = np.vstack([x, np.ones_like(x)]).T
    # 最小二乗
    m, b = np.linalg.lstsq(A, y, rcond=None)[0]
    daily = m  # ログ価格の傾き ≈ 日次リターン
    annual = math.exp(daily * 240) - 1.0
    return float(annual)  # 0.12 = +12%/yr など


@dataclass
class IndicatorResult:
    last_price: Optional[int]
    regime: str  # "trend" | "range" | "mixed"
    adx14: Optional[float]
    ema20_gt_ema50: Optional[bool]
    slope_yr: Optional[float]
    atr14: Optional[float]  
    

def _flatten_yf_columns(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """
    yfinanceの列を安全に単層化する。
    - group_by='column' を使っても環境によってMultiIndexになることがあるため念のため吸収。
    - 期待最終列: ['Open','High','Low','Close','Adj Close','Volume']
    """
    if isinstance(df.columns, pd.MultiIndex):
        # レベルにティッカーが含まれていれば、そのレベルで絞る
        # 例: ('Open','7203.T') のような形 → ティッカーレベルで xs
        # 最後のレベルにティッカーがいる想定で try
        try:
            if ticker in df.columns.get_level_values(-1):
                df = df.xs(ticker, axis=1, level=-1)
            elif ticker in df.columns.get_level_values(0):
                df = df.xs(ticker, axis=1, level=0)
            else:
                # 最後のレベルの値を列名に採用（('Open','7203.T')->'Open'）
                df.columns = [c[0] if isinstance(c, tuple) else str(c) for c in df.columns]
        except Exception:
            # どれも合わなければ先頭要素を採用
            df.columns = [c[0] if isinstance(c, tuple) else str(c) for c in df.columns]
    # 列名の見た目をそろえる
    df.columns = [str(c).strip().title() for c in df.columns]
    return df


def compute_indicators(ticker: str, days: int) -> IndicatorResult:
    end = datetime.now(JST).date() + timedelta(days=1)
    start = end - timedelta(days=max(65, days + 5))
    df = yf.download(
        _clean_ticker_str(ticker),
        start=start.isoformat(),
        end=end.isoformat(),
        progress=False,
        auto_adjust=True,
        group_by="column",
    )
    if df is None or len(df) < 40:
        return IndicatorResult(None, "mixed", None, None, None, None)

    df = _flatten_yf_columns(df, _clean_ticker_str(ticker))
    for col in ["Open", "High", "Low", "Close"]:
        if col not in df.columns:
            return IndicatorResult(None, "mixed", None, None, None, None)

    close = df["Close"]; high = df["High"]; low = df["Low"]

    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    adx14_line, pdi, mdi = adx(high, low, close, 14)
    atr14_line = atr(high, low, close, 14)              # ★ 追加：ATR14系列
    atr_last = float(atr14_line.iloc[-1]) if len(atr14_line) and not pd.isna(atr14_line.iloc[-1]) else None

    adx_last = float(adx14_line.iloc[-1]) if not pd.isna(adx14_line.iloc[-1]) else None
    ema_gt = bool(ema20.iloc[-1] > ema50.iloc[-1]) if not (pd.isna(ema20.iloc[-1]) or pd.isna(ema50.iloc[-1])) else None

    regime = "mixed"
    if adx_last is not None and ema_gt is not None:
        if adx_last >= 25 and ema_gt:
            regime = "trend"
        elif adx_last < 18:
            regime = "range"
        else:
            regime = "mixed"

    slope_yr = slope_annualized(close, days_window=min(60, len(close)))
    last_price = int(round(float(close.iloc[-1]))) if not pd.isna(close.iloc[-1]) else None

    return IndicatorResult(last_price, regime, adx_last, ema_gt, slope_yr, atr_last)  # ★ atr14 を返す


# ---------------------------
# TrendResult への保存（任意）
# ---------------------------
def upsert_trendresult(user_id: int, ticker: str, ind: IndicatorResult, asof: datetime.date):
    if TrendResult is None:
        return
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.filter(id=user_id).first() or User.objects.first()
    if not user:
        return

    name = None
    try:
        info = yf.Ticker(ticker).info
        name = info.get("shortName") or info.get("longName")
    except Exception:
        name = None

    weekly_trend = "up" if (ind.ema20_gt_ema50 is True) else ("down" if (ind.ema20_gt_ema50 is False) else "flat")
    base = 60
    if ind.adx14 is not None:
        base += 10 if ind.adx14 >= 25 else (-5 if ind.adx14 < 18 else 0)
    if ind.ema20_gt_ema50 is True:
        base += 10
    if ind.ema20_gt_ema50 is False:
        base -= 10
    overall = max(0, min(100, int(round(base))))

    # ★ ATR14をnotesに入れる（既存notesへマージ）
    notes = {"regime": ind.regime, "adx14": ind.adx14}
    if ind.atr14 is not None:
        notes["atr14"] = float(ind.atr14)

    TrendResult.objects.update_or_create(
        user=user,
        ticker=_clean_ticker_str(ticker),
        asof=asof,
        defaults=dict(
            name=name,
            close_price=ind.last_price,
            entry_price_hint=ind.last_price,
            weekly_trend=weekly_trend,
            win_prob=None,
            theme_label="trend",
            theme_score=0.55,
            overall_score=overall,
            size_mult=1.0,
            slope_annual=ind.slope_yr,
            confidence=0.55 if ind.adx14 is None else max(0.3, min(0.8, ind.adx14 / 50)),
            notes=notes,  # ★ ここに atr14 を含む
        ),
    )


# ---------------------------
# コマンド本体
# ---------------------------
class Command(BaseCommand):
    help = "銘柄ユニバースに対して yfinance で指標を更新し、必要に応じて TrendResult を保存します。"

    def add_arguments(self, parser: argparse.ArgumentParser):
        parser.add_argument("--days", type=int, default=60, help="計算対象の過去日数（既定60）")
        parser.add_argument("--limit", type=int, default=None, help="最大処理件数の上限")
        parser.add_argument("--sleep", type=float, default=0.0, help="各銘柄の間に入れるスリープ秒")
        # ユニバース指定
        parser.add_argument("--universe", type=str, default="demo10",
                            help="demo10 / n225 / watch / file から選択（既定: demo10）")
        parser.add_argument("--user-id", type=int, default=None, help="--universe watch のときに必要")
        parser.add_argument("--file", type=str, default=None, help="--universe file のときのCSVパス")
        # 保存制御
        parser.add_argument("--no-save", action="store_true", help="DBへ保存せず計算・表示のみ行う")

    def handle(self, *args, **opts):
        days = int(opts["days"])
        limit = opts.get("limit")
        sleep_sec = float(opts.get("sleep") or 0.0)
        universe = str(opts.get("universe") or "demo10")
        user_id = opts.get("user_id")
        file = opts.get("file")
        no_save = bool(opts.get("no_save"))

        tickers = load_universe(universe, user_id=user_id, file=file)
        if limit:
            tickers = tickers[: int(limit)]

        if not tickers:
            raise CommandError("処理対象の銘柄がありません")

        # 代表ユーザーの推定（保存が有効な場合のみ使う）
        User = get_user_model()
        user = User.objects.filter(id=user_id).first() if user_id else User.objects.first()
        user_id_eff = user.id if (user and not no_save) else None

        ok, ng = 0, 0
        asof = datetime.now(JST).date()

        for t in tickers:
            try:
                ind = compute_indicators(t, days=days)
                regime_label = ind.regime
                last_px = ind.last_price if ind.last_price is not None else "-"
                # コンソール出力（従来の雰囲気を維持）
                self.stdout.write(f"✅ {t} {last_px} ({regime_label})")

                if (user_id_eff is not None) and not no_save:
                    with transaction.atomic():
                        upsert_trendresult(user_id_eff, t, ind, asof=asof)

                ok += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"⚠️ {t} failed: {e}"))
                ng += 1
            if sleep_sec > 0:
                time.sleep(sleep_sec)

        self.stdout.write(self.style.SUCCESS(f"\n完了: 成功 {ok} / 失敗 {ng}"))