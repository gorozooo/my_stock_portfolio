# aiapp/services/sim_eval_service.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, datetime as _dt, time as _time, timedelta
from typing import Any, Dict, Optional, Tuple

import math
import pandas as pd
from django.utils import timezone

from aiapp.services import bars_5m as svc_bars_5m


Number = float | int


@dataclass
class SimEval:
    # 共通
    entry_ts: Optional[_dt]
    entry_px: Optional[Number]
    exit_ts: Optional[_dt]
    exit_px: Optional[Number]
    exit_reason: Optional[str]  # "hit_tp" / "hit_sl" / "horizon_close" / "skip"

    # 楽天
    label_rakuten: Optional[str]
    pl_rakuten: Optional[Number]

    # 松井
    label_matsui: Optional[str]
    pl_matsui: Optional[Number]

    # 表示用
    close_date: Optional[str]
    horizon_days: int


# ----------------------------------------------------------------------
# ユーティリティ
# ----------------------------------------------------------------------
def _parse_iso_dt(s: Optional[str]) -> Optional[_dt]:
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = _dt.fromisoformat(s)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


def _parse_date_any(s: Optional[str]) -> Optional[_date]:
    if not isinstance(s, str) or not s:
        return None
    try:
        # "YYYY-MM-DD" 想定
        return _dt.fromisoformat(s).date()
    except Exception:
        # "YYYY-MM-DD" 以外の文字列も一応トライ
        try:
            return _dt.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None


def _ensure_jst(dt: _dt) -> _dt:
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_default_timezone())
    return timezone.localtime(dt)


def _load_5m_bars(code: str, trade_date: _date, horizon_days: int = 5) -> pd.DataFrame:
    """
    5分足を bars_5m キャッシュサービス経由で取得して正規化する。

    - trade_date を起点に horizon_days 営業日ぶん（程度）を取得
    - 'ts' 列（JST aware datetime）を必ず持つ DataFrame に統一
    - Open / High / Low / Close 列名も大文字に正規化
    """
    raw = svc_bars_5m.load_5m_bars(code, trade_date, horizon_days=horizon_days)

    if raw is None:
        return pd.DataFrame()

    # bars_5m が (df, meta) を返す可能性も考慮
    if isinstance(raw, tuple):
        df = raw[0]
    else:
        df = raw

    if df is None or len(df) == 0:
        return pd.DataFrame()

    df = df.copy()

    # カラム名の小文字対応
    cols_lower = {str(c).lower(): c for c in df.columns}
    for want in ("open", "high", "low", "close"):
        if want not in cols_lower:
            raise ValueError(f"5m bars is missing column '{want}' for code={code}")
    col_open = cols_lower["open"]
    col_high = cols_lower["high"]
    col_low = cols_lower["low"]
    col_close = cols_lower["close"]

    # ts 列
    if "ts" in df.columns:
        ts = pd.to_datetime(df["ts"])
    else:
        ts = pd.to_datetime(df.index)

    if ts.dt.tz is None:
        ts = ts.dt.tz_localize(timezone.get_default_timezone())
    else:
        ts = ts.dt.tz_convert(timezone.get_default_timezone())

    df = pd.DataFrame(
        {
            "ts": ts,
            "Open": df[col_open].astype(float),
            "High": df[col_high].astype(float),
            "Low": df[col_low].astype(float),
            "Close": df[col_close].astype(float),
        }
    ).sort_values("ts")

    return df.reset_index(drop=True)


def _calc_pl(side: str, entry_px: Number, exit_px: Number, qty: Number) -> Number:
    if qty is None or qty == 0:
        return 0.0
    if side.upper() == "BUY":
        return float(exit_px - entry_px) * float(qty)
    else:
        # SELL
        return float(entry_px - exit_px) * float(qty)


def _label_from_pl(pl: Optional[Number], qty: Optional[Number]) -> Optional[str]:
    if qty is None or qty == 0:
        return "no_position"
    if pl is None:
        return None
    if pl > 0:
        return "win"
    if pl < 0:
        return "lose"
    return "flat"


