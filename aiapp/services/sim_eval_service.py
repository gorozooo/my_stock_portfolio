# aiapp/services/sim_eval_service.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, datetime as _dt
from typing import Any, Dict, Optional

import pandas as pd
from django.utils import timezone

# 5分足キャッシュサービス
from aiapp.services import bars_5m as svc_bars_5m


Number = float | int


@dataclass
class SimEvalResult:
    # 約定
    entry_ts: Optional[_dt]
    entry_px: Optional[Number]

    # クローズ
    exit_ts: Optional[_dt]
    exit_px: Optional[Number]
    exit_reason: Optional[str]  # "hit_tp" / "hit_sl" / "horizon_close" / "skip"

    # 楽天
    pl_rakuten: Optional[Number]
    label_rakuten: Optional[str]

    # 松井
    pl_matsui: Optional[Number]
    label_matsui: Optional[str]

    # 表示用
    close_date: Optional[str]
    horizon_days: int


# =====================================================================
# 共通ユーティリティ
# =====================================================================

def _parse_iso_date(s: Optional[str]) -> Optional[_date]:
    if not isinstance(s, str) or not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return _dt.strptime(s, fmt).date()
        except Exception:
            continue
    try:
        return _dt.fromisoformat(s).date()
    except Exception:
        return None


def _ensure_aware(dt: _dt) -> _dt:
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_default_timezone())
    return timezone.localtime(dt)


def _load_5m_bars(code: str, trade_date: _date, horizon_days: int) -> pd.DataFrame:
    """
    5分足を bars_5m サービス経由で取得して正規化。

    ※ bars_5m.load_5m_bars は (code, trade_date) の 2 引数だけを
       受け取るので、horizon_days はここでは使わない。
       評価の営業日数は df の長さで吸収する。
    """
    # ★ bars_5m 側は (code, trade_date) の 2 引数
    raw = svc_bars_5m.load_5m_bars(code, trade_date)

    if raw is None:
        return pd.DataFrame()

    if isinstance(raw, tuple):
        df = raw[0]
    else:
        df = raw

    if df is None or len(df) == 0:
        return pd.DataFrame()

    df = df.copy()

    # ---- 列マッピング（MultiIndex 対応）------------------------------
    # c が ('Open', 'JPY') みたいなタプルでも、先頭要素だけ見て
    # "open" / "high" / "low" / "close" を判定する
    base_map: Dict[str, Any] = {}
    for c in df.columns:
        if isinstance(c, tuple) and len(c) > 0:
            key = str(c[0]).lower()
        else:
            key = str(c).lower()
        # 同じ key が複数あっても最初の1つだけ使う
        if key not in base_map:
            base_map[key] = c

    cols_lower = base_map
    for need in ("open", "high", "low", "close"):
        if need not in cols_lower:
            raise ValueError(f"5m bars missing column '{need}' for code={code}")

    c_open = cols_lower["open"]
    c_high = cols_lower["high"]
    c_low = cols_lower["low"]
    c_close = cols_lower["close"]

    # ts 列
    if "ts" in df.columns:
        ts = pd.to_datetime(df["ts"])
    else:
        ts = pd.to_datetime(df.index)

    if ts.dt.tz is None:
        ts = ts.dt.tz_localize(timezone.get_default_timezone())
    else:
        ts = ts.dt.tz_convert(timezone.get_default_timezone())

    out = pd.DataFrame(
        {
            "ts": ts,
            "Open": df[c_open].astype(float),
            "High": df[c_high].astype(float),
            "Low": df[c_low].astype(float),
            "Close": df[c_close].astype(float),
        }
    ).sort_values("ts")

    return out.reset_index(drop=True)


def _calc_pl(side: str, entry_px: Number, exit_px: Number, qty: Number) -> float:
    if qty is None or qty == 0:
        return 0.0
    if side.upper() == "BUY":
        return float(exit_px - entry_px) * float(qty)
    else:
        # 将来 SELL にも対応できるよう反転
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


# =====================================================================
# コア評価ロジック
# =====================================================================

