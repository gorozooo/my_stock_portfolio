# aiapp/services/sim_eval_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as _time, timedelta
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
from django.utils import timezone

# ★ 追加：日足OHLCV取得・特徴量
try:
    from aiapp.services.fetch_price import get_prices
except Exception:  # pragma: no cover
    get_prices = None  # type: ignore

try:
    from aiapp.models.features import make_features, FeatureConfig
except Exception:  # pragma: no cover
    make_features = None  # type: ignore
    FeatureConfig = None  # type: ignore


# =========================================================
# ユーティリティ
# =========================================================

def _parse_trade_date(rec: Dict[str, Any]) -> date:
    """
    rec["trade_date"] (または run_date) を date に変換する。
    ※ 古いデータ用のフォールバック。
    """
    v = rec.get("trade_date") or rec.get("run_date")
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v:
        return date.fromisoformat(v)
    raise ValueError(f"invalid trade_date: {v!r}")


def _parse_ts_local(ts_str: Optional[str]) -> Optional[datetime]:
    """
    JSONL の ts(ISO文字列) を Asia/Tokyo の tz-aware datetime に変換。
    エラー時は None。
    """
    if not isinstance(ts_str, str) or not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


def _next_weekday(d: date) -> date:
    """
    単純な翌営業日（祝日考慮なし、土日だけ飛ばす）。
    """
    d = d + timedelta(days=1)
    while d.weekday() >= 5:  # 5=土, 6=日
        d = d + timedelta(days=1)
    return d


def _decide_trade_date(rec: Dict[str, Any]) -> date:
    """
    どの日付の足から見始めるか（trade_date）を決定。

    ルール:
      - ts が 9:00 未満 → その日
      - ts が 9:00〜15:20 → その日
      - ts が 15:20 超え → 翌営業日
      - ts が無い旧データ → trade_date / run_date をそのまま使用
    """
    base = _parse_trade_date(rec)
    ts_local = _parse_ts_local(rec.get("ts"))
    if ts_local is None:
        return base

    d = ts_local.date()
    t = ts_local.time()

    # ts と trade_date がズレている場合、未来側を優先
    base_d = max(d, base)

    if t < _time(9, 0):
        return base_d
    if t <= _time(15, 20):
        return base_d
    # 引け後は翌営業日
    return _next_weekday(base_d)