# ----------------------------------------------------------------------
# コア：1件のシミュレレコードを評価
# ----------------------------------------------------------------------
def _eval_one(
    rec: Dict[str, Any],
    horizon_days: int = 5,
) -> SimEval:
    """
    1 レコード分の TP/SL 判定と PL 計算を行う。

    仕様（レベル3 / 5分足）:

      - 指値が「エントリー日に一度でもタッチしたか」を見る
      - 寄り前に出していた指値は、
        → 寄り付き (9:00) の板寄せで約定したら「寄り値で 9:00 約定」
      - 指値に一度も触れなければ no_position（0株扱い）

      - 約定した場合:
        - そこから最大 horizon_days 日ぶんの 5分足で TP / SL を監視
        - 先にヒットした方でクローズ（SL 優先）
        - どちらも 5営業日以内にヒットしなければ、
          horizon_days 日目の大引け（終値）でクローズ（タイムアップ）
    """

    code = str(rec.get("code") or "").strip()
    side = (rec.get("side") or "BUY").upper()
    entry_limit = rec.get("entry")
    tp = rec.get("tp")
    sl = rec.get("sl")

    qty_rakuten = rec.get("qty_rakuten") or 0
    qty_matsui = rec.get("qty_matsui") or 0

    # 日付
    trade_date = (
        _parse_date_any(rec.get("trade_date"))
        or _parse_date_any(rec.get("run_date"))
        or _parse_date_any(rec.get("price_date"))
    )
    if trade_date is None:
        # 日付が取れない場合は「評価不能」として素通し
        return SimEval(
            entry_ts=None,
            entry_px=None,
            exit_ts=None,
            exit_px=None,
            exit_reason=None,
            label_rakuten=None,
            pl_rakuten=None,
            label_matsui=None,
            pl_matsui=None,
            close_date=None,
            horizon_days=horizon_days,
        )

    # 発注時刻（ts） … シミュレ登録の時刻
    order_ts = _parse_iso_dt(rec.get("ts")) or _ensure_jst(
        _dt.combine(trade_date, _time(7, 0))
    )

    tz = timezone.get_default_timezone()
    session_open = _ensure_jst(_dt.combine(trade_date, _time(9, 0)))
    pre_open_order = order_ts <= session_open
    active_from_ts = session_open if pre_open_order else order_ts

    # 5分足取得
    try:
        df_all = _load_5m_bars(code, trade_date, horizon_days=horizon_days)
    except Exception:
        df_all = pd.DataFrame()

    if df_all.empty or entry_limit is None:
        # 5分足が無い or entry が無い場合は評価できない → no_position
        return SimEval(
            entry_ts=None,
            entry_px=None,
            exit_ts=None,
            exit_px=None,
            exit_reason="skip",
            label_rakuten="no_position" if qty_rakuten else "no_position",
            pl_rakuten=0.0,
            label_matsui="no_position" if qty_matsui else "no_position",
            pl_matsui=0.0,
            close_date=trade_date.isoformat(),
            horizon_days=horizon_days,
        )

    # ----- エントリー判定（trade_date のみ有効） -----------------
    df_today = df_all[df_all["ts"].dt.date == trade_date].copy()
    if df_today.empty:
        return SimEval(
            entry_ts=None,
            entry_px=None,
            exit_ts=None,
            exit_px=None,
            exit_reason="skip",
            label_rakuten="no_position" if qty_rakuten else "no_position",
            pl_rakuten=0.0,
            label_matsui="no_position" if qty_matsui else "no_position",
            pl_matsui=0.0,
            close_date=trade_date.isoformat(),
            horizon_days=horizon_days,
        )

    df_today = df_today.sort_values("ts")

    # 1本目のバー（寄り付き）
    first_bar_ts = df_today["ts"].min()
    entry_ts: Optional[_dt] = None
    exec_entry_px: Optional[Number] = None

    for idx, row in df_today.iterrows():
        bar_ts: _dt = row["ts"]
        if bar_ts < active_from_ts:
            continue

        o = float(row["Open"])
        h = float(row["High"])
        l = float(row["Low"])

        # 寄り前に出していた指値は「寄りで IN していれば寄り値で約定」
        if pre_open_order and bar_ts == first_bar_ts and entry_ts is None:
            if side == "BUY":
                if o <= entry_limit:
                    entry_ts = bar_ts
                    exec_entry_px = o  # ★寄り値で約定
                    break
            else:  # SELL
                if o >= entry_limit:
                    entry_ts = bar_ts
                    exec_entry_px = o
                    break

        # それ以外のケースは「バー内で指値タッチしたら指値で約定」
        if side == "BUY":
            if l <= entry_limit <= h:
                entry_ts = bar_ts
                exec_entry_px = float(entry_limit)
                break
        else:  # SELL
            if h >= entry_limit >= l:
                entry_ts = bar_ts
                exec_entry_px = float(entry_limit)
                break

    if entry_ts is None or exec_entry_px is None:
        # 1日を通して指値に一度も触れなかった → no_position
        return SimEval(
            entry_ts=None,
            entry_px=None,
            exit_ts=None,
            exit_px=None,
            exit_reason="skip",
            label_rakuten="no_position" if qty_rakuten else "no_position",
            pl_rakuten=0.0,
            label_matsui="no_position" if qty_matsui else "no_position",
            pl_matsui=0.0,
            close_date=trade_date.isoformat(),
            horizon_days=horizon_days,
        )

    # ----- TP / SL / タイムアップ判定 --------------------------------
    # エントリー時刻以降で horizon_days 日ぶんを見る
    horizon_end_ts = entry_ts + timedelta(days=horizon_days)

    df_eval = df_all[(df_all["ts"] >= entry_ts) & (df_all["ts"] <= horizon_end_ts)].copy()
    df_eval = df_eval.sort_values("ts")

    exit_ts: Optional[_dt] = None
    exit_px: Optional[Number] = None
    exit_reason: Optional[str] = None

    for idx, row in df_eval.iterrows():
        bar_ts: _dt = row["ts"]
        o = float(row["Open"])
        h = float(row["High"])
        l = float(row["Low"])
        c = float(row["Close"])

        # まず SL 優先
        if sl is not None:
            if side == "BUY" and l <= sl <= h:
                exit_ts = bar_ts
                exit_px = float(sl)
                exit_reason = "hit_sl"
                break
            if side == "SELL" and h >= sl >= l:
                exit_ts = bar_ts
                exit_px = float(sl)
                exit_reason = "hit_sl"
                break

        # 次に TP
        if tp is not None:
            if side == "BUY" and l <= tp <= h:
                exit_ts = bar_ts
                exit_px = float(tp)
                exit_reason = "hit_tp"
                break
            if side == "SELL" and h >= tp >= l:
                exit_ts = bar_ts
                exit_px = float(tp)
                exit_reason = "hit_tp"
                break

    if exit_ts is None or exit_px is None:
        # 5営業日内に TP/SL どちらもヒットしなかった → タイムアップ
        if not df_eval.empty:
            last_row = df_eval.iloc[-1]
        else:
            # 万一 df_eval が空なら、entry 当日の最終バーを使う
            last_row = df_today.iloc[-1]

        exit_ts = last_row["ts"]
        exit_px = float(last_row["Close"])
        exit_reason = "horizon_close"

    # ----- PL / ラベル計算 -------------------------------------------
    pl_rakuten = _calc_pl(side, exec_entry_px, exit_px, qty_rakuten)
    pl_matsui = _calc_pl(side, exec_entry_px, exit_px, qty_matsui)

    label_rakuten = _label_from_pl(pl_rakuten, qty_rakuten)
    label_matsui = _label_from_pl(pl_matsui, qty_matsui)

    close_date = exit_ts.date().isoformat() if isinstance(exit_ts, _dt) else None

    return SimEval(
        entry_ts=entry_ts,
        entry_px=exec_entry_px,
        exit_ts=exit_ts,
        exit_px=exit_px,
        exit_reason=exit_reason,
        label_rakuten=label_rakuten,
        pl_rakuten=pl_rakuten,
        label_matsui=label_matsui,
        pl_matsui=pl_matsui,
        close_date=close_date,
        horizon_days=horizon_days,
    )