def _eval_one(rec: Dict[str, Any], horizon_days: int = 5) -> SimEvalResult:
    """
    1件分のシミュレを評価する。

    ルール（BUY想定）:
      - trade_date の寄り付きで始値が指値以下なら「寄りで約定」
        → entry_px = 始値, entry_ts = 9:00バー
      - そうでなければ、5分足の中で「Low <= 指値 <= High」となる
        最初のバーで指値約定
        → entry_px = 指値, entry_ts = そのバーの時刻
      - どのバーでも指値に一度も触れなければ「no_position / skip」
      - 約定した場合は、
          そこから horizon_days 営業日ぶんの 5分足で TP / SL を監視
        * 先に SL に触れたら SL でクローズ (hit_sl)
        * 先に TP に触れたら TP でクローズ (hit_tp)
        * どちらも触れなければ、配列の最後のバー終値でクローズ
          (horizon_close)
    """

    code = str(rec.get("code") or "").strip()
    side = (rec.get("side") or "BUY").upper()

    entry_limit = rec.get("entry")
    tp = rec.get("tp")
    sl = rec.get("sl")

    qty_rakuten = rec.get("qty_rakuten") or 0
    qty_matsui = rec.get("qty_matsui") or 0

    # trade_date 決定
    trade_date = (
        _parse_iso_date(rec.get("trade_date"))
        or _parse_iso_date(rec.get("run_date"))
        or _parse_iso_date(rec.get("price_date"))
    )
    if trade_date is None:
        # 日付が取れない場合は評価不能として no_position 扱い
        return SimEvalResult(
            entry_ts=None,
            entry_px=None,
            exit_ts=None,
            exit_px=None,
            exit_reason="skip",
            pl_rakuten=0.0,
            label_rakuten="no_position",
            pl_matsui=0.0,
            label_matsui="no_position",
            close_date=None,
            horizon_days=horizon_days,
        )

    # 5分足ロード
    df = _load_5m_bars(code, trade_date, horizon_days=horizon_days)
    if df.empty or entry_limit is None:
        last_close = rec.get("last_close") or entry_limit
        close_date = trade_date.isoformat()
        return SimEvalResult(
            entry_ts=None,
            entry_px=None,
            exit_ts=None,
            exit_px=last_close,
            exit_reason="skip",
            pl_rakuten=0.0,
            label_rakuten="no_position",
            pl_matsui=0.0,
            label_matsui="no_position",
            close_date=close_date,
            horizon_days=horizon_days,
        )

    df = df.copy()
    # trade_date 以降だけを対象にする
    df = df[df["ts"].dt.date >= trade_date]
    df = df.reset_index(drop=True)

    if df.empty:
        last_close = rec.get("last_close") or entry_limit
        close_date = trade_date.isoformat()
        return SimEvalResult(
            entry_ts=None,
            entry_px=None,
            exit_ts=None,
            exit_px=last_close,
            exit_reason="skip",
            pl_rakuten=0.0,
            label_rakuten="no_position",
            pl_matsui=0.0,
            label_matsui="no_position",
            close_date=close_date,
            horizon_days=horizon_days,
        )

    # ------------------------------------------------------------
    # 1. エントリー判定
    # ------------------------------------------------------------
    entry_ts: Optional[_dt] = None
    entry_px: Optional[Number] = None
    entry_idx: Optional[int] = None

    first = df.iloc[0]
    first_open = float(first["Open"])
    first_ts = _ensure_aware(first["ts"])
    limit = float(entry_limit)

    if side == "BUY":
        # 寄りが指値以下 → 寄りで有利約定
        if first_open <= limit:
            entry_ts = first_ts
            entry_px = first_open
            entry_idx = 0
        else:
            # 寄りで刺さらなかった場合、Low/High のレンジヒットを探す
            mask = (df["Low"] <= limit) & (df["High"] >= limit)
            if mask.any():
                idx = int(mask.idxmax())
                bar = df.loc[idx]
                entry_ts = _ensure_aware(bar["ts"])
                entry_px = limit
                entry_idx = idx
    else:  # SELL（将来用）
        if first_open >= limit:
            entry_ts = first_ts
            entry_px = first_open
            entry_idx = 0
        else:
            mask = (df["Low"] <= limit) & (df["High"] >= limit)
            if mask.any():
                idx = int(mask.idxmax())
                bar = df.loc[idx]
                entry_ts = _ensure_aware(bar["ts"])
                entry_px = limit
                entry_idx = idx

    # 指値に一度も触れなかった → no_position
    if entry_ts is None or entry_idx is None:
        last = df.iloc[-1]
        last_close = float(last["Close"])
        close_date = last["ts"].date().isoformat()
        return SimEvalResult(
            entry_ts=None,
            entry_px=None,
            exit_ts=None,
            exit_px=last_close,
            exit_reason="skip",
            pl_rakuten=0.0,
            label_rakuten="no_position",
            pl_matsui=0.0,
            label_matsui="no_position",
            close_date=close_date,
            horizon_days=horizon_days,
        )

    # ------------------------------------------------------------
    # 2. TP / SL 判定（エントリー後のバーのみを見る）
    # ------------------------------------------------------------
    exit_ts: Optional[_dt] = None
    exit_px: Optional[Number] = None
    exit_reason: Optional[str] = None

    # エントリー以降のバー
    eval_df = df.iloc[entry_idx:]
    eval_df = eval_df.reset_index(drop=True)

    limit_tp = float(tp) if tp is not None else None
    limit_sl = float(sl) if sl is not None else None

    for i in range(1, len(eval_df)):
        row = eval_df.iloc[i]
        row_ts = _ensure_aware(row["ts"])
        low = float(row["Low"])
        high = float(row["High"])

        if side == "BUY":
            # 先に SL を優先
            if limit_sl is not None and low <= limit_sl:
                exit_ts = row_ts
                exit_px = limit_sl
                exit_reason = "hit_sl"
                break
            if limit_tp is not None and high >= limit_tp:
                exit_ts = row_ts
                exit_px = limit_tp
                exit_reason = "hit_tp"
                break
        else:
            # SELL の場合は逆方向（必要になったら詳細調整）
            if limit_tp is not None and high >= limit_tp:
                exit_ts = row_ts
                exit_px = limit_tp
                exit_reason = "hit_tp"
                break
            if limit_sl is not None and low <= limit_sl:
                exit_ts = row_ts
                exit_px = limit_sl
                exit_reason = "hit_sl"
                break

    # TP/SL どちらもヒットしなかった → horizon_close
    if exit_ts is None or exit_px is None:
        last = df.iloc[-1]
        exit_ts = _ensure_aware(last["ts"])
        exit_px = float(last["Close"])
        exit_reason = "horizon_close"

    close_date = exit_ts.date().isoformat()

    # ------------------------------------------------------------
    # 3. PL / ラベル計算
    # ------------------------------------------------------------
    pl_rakuten = _calc_pl(side, entry_px, exit_px, qty_rakuten)
    pl_matsui = _calc_pl(side, entry_px, exit_px, qty_matsui)

    label_rakuten = _label_from_pl(pl_rakuten, qty_rakuten)
    label_matsui = _label_from_pl(pl_matsui, qty_matsui)

    return SimEvalResult(
        entry_ts=entry_ts,
        entry_px=entry_px,
        exit_ts=exit_ts,
        exit_px=exit_px,
        exit_reason=exit_reason,
        pl_rakuten=pl_rakuten,
        label_rakuten=label_rakuten,
        pl_matsui=pl_matsui,
        label_matsui=label_matsui,
        close_date=close_date,
        horizon_days=horizon_days,
    )