def _decide_entry_start_ts(trade_d: date, rec: Dict[str, Any]) -> datetime:
    """
    エントリー判定を開始する最初の時刻。

    ルール:
      - ts が trade_d より前 → trade_d 09:00
      - ts が trade_d と同じ日:
          * 9:00 未満 → 09:00
          * 9:00〜15:20 → ts の直後の 5分足
      - それ以外 → trade_d 09:00
    """
    tz = timezone.get_default_timezone()
    open_ts = timezone.make_aware(
        datetime.combine(trade_d, _time(9, 0)), tz
    )

    ts_local = _parse_ts_local(rec.get("ts"))
    if ts_local is None:
        return open_ts

    # 日付が違う場合
    if ts_local.date() != trade_d:
        return open_ts

    # 同じ日
    t = ts_local.time()
    if t < _time(9, 0):
        return open_ts
    if t > _time(15, 20):
        return open_ts

    # 場中 → 直後の 5分足に切り上げ
    if ts_local.second != 0 or ts_local.microsecond != 0 or (ts_local.minute % 5 != 0):
        minute_block = (ts_local.minute // 5 + 1) * 5
        hour = ts_local.hour
        if minute_block >= 60:
            hour += 1
            minute_block -= 60
        start_naive = datetime.combine(trade_d, _time(hour, minute_block))
        return timezone.make_aware(start_naive, tz)

    return ts_local


def _yf_symbol(code: str) -> str:
    code = str(code).strip()
    if not code:
        raise ValueError("code is empty")
    if code.endswith(".T"):
        return code
    return f"{code}.T"


def _pick_price_col(df: pd.DataFrame, name: str) -> pd.Series:
    """
    yfinance の DataFrame から open/high/low/close を取り出す。
    MultiIndex 列 (('Open', 'xxx'), ...) にも対応。
    """
    target = None
    for c in df.columns:
        if isinstance(c, tuple):
            key = str(c[0]).lower()
        else:
            key = str(c).lower()
        if key == name:
            target = c
            break
    if target is None:
        raise ValueError(f"bars missing column '{name}'")
    return df[target]


def load_5m_bars(code: str, trade_date: date, horizon_days: int) -> pd.DataFrame:
    """
    指定コードの 5分足を trade_date から horizon_days 営業日ぶん取得。

    戻り値: ["ts", "open", "high", "low", "close"]
      ts は Asia/Tokyo の tz-aware datetime。
    """
    symbol = _yf_symbol(code)

    start_dt = datetime.combine(trade_date, datetime.min.time())
    end_dt = start_dt + timedelta(days=horizon_days + 1)

    df = yf.download(
        symbol,
        interval="5m",
        start=start_dt,
        end=end_dt,
        progress=False,
        auto_adjust=False,
    )
    if df is None or df.empty:
        raise ValueError(f"no 5m data for {code}")

    open_s = _pick_price_col(df, "open")
    high_s = _pick_price_col(df, "high")
    low_s = _pick_price_col(df, "low")
    close_s = _pick_price_col(df, "close")

    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    ts_jst = idx.tz_convert("Asia/Tokyo")

    out = pd.DataFrame(
        {
            "ts": ts_jst,
            "open": open_s.astype(float),
            "high": high_s.astype(float),
            "low": low_s.astype(float),
            "close": close_s.astype(float),
        }
    ).reset_index(drop=True)

    return out


def load_1d_bars(code: str, trade_date: date, horizon_days: int) -> pd.DataFrame:
    """
    指定コードの日足を trade_date から取得。

    horizon_days=5 でも、祝日などを考慮して少し長めに取り、
    実際に使うのは「trade_date 以降の営業日を上から数えて N 本目」だけ。
    """
    symbol = _yf_symbol(code)

    start_dt = trade_date - timedelta(days=1)
    end_dt = trade_date + timedelta(days=horizon_days * 3)

    df = yf.download(
        symbol,
        interval="1d",
        start=start_dt,
        end=end_dt,
        progress=False,
        auto_adjust=False,
    )
    if df is None or df.empty:
        raise ValueError(f"no 1d data for {code}")

    close_s = _pick_price_col(df, "close")

    out = pd.DataFrame({"close": close_s.astype(float)})
    out.index = close_s.index
    return out


def _pick_horizon_close_daily(
    code: str,
    trade_date: date,
    horizon_days: int,
) -> Tuple[datetime, float, int]:
    """
    「trade_date から数えて horizon_days 営業日目」の
    日足終値と日付を返す。

    戻り値:
      (終値の日付の 15:20 JST datetime, 終値, 実際営業日数)
    """
    df = load_1d_bars(code, trade_date, horizon_days)

    dates: list[date] = []
    closes: list[float] = []

    for idx, row in df.iterrows():
        if isinstance(idx, (datetime, pd.Timestamp)):
            d = idx.date()
        else:
            continue
        if d < trade_date:
            continue
        dates.append(d)
        closes.append(float(row["close"]))

    if not dates:
        raise ValueError(f"no 1d data on or after trade_date for {code}")

    if len(dates) >= horizon_days:
        idx = horizon_days - 1
    else:
        idx = len(dates) - 1

    d_target = dates[idx]
    close_px = closes[idx]
    effective_days = idx + 1

    tz = timezone.get_default_timezone()
    exit_ts = timezone.make_aware(
        datetime.combine(d_target, datetime.min.time()) + timedelta(hours=15, minutes=20),
        tz,
    )
    return exit_ts, close_px, effective_days


def _label_and_pl(
    qty: float,
    side: str,
    entry_px: Optional[float],
    exit_px: Optional[float],
) -> Tuple[str, float]:
    """
    qty / entry_px / exit_px から label(win/lose/flat/no_position) と PL を計算。
    """
    if not qty or entry_px is None or exit_px is None:
        return "no_position", 0.0

    side = (side or "BUY").upper()
    if side == "BUY":
        pl = (exit_px - entry_px) * qty
    else:
        pl = (entry_px - exit_px) * qty

    if pl > 0:
        label = "win"
    elif pl < 0:
        label = "lose"
    else:
        label = "flat"
    return label, float(pl)


def _to_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    dt_jst = dt.astimezone(timezone.get_default_timezone())
    return dt_jst.isoformat()


def _safe_float(x: Any) -> Optional[float]:
    if x in (None, "", "null"):
        return None
    try:
        v = float(x)
        if not np.isfinite(v):
            return None
        return v
    except Exception:
        return None


def _safe_int(x: Any) -> Optional[int]:
    if x in (None, "", "null"):
        return None
    try:
        return int(x)
    except Exception:
        return None


def _trend_from_slope_ret(slope: Optional[float], ret: Optional[float]) -> str:
    """
    超軽量のトレンドラベル（dataset用）
    """
    s = _safe_float(slope)
    r = _safe_float(ret)
    if s is None or r is None:
        return "unknown"
    if s >= 0 and r >= 0:
        return "up"
    if s <= 0 and r <= 0:
        return "down"
    return "side"


def _compute_design_metrics(
    side: str,
    entry_px: Optional[float],
    tp: Optional[float],
    sl: Optional[float],
    atr14: Optional[float],
) -> Dict[str, Any]:
    """
    Entry/TP/SL の“設計”を数値化して simulate に保存。
    BUY:
      reward = tp - entry
      risk   = entry - sl
    SELL:
      reward = entry - tp
      risk   = sl - entry
    """
    out: Dict[str, Any] = {
        "design_reward": None,
        "design_risk": None,
        "design_rr": None,
        "risk_atr": None,
        "reward_atr": None,
    }

    e = _safe_float(entry_px)
    t = _safe_float(tp)
    s = _safe_float(sl)
    a = _safe_float(atr14)

    if e is None or t is None or s is None:
        return out

    side_u = (side or "BUY").upper()
    if side_u == "BUY":
        reward = t - e
        risk = e - s
    else:
        reward = e - t
        risk = s - e

    if not np.isfinite(reward) or not np.isfinite(risk):
        return out

    out["design_reward"] = float(reward)
    out["design_risk"] = float(risk)

    if risk <= 0:
        out["design_rr"] = None
    else:
        out["design_rr"] = float(reward / risk)

    if a is not None and a > 0:
        out["risk_atr"] = float(risk / a)
        out["reward_atr"] = float(reward / a)

    return out


def _attach_daily_feature_snapshot(
    code: str,
    trade_d: date,
) -> Dict[str, Any]:
    """
    trade_d 時点の “日足特徴量スナップショット” を作って返す。
    失敗しても空dictを返す（落とさない）。
    """
    if get_prices is None or make_features is None:
        return {}

    try:
        # だいたい 2年分あれば 200MA まで余裕
        raw = get_prices(code, nbars=None, period="3y")
        if raw is None or raw.empty:
            return {}
        df = raw.copy()
        # trade_d 以降が混じってもOKだが、評価基準としては trade_d までを使う
        try:
            df = df[df.index.date <= trade_d]
        except Exception:
            pass
        if df.empty:
            return {}

        feat = make_features(df, cfg=None)
        if feat is None or feat.empty:
            return {}

        last = feat.iloc[-1]

        # 重要なキーだけ “トップレベルでも” 使いやすいように返す
        def _g(k: str) -> Optional[float]:
            if k not in feat.columns:
                return None
            v = pd.to_numeric(last.get(k), errors="coerce")
            try:
                fv = float(v)
                if not np.isfinite(fv):
                    return None
                return fv
            except Exception:
                return None

        atr14 = _g("ATR14")
        slope25 = _g("SLOPE_25")
        ret20 = _g("RET_20")
        rsi14 = _g("RSI14")
        bbz = _g("BB_Z")
        vwap_gap = _g("VWAP_GAP_PCT")

        trend_daily = _trend_from_slope_ret(slope25, ret20)

        snap = {
            "atr_14": atr14,
            "slope_25": slope25,
            "ret_20": ret20,
            "rsi_14": rsi14,
            "bb_z": bbz,
            "vwap_gap_pct": vwap_gap,
            "trend_daily": trend_daily,
        }

        # 将来の拡張用に “feature_snapshot” としても保存（軽量）
        snap["feature_snapshot"] = {
            "ATR14": atr14,
            "SLOPE_25": slope25,
            "RET_20": ret20,
            "RSI14": rsi14,
            "BB_Z": bbz,
            "VWAP_GAP_PCT": vwap_gap,
        }

        return snap
    except Exception:
        return {}


# =========================================================
# メイン：1レコード評価
# =========================================================

def eval_sim_record(rec: Dict[str, Any], horizon_days: int = 5) -> Dict[str, Any]:
    """
    1つのシミュレレコードを評価して、eval_ 系の情報を付与して返す。

      - entry/tp/sl は「AI 指値スナップショット」 → 改変しない
      - 実際の約定価格・時間は eval_entry_px / eval_entry_ts
      - TP/SL にかからなければ horizon_days 営業日目の日足終値でクローズ
      - ts が場中なら、その時刻以降の 5分足だけを見てエントリーを判定
      - ★ 本番用: 日足特徴量スナップショット（atr/slope/ret等）と設計指標(rr等)を out に保存
    """
    out = dict(rec)

    code = str(rec.get("code"))
    side = (rec.get("side") or "BUY").upper()

    ai_entry_px = rec.get("entry")
    tp = rec.get("tp")
    sl = rec.get("sl")

    ai_entry_px = float(ai_entry_px) if ai_entry_px is not None else None
    tp = float(tp) if tp is not None else None
    sl = float(sl) if sl is not None else None

    # トレード開始日と、エントリー判定開始時刻
    trade_d = _decide_trade_date(rec)
    start_ts_for_entry = _decide_entry_start_ts(trade_d, rec)

    # ★ 日足特徴量スナップショット（失敗してもOK）
    snap = _attach_daily_feature_snapshot(code, trade_d)
    if snap:
        # トップレベルにも置く（datasetで拾いやすい）
        out["atr_14"] = snap.get("atr_14")
        out["slope_25"] = snap.get("slope_25")
        out["ret_20"] = snap.get("ret_20")
        out["trend_daily"] = snap.get("trend_daily")
        out["feature_snapshot"] = snap.get("feature_snapshot")

    # ★ 設計指標（RR, ATR倍率など）
    atr14 = _safe_float(out.get("atr_14"))
    dm = _compute_design_metrics(side, ai_entry_px, tp, sl, atr14)
    out.update(dm)

    # 5分足取得
    try:
        df_5m = load_5m_bars(code, trade_d, horizon_days)
    except Exception:
        out["eval_horizon_days"] = horizon_days
        return out

    if df_5m.empty:
        out["eval_horizon_days"] = horizon_days
        return out

    # ============================================
    # 1) エントリー判定
    # ============================================
    entry_ts: Optional[datetime] = None
    entry_px: Optional[float] = None

    if ai_entry_px is None:
        entry_ts = None
        entry_px = None
    else:
        df_for_entry = df_5m[df_5m["ts"] >= start_ts_for_entry]

        if df_for_entry.empty:
            entry_ts = None
            entry_px = None
        else:
            first = df_for_entry.iloc[0]
            open_px = float(first["open"])
            open_ts = first["ts"].to_pydatetime()

            if side == "BUY":
                if ai_entry_px >= open_px:
                    entry_ts = open_ts
                    entry_px = open_px
                else:
                    hit = df_for_entry[
                        (df_for_entry["low"] <= ai_entry_px)
                        & (df_for_entry["high"] >= ai_entry_px)
                    ]
                    if not hit.empty:
                        bar = hit.iloc[0]
                        entry_ts = bar["ts"].to_pydatetime()
                        entry_px = ai_entry_px
            else:
                if ai_entry_px <= open_px:
                    entry_ts = open_ts
                    entry_px = open_px
                else:
                    hit = df_for_entry[
                        (df_for_entry["high"] >= ai_entry_px)
                        & (df_for_entry["low"] <= ai_entry_px)
                    ]
                    if not hit.empty:
                        bar = hit.iloc[0]
                        entry_ts = bar["ts"].to_pydatetime()
                        entry_px = ai_entry_px

    # ============================================
    # 2) エグジット判定（TP / SL / タイムアップ）
    # ============================================
    exit_reason: Optional[str] = None
    exit_ts: Optional[datetime] = None
    exit_px: Optional[float] = None

    def _horizon_close_with_daily() -> Tuple[Optional[datetime], Optional[float], int]:
        try:
            dts, px, eff_days = _pick_horizon_close_daily(code, trade_d, horizon_days)
            return dts, px, eff_days
        except Exception:
            last_bar = df_5m.iloc[-1]
            ts = last_bar["ts"].to_pydatetime()
            px = float(last_bar["close"])
            return ts, px, horizon_days

    if entry_ts is None or entry_px is None:
        exit_ts, exit_px, eff_days = _horizon_close_with_daily()
        exit_reason = "no_fill"
        out["eval_horizon_days"] = eff_days
    else:
        df_after = df_5m[df_5m["ts"] >= entry_ts]

        hit_index: Optional[int] = None
        hit_kind: Optional[str] = None
        hit_px: Optional[float] = None
        hit_ts: Optional[datetime] = None

        if not df_after.empty:
            for i, row in df_after.iterrows():
                high = float(row["high"])
                low = float(row["low"])
                bar_ts = row["ts"].to_pydatetime()

                if side == "BUY":
                    if tp is not None and high >= tp:
                        hit_index = i
                        hit_kind = "hit_tp"
                        hit_px = tp
                        hit_ts = bar_ts
                        break
                    if sl is not None and low <= sl:
                        hit_index = i
                        hit_kind = "hit_sl"
                        hit_px = sl
                        hit_ts = bar_ts
                        break
                else:
                    if tp is not None and low <= tp:
                        hit_index = i
                        hit_kind = "hit_tp"
                        hit_px = tp
                        hit_ts = bar_ts
                        break
                    if sl is not None and high >= sl:
                        hit_index = i
                        hit_kind = "hit_sl"
                        hit_px = sl
                        hit_ts = bar_ts
                        break

        if hit_index is not None:
            exit_reason = hit_kind
            exit_px = hit_px
            exit_ts = hit_ts
            out["eval_horizon_days"] = horizon_days
        else:
            exit_ts, exit_px, eff_days = _horizon_close_with_daily()
            exit_reason = "horizon_close"
            out["eval_horizon_days"] = eff_days

    # ============================================
    # 3) PL / ラベル計算
    # ============================================
    qty_r = float(out.get("qty_rakuten") or 0)
    qty_m = float(out.get("qty_matsui") or 0)

    label_r, pl_r = _label_and_pl(qty_r, side, entry_px, exit_px)
    label_m, pl_m = _label_and_pl(qty_m, side, entry_px, exit_px)

    out["eval_label_rakuten"] = label_r
    out["eval_pl_rakuten"] = pl_r
    out["eval_label_matsui"] = label_m
    out["eval_pl_matsui"] = pl_m

    out["eval_close_px"] = exit_px
    out["eval_close_date"] = exit_ts.date().isoformat() if exit_ts else None
    out["eval_exit_reason"] = exit_reason
    out["eval_entry_px"] = entry_px
    out["eval_entry_ts"] = _to_iso(entry_ts)
    out["eval_exit_ts"] = _to_iso(exit_ts)

    # ============================================
    # 4) eval_r（結果の質：PLを“想定損失”で割る）
    # ============================================
    # est_loss が無い/0 の時は None（落とさない）
    est_loss_r = _safe_float(out.get("est_loss_rakuten"))
    est_loss_m = _safe_float(out.get("est_loss_matsui"))

    def _calc_r(pl: float, est_loss: Optional[float]) -> Optional[float]:
        if est_loss is None:
            return None
        denom = abs(float(est_loss))
        if denom <= 1e-9:
            return None
        return float(pl / denom)

    out["eval_r_rakuten"] = _calc_r(pl_r, est_loss_r)
    out["eval_r_matsui"] = _calc_r(pl_m, est_loss_m)

    # UI 用まとめラベル
    combined = "unknown"
    labels = {label_r, label_m}
    if labels <= {"no_position"}:
        combined = "skip"
    elif "win" in labels and "lose" in labels:
        combined = "mixed"
    elif "win" in labels:
        combined = "win"
    elif "lose" in labels:
        combined = "lose"
    elif labels <= {"flat"}:
        combined = "flat"
    out["_combined_label"] = combined

    return out