# ----------------------------------------------------------------------
# 公開 API
# ----------------------------------------------------------------------
def eval_sim_record(rec: Dict[str, Any], horizon_days: int = 5) -> Dict[str, Any]:
    """
    ai_sim_eval から呼ばれるエントリポイント。

    入力: シミュレ JSON 1行 (dict)
    出力: eval_* 系フィールドを付けた dict
    """
    try:
        result = _eval_one(rec, horizon_days=horizon_days)
    except Exception:
        # 例外時は元のレコードを壊さず、そのまま返す
        return rec

    updated = dict(rec)

    # 共通
    updated["eval_horizon_days"] = result.horizon_days
    updated["eval_close_px"] = result.exit_px
    updated["eval_close_date"] = result.close_date
    updated["eval_exit_reason"] = result.exit_reason
    updated["eval_entry_ts"] = result.entry_ts.isoformat() if result.entry_ts else None
    updated["eval_exit_ts"] = result.exit_ts.isoformat() if result.exit_ts else None

    # 楽天
    updated["eval_pl_rakuten"] = result.pl_rakuten
    updated["eval_label_rakuten"] = result.label_rakuten

    # 松井
    updated["eval_pl_matsui"] = result.pl_matsui
    updated["eval_label_matsui"] = result.label_matsui

    return updated