# =====================================================================
# 公開 API
# =====================================================================

def eval_sim_record(rec: Dict[str, Any], horizon_days: int = 5) -> Dict[str, Any]:
    """
    ai_sim_eval コマンドから呼ばれるエントリポイント。

    - 受け取った rec を壊さずコピー
    - 評価結果を eval_* フィールドとして付与
    - 約定した場合は entry を「実際に約定した価格」に上書き
      （例：指値 3,449 円 / 始値 3,445 円 → entry=3,445）
    """
    result = _eval_one(rec, horizon_days=horizon_days)

    out = dict(rec)

    # 約定価格を entry に反映（no_position のときはそのまま）
    if result.entry_px is not None:
        out["entry"] = result.entry_px

    out["eval_label_rakuten"] = result.label_rakuten
    out["eval_pl_rakuten"] = result.pl_rakuten
    out["eval_label_matsui"] = result.label_matsui
    out["eval_pl_matsui"] = result.pl_matsui

    out["eval_close_px"] = result.exit_px
    out["eval_close_date"] = result.close_date
    out["eval_horizon_days"] = result.horizon_days

    out["eval_exit_reason"] = result.exit_reason
    out["eval_entry_ts"] = (
        result.entry_ts.isoformat() if result.entry_ts is not None else None
    )
    out["eval_exit_ts"] = (
        result.exit_ts.isoformat() if result.exit_ts is not None else None
    )

    return out