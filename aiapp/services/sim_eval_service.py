# aiapp/services/sim_eval_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from typing import Any, Dict, Optional, Tuple

from decimal import Decimal
import logging

import pandas as pd
import yfinance as yf
from django.utils import timezone

logger = logging.getLogger(__name__)

TZ_JP = timezone.get_default_timezone()


# =========================================================
# 補助データ構造
# =========================================================
@dataclass
class EntryResult:
    has_position: bool          # エントリー成立したか
    entry_px: Optional[float]   # 実際に入った価格（寄り or 指値）
    entry_ts: Optional[datetime]  # 実際に入った日時（JST）


@dataclass
class ExitResult:
    exit_px: float
    exit_ts: datetime
    exit_reason: str  # "hit_tp" / "hit_sl" / "horizon_close" / "no_touch"


# =========================================================
# 5分足ロード
# =========================================================
def _normalize_ohlc_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    yfinance の戻り値を:
        index: DatetimeIndex (JST)
        columns: open/high/low/close
    に揃える。
    """
    if df is None or len(df) == 0:
        raise ValueError("empty dataframe from yfinance")

    # MultiIndex 対策
    if isinstance(df.columns, pd.MultiIndex):
        new_cols = [str(c[0]).lower() for c in df.columns]
        df = df.copy()
        df.columns = new_cols
    else:
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]

    if "open" not in df.columns or "high" not in df.columns or "low" not in df.columns or "close" not in df.columns:
        raise ValueError("5m bars missing required columns 'open', 'high', 'low', 'close'")

    idx = df.index
    if timezone.is_naive(idx[0]):
        idx = idx.tz_localize("UTC")
    idx = idx.tz_convert(TZ_JP)
    df.index = idx

    out = df[["open", "high", "low", "close"]].copy()
    out["ts"] = out.index
    return out


def load_5m_bars(code: str, trade_date: date, horizon_days: int) -> pd.DataFrame:
    """
    code（"9793"）の 5分足を、trade_date から horizon_days 営業日ぶん取得する。
    """
    symbol = f"{code}.T"

    # yfinance は土日なども混ざるので、シンプルに日数範囲で取る
    start_dt = datetime.combine(trade_date, time(0, 0))
    end_dt = start_dt + timedelta(days=horizon_days)
    start_utc = timezone.make_aware(start_dt, TZ_JP).astimezone(timezone.utc)
    end_utc = timezone.make_aware(end_dt, TZ_JP).astimezone(timezone.utc)

    df = yf.download(
        symbol,
        interval="5m",
        start=start_utc,
        end=end_utc,
        progress=False,
        auto_adjust=False,
        actions=False,
    )

    if df is None or len(df) == 0:
        raise ValueError(f"no 5m data downloaded for {code}")

    df_norm = _normalize_ohlc_df(df)

    # ちゃんと trade_date 〜 horizon_days 日目までだけに絞る
    mask = (df_norm["ts"].dt.date >= trade_date) & (
        df_norm["ts"].dt.date < trade_date + timedelta(days=horizon_days)
    )
    df_norm = df_norm.loc[mask].copy()

    if len(df_norm) == 0:
        raise ValueError(f"no 5m data in horizon for {code}")

    return df_norm


# =========================================================
# ロジック本体
# =========================================================
def _parse_trade_date(rec: Dict[str, Any]) -> date:
    s = rec.get("trade_date") or rec.get("run_date")
    if not s:
        # safety: 今日扱い
        return timezone.localdate()
    try:
        return date.fromisoformat(str(s))
    except Exception:
        return timezone.localdate()


def _detect_entry(
    side: str,
    limit_px: float,
    bars: pd.DataFrame,
    trade_date: date,
) -> EntryResult:
    """
    エントリー成立判定
    - 指値 <= 初日寄り → 寄り成行 (open)
    - そうでなければ、5分足の中で high/low が指値を跨いだ最初のバーで成立
    - どこでも触れなければ has_position=False
    """
    if limit_px <= 0:
        return EntryResult(False, None, None)

    # 初日セッションだけを寄り判定に使う（9:00〜15:00）
    day1 = bars[bars["ts"].dt.date == trade_date].copy()
    if len(day1) == 0:
        return EntryResult(False, None, None)

    # 9:00 以降だけ
    day1 = day1[day1["ts"].dt.time >= time(9, 0)]
    if len(day1) == 0:
        return EntryResult(False, None, None)

    first_bar = day1.iloc[0]
    open_px = float(first_bar["open"])

    # ロング想定
    if side.upper() == "BUY":
        # 寄りで指値以上なら寄り成行
        if limit_px <= open_px:
            return EntryResult(True, open_px, first_bar["ts"])

    # 寄りで入らなかった場合 → 全期間のバーで指値レンジを検索
    hit_mask = (bars["low"] <= limit_px) & (bars["high"] >= limit_px)
    if not bool(hit_mask.any()):
        return EntryResult(False, None, None)

    first_hit = bars.loc[hit_mask].iloc[0]
    return EntryResult(True, limit_px, first_hit["ts"])


def _detect_exit(
    side: str,
    entry_px: float,
    bars: pd.DataFrame,
    entry_ts: datetime,
    tp_px: Optional[float],
    sl_px: Optional[float],
    trade_date: date,
    horizon_days: int,
) -> ExitResult:
    """
    TP / SL / タイムアップ判定
    - entry_ts 以降のバーを見て、最初に SL または TP に達したところでクローズ
    - どちらも触れなければ horizon_days 営業日目の終値でクローズ
    """
    # 評価対象期間: entry_ts 以降
    window = bars[bars["ts"] >= entry_ts].copy()
    if len(window) == 0:
        # 安全フォールバック: 最後のバー
        last_bar = bars.iloc[-1]
        return ExitResult(float(last_bar["close"]), last_bar["ts"], "horizon_close")

    # まず TP / SL 判定（時間順）
    side = side.upper()
    for _, row in window.iterrows():
        low = float(row["low"])
        high = float(row["high"])

        if side == "BUY":
            # 先に SL 判定 → そのあと TP 判定（順番は好みだが、ここでは SL 優先）
            if sl_px is not None and low <= sl_px:
                return ExitResult(sl_px, row["ts"], "hit_sl")
            if tp_px is not None and high >= tp_px:
                return ExitResult(tp_px, row["ts"], "hit_tp")
        else:
            # SELL の場合は逆（今回は BUY しか想定していないはずだが念のため）
            if tp_px is not None and low <= tp_px:
                return ExitResult(tp_px, row["ts"], "hit_tp")
            if sl_px is not None and high >= sl_px:
                return ExitResult(sl_px, row["ts"], "hit_sl")

    # どこでも TP/SL に触れなかった → horizon_days 営業日目の終値
    horizon_end = trade_date + timedelta(days=horizon_days - 1)
    horizon_bars = bars[bars["ts"].dt.date <= horizon_end].copy()
    if len(horizon_bars) == 0:
        horizon_bars = bars

    last_bar = horizon_bars.iloc[-1]
    return ExitResult(float(last_bar["close"]), last_bar["ts"], "horizon_close")


# =========================================================
# 公開関数: 1レコード評価
# =========================================================
def eval_sim_record(rec: Dict[str, Any], horizon_days: int = 5) -> Dict[str, Any]:
    """
    1件のシミュレ記録 dict に対して、
    - eval_label_rakuten / eval_pl_rakuten
    - eval_label_matsui / eval_pl_matsui
    - eval_close_px / eval_close_date / eval_horizon_days
    - eval_exit_reason
    - eval_entry_px / eval_entry_ts
    を付与して返す。

    ★重要:
      entry / tp / sl は「AIが出したスナップショット」として一切書き換えない。
      実際に約定した価格・時間は eval_entry_* にのみ保存する。
    """
    # 元の値を絶対に壊さないように退避
    orig_entry = rec.get("entry")
    orig_tp = rec.get("tp")
    orig_sl = rec.get("sl")

    try:
        side = (rec.get("side") or "BUY").upper()
        trade_dt = _parse_trade_date(rec)

        # 数値取り出し（Decimal/str 対応）
        def _to_float(x: Any) -> Optional[float]:
            if x is None:
                return None
            try:
                return float(Decimal(str(x)))
            except Exception:
                return None

        limit_entry = _to_float(rec.get("entry"))
        tp_px = _to_float(rec.get("tp"))
        sl_px = _to_float(rec.get("sl"))

        qty_rakuten = _to_float(rec.get("qty_rakuten")) or 0.0
        qty_matsui = _to_float(rec.get("qty_matsui")) or 0.0

        # どちらの口座も 0株なら、そもそも「見送り」扱い
        if qty_rakuten == 0 and qty_matsui == 0:
            rec["eval_label_rakuten"] = "no_position"
            rec["eval_pl_rakuten"] = 0.0
            rec["eval_label_matsui"] = "no_position"
            rec["eval_pl_matsui"] = 0.0
            rec["eval_close_px"] = None
            rec["eval_close_date"] = None
            rec["eval_horizon_days"] = horizon_days
            rec["eval_exit_reason"] = "no_shares"
            rec["eval_entry_px"] = None
            rec["eval_entry_ts"] = None
            return rec

        # 5分足ロード
        try:
            bars = load_5m_bars(str(rec.get("code") or ""), trade_dt, horizon_days)
        except Exception as e:
            logger.exception("load_5m_bars failed: %s", e)
            # 落とさずに「評価不能」として残す
            rec["eval_label_rakuten"] = None
            rec["eval_pl_rakuten"] = None
            rec["eval_label_matsui"] = None
            rec["eval_pl_matsui"] = None
            rec["eval_close_px"] = None
            rec["eval_close_date"] = None
            rec["eval_horizon_days"] = horizon_days
            rec["eval_exit_reason"] = f"error:{e}"
            rec["eval_entry_px"] = None
            rec["eval_entry_ts"] = None
            return rec

        # エントリー成立判定
        entry_res = _detect_entry(side, limit_entry or 0.0, bars, trade_dt)

        if not entry_res.has_position:
            # 一度も指値に触れなかった → 全口座 no_position/PL=0
            rec["eval_label_rakuten"] = "no_position" if qty_rakuten > 0 else "no_position"
            rec["eval_pl_rakuten"] = 0.0
            rec["eval_label_matsui"] = "no_position" if qty_matsui > 0 else "no_position"
            rec["eval_pl_matsui"] = 0.0

            # 情報としては horizon 終値だけ載せておく
            horizon_end = trade_dt + timedelta(days=horizon_days - 1)
            horizon_bars = bars[bars["ts"].dt.date <= horizon_end].copy()
            if len(horizon_bars) == 0:
                horizon_bars = bars
            last_bar = horizon_bars.iloc[-1]

            rec["eval_close_px"] = float(last_bar["close"])
            rec["eval_close_date"] = last_bar["ts"].date().isoformat()
            rec["eval_horizon_days"] = horizon_days
            rec["eval_exit_reason"] = "no_touch"
            rec["eval_entry_px"] = None
            rec["eval_entry_ts"] = None
            return rec

        # TP/SL/タイムアップ判定
        exit_res = _detect_exit(
            side=side,
            entry_px=entry_res.entry_px,
            bars=bars,
            entry_ts=entry_res.entry_ts,
            tp_px=tp_px,
            sl_px=sl_px,
            trade_date=trade_dt,
            horizon_days=horizon_days,
        )

        # PL 計算
        def _calc_pl(qty: float) -> float:
            if qty == 0:
                return 0.0
            if side == "BUY":
                return float(qty) * (exit_res.exit_px - entry_res.entry_px)
            else:
                return float(qty) * (entry_res.entry_px - exit_res.exit_px)

        pl_rakuten = _calc_pl(qty_rakuten)
        pl_matsui = _calc_pl(qty_matsui)

        def _label_from_pl(qty: float, pl: float) -> str:
            if qty == 0:
                return "no_position"
            if pl > 0:
                return "win"
            if pl < 0:
                return "lose"
            return "flat"

        rec["eval_label_rakuten"] = _label_from_pl(qty_rakuten, pl_rakuten)
        rec["eval_pl_rakuten"] = pl_rakuten
        rec["eval_label_matsui"] = _label_from_pl(qty_matsui, pl_matsui)
        rec["eval_pl_matsui"] = pl_matsui

        rec["eval_close_px"] = exit_res.exit_px
        rec["eval_close_date"] = exit_res.exit_ts.date().isoformat()
        rec["eval_horizon_days"] = horizon_days
        rec["eval_exit_reason"] = exit_res.exit_reason

        rec["eval_entry_px"] = entry_res.entry_px
        rec["eval_entry_ts"] = entry_res.entry_ts.isoformat() if entry_res.entry_ts else None

        return rec

    finally:
        # ★ここが今回一番大事なガード★
        # どんなロジックになっても、entry/tp/sl は必ず元の値を維持する
        if orig_entry is not None:
            rec["entry"] = orig_entry
        if orig_tp is not None:
            rec["tp"] = orig_tp
        if orig_sl is not None:
            rec["sl"] = orig_sl