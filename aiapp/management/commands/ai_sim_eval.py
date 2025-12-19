# aiapp/management/commands/ai_sim_eval.py
# -*- coding: utf-8 -*-
"""
ai_sim_eval

AI紙シミュ評価（5分足）→ VirtualTrade に eval_* / R / EV_true_pro / rank_pro を反映

合意（プロ仕様 / A案）：
- 評価開始は opened_at（ただし場外なら当日 09:00 に丸める）
- entry は「起票日当日のみ」。刺さらなければ no_position で CLOSED
- 寄り判定だけは “寄り値(09:00)” を別取得（日足Open or 1分足の最初）
- BUY指値は「上限価格」：
    寄り <= entry なら寄りで即約定（marketable limit）
- SELL指値は「下限価格」：
    寄り >= entry なら寄りで即約定（marketable limit）
- A案：R固定（起票時に想定した構造を壊さない）
    R0 = |entry - sl|（起票時のR）
    tp_ratio = (tp - entry)/R0（起票時のTP距離をR換算）
    実約定が有利でも、TP/SL を exec_entry_px 起点で “同じR幅” に再配置
- 評価期間：horizon_bd 営業日（デフォルト3）
    TP/SL ヒットで即CLOSED
    horizon最終営業日 15:30 の最後の足で未達なら time_stop で強制CLOSED
    horizonがまだ完了してない場合は carry（=毎日評価対象に残す）
- 対象抽出：
    trade_date 基準
    eval_exit_reason == ""（未評価） または "carry"（途中）
    それ以外（hit_tp/hit_sl/time_stop/no_position 等）は二度と触らない（再現性維持）
- tz整合：
    DBはUTC保持、足データはJST混在しうる → 比較は必ず Asia/Tokyo に統一
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, datetime as _dt, time as _time, timedelta as _timedelta
from typing import Any, Dict, Optional, Tuple, List

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade
from aiapp.models.behavior_stats import BehaviorStats
from aiapp.services.bars_5m import load_5m_bars


# ==============================
# small helpers
# ==============================

def _jst_session_range(d: _date) -> Tuple[_dt, _dt]:
    tz = timezone.get_default_timezone()
    start = timezone.make_aware(_dt.combine(d, _time(9, 0)), tz)
    end = timezone.make_aware(_dt.combine(d, _time(15, 30)), tz)
    return start, end


def _to_local(dt: Optional[_dt]) -> Optional[_dt]:
    if dt is None:
        return None
    try:
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)  # Asia/Tokyo
    except Exception:
        return None


def _safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        f = float(x)
        if f != f:
            return None
        return f
    except Exception:
        return None


def _label(qty: Optional[int], pl_amount: float) -> str:
    """
    pl_amount は「建玉数量ぶん」ではなく「その銘柄の損益（数量反映前/後は問わない）」でOK。
    qty<=0 は no_position。
    """
    if qty is None or qty <= 0:
        return "no_position"
    if pl_amount > 0:
        return "win"
    if pl_amount < 0:
        return "lose"
    return "flat"


def _find_ohlc_columns(df) -> Tuple[Optional[Any], Optional[Any], Optional[Any], Optional[Any]]:
    """
    df.columns が str でも MultiIndex でも、
    'open' / 'low' / 'high' / 'close'(or 'adj close') を拾う。
    """
    open_col = low_col = high_col = close_col = None
    for col in df.columns:
        if isinstance(col, tuple):
            parts = [str(p).lower() for p in col if p is not None]
        else:
            parts = [str(col).lower()]

        if open_col is None and any(p == "open" for p in parts):
            open_col = col
        if low_col is None and any(p == "low" for p in parts):
            low_col = col
        if high_col is None and any(p == "high" for p in parts):
            high_col = col
        if close_col is None and any(p in ("close", "adj close") for p in parts):
            close_col = col

    return open_col, low_col, high_col, close_col


def _coerce_ts(val: Any, fallback: _dt) -> _dt:
    try:
        import pandas as pd
    except Exception:
        return fallback

    if isinstance(val, pd.Series):
        if not val.empty:
            val = val.iloc[0]
        else:
            return fallback

    try:
        ts = pd.Timestamp(val)
    except Exception:
        ts = pd.to_datetime(val, errors="coerce")

    if pd.isna(ts):
        return fallback

    dt = ts.to_pydatetime()
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_default_timezone())
    return timezone.localtime(dt)


def _ensure_ts_jst(df) -> Optional[Any]:
    """
    df["ts"] を必ず Asia/Tokyo の tz-aware datetime にそろえる。
    """
    try:
        import pandas as pd
    except Exception:
        return None

    if "ts" not in df.columns:
        return None

    try:
        s = pd.to_datetime(df["ts"], errors="coerce")
    except Exception:
        return None

    try:
        if getattr(s.dt, "tz", None) is not None:
            s = s.dt.tz_convert("Asia/Tokyo")
        else:
            s = s.dt.tz_localize("Asia/Tokyo")
    except Exception:
        # object dtype などの最後の砦
        try:
            s2 = []
            for x in s:
                if pd.isna(x):
                    s2.append(pd.NaT)
                    continue
                ts = pd.Timestamp(x)
                if ts.tzinfo is None:
                    ts = ts.tz_localize("Asia/Tokyo")
                else:
                    ts = ts.tz_convert("Asia/Tokyo")
                s2.append(ts)
            s = pd.Series(s2, index=df.index)
        except Exception:
            return None

    df["ts"] = s
    return df


def _is_business_day(d: _date) -> bool:
    # 祝日は一旦考慮しない（合意どおり）
    return d.weekday() < 5


def _add_business_days(start: _date, n: int) -> _date:
    """
    start を 0日目として、n営業日目（0-based）の日付を返す。
    例：start=火、n=2 -> 木（火=0,水=1,木=2）
    """
    if n <= 0:
        return start
    d = start
    i = 0
    while i < n:
        d = d + _timedelta(days=1)
        if _is_business_day(d):
            i += 1
    return d


def _business_days_inclusive(start: _date, end: _date) -> List[_date]:
    d = start
    out: List[_date] = []
    while d <= end:
        if _is_business_day(d):
            out.append(d)
        d = d + _timedelta(days=1)
    return out


def _fetch_open_price_yori(code: str, d: _date) -> Optional[float]:
    """
    “寄り値(09:00)”を別取得（プロ仕様）
    優先順位：
      1) 1分足 09:00〜09:01 の最初（可能なら）
      2) 日足 Open
    どちらも取れなければ None
    """
    try:
        import pandas as pd
        import yfinance as yf
    except Exception:
        return None

    ticker = f"{str(code)}.T"

    # 1分足（09:00 の寄りに最も近い）
    try:
        # JSTの当日 09:00〜09:02 を狙う。yfinance はUTC基準で返ることが多いので、
        # まず日付範囲で取りに行ってから JSTに寄せて 09:00 を探す。
        start = pd.Timestamp(d).tz_localize("Asia/Tokyo")
        end = start + pd.Timedelta(days=1)
        df1 = yf.download(
            tickers=ticker,
            interval="1m",
            start=start.tz_convert("UTC"),
            end=end.tz_convert("UTC"),
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        if df1 is not None and len(df1) > 0:
            # index をJSTに
            try:
                if df1.index.tz is None:
                    df1.index = df1.index.tz_localize("UTC").tz_convert("Asia/Tokyo")
                else:
                    df1.index = df1.index.tz_convert("Asia/Tokyo")
            except Exception:
                pass

            # 09:00 〜 09:01 の最初
            window = df1.between_time("09:00", "09:01")
            if window is not None and len(window) > 0:
                # Open列名の揺れに対応
                for c in ["Open", "open"]:
                    if c in window.columns:
                        v = _safe_float(window.iloc[0][c])
                        if v is not None:
                            return float(v)
    except Exception:
        pass

    # 日足Open
    try:
        start = d - _timedelta(days=3)
        end = d + _timedelta(days=1)
        dfd = yf.download(
            tickers=ticker,
            interval="1d",
            start=start,
            end=end,
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        if dfd is not None and len(dfd) > 0:
            # 行の特定：日付でいける場合
            try:
                # index は日付 or datetime
                # 当日に最も近い行を探す
                row = None
                if getattr(dfd.index, "tz", None) is not None:
                    idx = dfd.index.tz_convert("Asia/Tokyo").date
                else:
                    idx = dfd.index.date
                for i, dd in enumerate(idx):
                    if dd == d:
                        row = dfd.iloc[i]
                        break
                if row is None:
                    row = dfd.iloc[-1]
            except Exception:
                row = dfd.iloc[-1]

            for c in ["Open", "open"]:
                if c in row.index:
                    v = _safe_float(row[c])
                    if v is not None:
                        return float(v)
    except Exception:
        pass

    return None


@dataclass
class EvalResult:
    ok: bool
    reason: str
    eval_entry_px: Optional[float] = None
    eval_entry_ts: Optional[_dt] = None
    eval_exit_px: Optional[float] = None
    eval_exit_ts: Optional[_dt] = None
    eval_exit_reason: str = ""
    pl_per_share: Optional[float] = None


# ==============================
# core evaluation
# ==============================

def _evaluate_one(v: VirtualTrade, *, horizon_bd: int, verbose: int = 1) -> EvalResult:
    """
    A案（R固定）で評価する。
    - entry は trade_date 当日のみ
    - exit は trade_date から horizon_bd 営業日（3）まで跨ぐ
    """

    # すでにCLOSED扱いなら何もしない（再現性維持）
    # ※ 呼び出し側で対象抽出してるが、保険で。
    if str(v.eval_exit_reason or "").strip() not in ("", "carry"):
        return EvalResult(ok=True, reason="already_closed", eval_exit_reason=str(v.eval_exit_reason or "").strip())

    if horizon_bd <= 0:
        horizon_bd = 1

    trade_date = v.trade_date
    side = str(v.side or "BUY").upper().strip()
    if side not in ("BUY", "SELL"):
        side = "BUY"

    # 起票時パラメータ（entry/tp/sl）
    entry_plan = _safe_float(v.entry_px) or _safe_float((v.replay or {}).get("sim_order", {}).get("entry"))
    tp_plan = _safe_float(v.tp_px) or _safe_float((v.replay or {}).get("sim_order", {}).get("tp"))
    sl_plan = _safe_float(v.sl_px) or _safe_float((v.replay or {}).get("sim_order", {}).get("sl"))

    if entry_plan is None:
        return EvalResult(ok=False, reason="no_entry")
    if sl_plan is None:
        return EvalResult(ok=False, reason="no_sl")  # A案のR固定のため必須

    R0 = abs(float(entry_plan) - float(sl_plan))
    if R0 <= 0:
        return EvalResult(ok=False, reason="bad_r")

    tp_ratio = None
    if tp_plan is not None:
        try:
            tp_ratio = (float(tp_plan) - float(entry_plan)) / float(R0)
        except Exception:
            tp_ratio = None

    # opened_at を JST に
    opened_local = _to_local(v.opened_at)
    if opened_local is None:
        return EvalResult(ok=False, reason="no_opened_at")

    # 評価対象となる営業日リスト
    horizon_end_date = _add_business_days(trade_date, horizon_bd - 1)
    days_list = _business_days_inclusive(trade_date, horizon_end_date)
    if not days_list:
        days_list = [trade_date]

    # その日の場時間
    session_start, session_end = _jst_session_range(trade_date)

    # entry は当日だけ：active_start は opened_at（ただし場外なら当日9:00に丸め）
    if opened_local < session_start:
        active_start = session_start
    elif opened_local > session_end:
        # 当日場が終わってから起票 → 当日entry不可 → no_position
        return EvalResult(
            ok=True,
            reason="no_position",
            eval_exit_reason="no_position",
            pl_per_share=0.0,
        )
    else:
        active_start = opened_local

    # ---- trade_date の 5分足ロード（entry判定用） ----
    bars0 = load_5m_bars(v.code, trade_date)
    if bars0 is None or len(bars0) == 0:
        return EvalResult(ok=False, reason="no_bars")

    df0 = bars0.copy()

    # tsカラム復元（必要なら）
    if "ts" not in df0.columns:
        try:
            import pandas as pd
            if isinstance(df0.index, pd.DatetimeIndex):
                df0 = df0.reset_index().rename(columns={df0.index.name or "index": "ts"})
            else:
                return EvalResult(ok=False, reason="no_ts")
        except Exception:
            return EvalResult(ok=False, reason="no_ts")

    df0 = _ensure_ts_jst(df0)
    if df0 is None:
        return EvalResult(ok=False, reason="bad_ts")

    open_col, low_col, high_col, close_col = _find_ohlc_columns(df0)
    if low_col is None or high_col is None or close_col is None:
        return EvalResult(ok=False, reason="no_ohlc")

    # 当日レンジに絞る
    df0_eff = df0[(df0["ts"] >= active_start) & (df0["ts"] <= session_end)]
    if df0_eff is None or len(df0_eff) == 0:
        return EvalResult(ok=False, reason="no_bars_after_active")

    # ---- 寄り値（09:00）を別取得して marketable 判定 ----
    # 寄り値は “寄り約定判定にだけ使う”
    yori = _fetch_open_price_yori(v.code, trade_date)

    exec_entry_px: Optional[float] = None
    entry_ts: Optional[_dt] = None

    # (1) 寄り約定判定（opened_at が寄り前なら寄りで約定できる可能性）
    #     ※ opened_at が寄り後でも、寄りでの即約定は意味がないので無視
    if opened_local <= session_start and yori is not None:
        if side == "BUY":
            if float(yori) <= float(entry_plan):
                exec_entry_px = float(yori)
                entry_ts = session_start
        else:  # SELL
            if float(yori) >= float(entry_plan):
                exec_entry_px = float(yori)
                entry_ts = session_start

    # (2) 寄りで決まらなかった場合：当日5分足で指値（marketable limit + 通常到達）
    if exec_entry_px is None or entry_ts is None:
        for _, row in df0_eff.iterrows():
            bar_ts = _coerce_ts(row["ts"], fallback=active_start)

            # open
            o = None
            if open_col is not None:
                try:
                    o = _safe_float(row[open_col])
                except Exception:
                    o = None

            # lo/hi
            try:
                lo = _safe_float(row[low_col])
                hi = _safe_float(row[high_col])
            except Exception:
                lo = hi = None

            if side == "BUY":
                # marketable limit：open <= entry なら open で即約定
                if o is not None and float(o) <= float(entry_plan):
                    exec_entry_px = float(o)
                    entry_ts = bar_ts
                    break
                # 通常：バー内で entry 到達
                if lo is not None and hi is not None and float(lo) <= float(entry_plan) <= float(hi):
                    exec_entry_px = float(entry_plan)
                    entry_ts = bar_ts
                    break
            else:  # SELL
                # marketable limit：open >= entry なら open で即約定
                if o is not None and float(o) >= float(entry_plan):
                    exec_entry_px = float(o)
                    entry_ts = bar_ts
                    break
                if lo is not None and hi is not None and float(lo) <= float(entry_plan) <= float(hi):
                    exec_entry_px = float(entry_plan)
                    entry_ts = bar_ts
                    break

    # 当日中に刺さらなかった → no_position で CLOSED（合意）
    if exec_entry_px is None or entry_ts is None:
        return EvalResult(
            ok=True,
            reason="no_position",
            eval_exit_reason="no_position",
            pl_per_share=0.0,
        )

    # ---- A案：TP/SL を exec_entry_px 起点で R固定再配置 ----
    if side == "BUY":
        sl_exec = float(exec_entry_px) - float(R0)
        tp_exec = None if tp_ratio is None else (float(exec_entry_px) + float(R0) * float(tp_ratio))
    else:  # SELL
        sl_exec = float(exec_entry_px) + float(R0)  # SELLの損切りは上側
        tp_exec = None if tp_ratio is None else (float(exec_entry_px) - float(R0) * float(tp_ratio))

    # ---- exit判定：trade_date〜horizon_end_date（営業日）で 5分足を跨いで評価 ----
    exit_px: Optional[float] = None
    exit_ts: Optional[_dt] = None
    exit_reason: str = ""

    # まだ将来日の足が取れない（= horizon完了してない）場合は carry にする
    # → “取れる範囲でTP/SLが当たってたら当てる。無ければ carry”
    reached_horizon_end = True

    # entry日から評価開始
    started = False

    for di, d in enumerate(days_list):
        bars = load_5m_bars(v.code, d)
        if bars is None or len(bars) == 0:
            # 途中の日のデータがまだ無い（市場前/取得失敗など）
            reached_horizon_end = False
            break

        df = bars.copy()
        if "ts" not in df.columns:
            try:
                import pandas as pd
                if isinstance(df.index, pd.DatetimeIndex):
                    df = df.reset_index().rename(columns={df.index.name or "index": "ts"})
                else:
                    reached_horizon_end = False
                    break
            except Exception:
                reached_horizon_end = False
                break

        df = _ensure_ts_jst(df)
        if df is None:
            reached_horizon_end = False
            break

        open_col2, low_col2, high_col2, close_col2 = _find_ohlc_columns(df)
        if low_col2 is None or high_col2 is None or close_col2 is None:
            reached_horizon_end = False
            break

        day_start, day_end = _jst_session_range(d)

        # entry日だけ：entry_ts 以降
        if d == trade_date:
            df_eff = df[(df["ts"] >= entry_ts) & (df["ts"] <= day_end)]
            started = True
        else:
            # entry後の日
            if not started:
                # 本来あり得ないが保険
                df_eff = df[(df["ts"] >= entry_ts) & (df["ts"] <= day_end)]
                started = True
            else:
                df_eff = df[(df["ts"] >= day_start) & (df["ts"] <= day_end)]

        if df_eff is None or len(df_eff) == 0:
            # この日の有効バーがない → horizon完了扱いにしない
            reached_horizon_end = False
            break

        # TP/SL判定（先に当たった方）
        hit_tp_ts = None
        hit_sl_ts = None

        if side == "BUY":
            if tp_exec is not None:
                tp_mask = df_eff[high_col2] >= float(tp_exec)
                if tp_mask.to_numpy().any():
                    i0 = df_eff[tp_mask].index[0]
                    r0 = df_eff.loc[i0]
                    hit_tp_ts = _coerce_ts(r0["ts"], fallback=day_start)

            sl_mask = df_eff[low_col2] <= float(sl_exec)
            if sl_mask.to_numpy().any():
                i0 = df_eff[sl_mask].index[0]
                r0 = df_eff.loc[i0]
                hit_sl_ts = _coerce_ts(r0["ts"], fallback=day_start)

            if hit_tp_ts is not None or hit_sl_ts is not None:
                if hit_tp_ts is not None and hit_sl_ts is not None:
                    if hit_tp_ts <= hit_sl_ts:
                        exit_ts = hit_tp_ts
                        exit_px = float(tp_exec)
                        exit_reason = "hit_tp"
                    else:
                        exit_ts = hit_sl_ts
                        exit_px = float(sl_exec)
                        exit_reason = "hit_sl"
                elif hit_tp_ts is not None:
                    exit_ts = hit_tp_ts
                    exit_px = float(tp_exec)
                    exit_reason = "hit_tp"
                else:
                    exit_ts = hit_sl_ts
                    exit_px = float(sl_exec)
                    exit_reason = "hit_sl"

                break
        else:  # SELL
            # SELLは利益方向が下。TPは low <= tp_exec、SLは high >= sl_exec
            if tp_exec is not None:
                tp_mask = df_eff[low_col2] <= float(tp_exec)
                if tp_mask.to_numpy().any():
                    i0 = df_eff[tp_mask].index[0]
                    r0 = df_eff.loc[i0]
                    hit_tp_ts = _coerce_ts(r0["ts"], fallback=day_start)

            sl_mask = df_eff[high_col2] >= float(sl_exec)
            if sl_mask.to_numpy().any():
                i0 = df_eff[sl_mask].index[0]
                r0 = df_eff.loc[i0]
                hit_sl_ts = _coerce_ts(r0["ts"], fallback=day_start)

            if hit_tp_ts is not None or hit_sl_ts is not None:
                if hit_tp_ts is not None and hit_sl_ts is not None:
                    if hit_tp_ts <= hit_sl_ts:
                        exit_ts = hit_tp_ts
                        exit_px = float(tp_exec)
                        exit_reason = "hit_tp"
                    else:
                        exit_ts = hit_sl_ts
                        exit_px = float(sl_exec)
                        exit_reason = "hit_sl"
                elif hit_tp_ts is not None:
                    exit_ts = hit_tp_ts
                    exit_px = float(tp_exec)
                    exit_reason = "hit_tp"
                else:
                    exit_ts = hit_sl_ts
                    exit_px = float(sl_exec)
                    exit_reason = "hit_sl"
                break

        # この日ではTP/SL未達 → 次の日へ
        # horizon最終日に来たら time_stop でクローズするかは後段

    # TP/SLで決着してない場合
    if exit_reason == "":
        if not reached_horizon_end:
            # horizonがまだ完了してない（未来日の足が無い）→ carry
            return EvalResult(
                ok=True,
                reason="entry_ok",
                eval_entry_px=float(exec_entry_px),
                eval_entry_ts=entry_ts,
                eval_exit_reason="carry",
                pl_per_share=None,
            )

        # horizon最終日の 15:30 の最後の足足で強制クローズ（time_stop）
        last_day = days_list[-1]
        bars_last = load_5m_bars(v.code, last_day)
        if bars_last is None or len(bars_last) == 0:
            # 本来 reached_horizon_end True ならここは来ないが保険
            return EvalResult(
                ok=True,
                reason="entry_ok",
                eval_entry_px=float(exec_entry_px),
                eval_entry_ts=entry_ts,
                eval_exit_reason="carry",
                pl_per_share=None,
            )

        dfL = bars_last.copy()
        if "ts" not in dfL.columns:
            try:
                import pandas as pd
                if isinstance(dfL.index, pd.DatetimeIndex):
                    dfL = dfL.reset_index().rename(columns={dfL.index.name or "index": "ts"})
                else:
                    return EvalResult(
                        ok=True,
                        reason="entry_ok",
                        eval_entry_px=float(exec_entry_px),
                        eval_entry_ts=entry_ts,
                        eval_exit_reason="carry",
                        pl_per_share=None,
                    )
            except Exception:
                return EvalResult(
                    ok=True,
                    reason="entry_ok",
                    eval_entry_px=float(exec_entry_px),
                    eval_entry_ts=entry_ts,
                    eval_exit_reason="carry",
                    pl_per_share=None,
                )

        dfL = _ensure_ts_jst(dfL)
        if dfL is None:
            return EvalResult(
                ok=True,
                reason="entry_ok",
                eval_entry_px=float(exec_entry_px),
                eval_entry_ts=entry_ts,
                eval_exit_reason="carry",
                pl_per_share=None,
            )

        _, _, _, close_colL = _find_ohlc_columns(dfL)
        if close_colL is None:
            return EvalResult(
                ok=True,
                reason="entry_ok",
                eval_entry_px=float(exec_entry_px),
                eval_entry_ts=entry_ts,
                eval_exit_reason="carry",
                pl_per_share=None,
            )

        day_start, day_end = _jst_session_range(last_day)
        df_effL = dfL[(dfL["ts"] >= day_start) & (dfL["ts"] <= day_end)]
        if df_effL is None or len(df_effL) == 0:
            return EvalResult(
                ok=True,
                reason="entry_ok",
                eval_entry_px=float(exec_entry_px),
                eval_entry_ts=entry_ts,
                eval_exit_reason="carry",
                pl_per_share=None,
            )

        last_row = df_effL.iloc[-1]
        exit_ts = _coerce_ts(last_row["ts"], fallback=day_end)
        exit_px = float(last_row[close_colL])
        exit_reason = "time_stop"

    # pl_per_share（BUY/SELLで符号を合わせる）
    if exit_px is None or exit_ts is None:
        # ここに来るのは基本おかしいので carry
        return EvalResult(
            ok=True,
            reason="entry_ok",
            eval_entry_px=float(exec_entry_px),
            eval_entry_ts=entry_ts,
            eval_exit_reason="carry",
            pl_per_share=None,
        )

    if side == "BUY":
        pl_per_share = float(exit_px) - float(exec_entry_px)
    else:
        pl_per_share = float(exec_entry_px) - float(exit_px)

    return EvalResult(
        ok=True,
        reason="ok",
        eval_entry_px=float(exec_entry_px),
        eval_entry_ts=entry_ts,
        eval_exit_px=float(exit_px),
        eval_exit_ts=exit_ts,
        eval_exit_reason=str(exit_reason),
        pl_per_share=float(pl_per_share),
    )


def _ev_true_from_behavior(code: str) -> float:
    row = (
        BehaviorStats.objects
        .filter(code=str(code), mode_period="all", mode_aggr="all")
        .values("win_rate")
        .first()
    )
    if not row:
        return 0.0
    wr = _safe_float(row.get("win_rate"))
    if wr is None:
        return 0.0
    v = max(0.0, min(1.0, wr / 100.0))
    return float(v)


def _rank_within_run(run_id: str) -> int:
    qs = (
        VirtualTrade.objects
        .filter(run_id=run_id)
        .order_by("-ev_true_pro", "code", "id")
        .only("id", "ev_true_pro")
    )

    updated = 0
    with transaction.atomic():
        i = 0
        for v in qs:
            i += 1
            if v.rank_pro != i:
                VirtualTrade.objects.filter(id=v.id).update(rank_pro=i)
                updated += 1
    return updated


# ==============================
# management command
# ==============================

class Command(BaseCommand):
    help = "AI紙シミュ評価（5分足）→ VirtualTrade に eval_* / R / EV_true_pro / Rank を反映（A案/3営業日）"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=20, help="何日前まで評価対象に含めるか（trade_date基準）")
        parser.add_argument("--horizon", type=int, default=3, help="評価期間（営業日）。例：3なら起票日+2営業日まで")
        parser.add_argument("--limit", type=int, default=0, help="0なら全件。>0なら最大件数（新しい opened_at 優先）")
        parser.add_argument("--force", action="store_true", help="すでに評価済みでも再評価する（通常cronでは使わない）")
        parser.add_argument("--dry-run", action="store_true", help="DB更新せずログだけ")
        parser.add_argument("--include-today", action="store_true", help="市場前でもtrade_date=todayを対象に含める（通常は推奨しない）")

    def handle(self, *args, **options):
        verbose = int(options.get("verbosity", 1) or 1)

        # days の 0→5化け防止（Noneだけデフォルト）
        days_opt = options.get("days", None)
        days = 20 if days_opt is None else int(days_opt)

        horizon_opt = options.get("horizon", None)
        horizon = 3 if horizon_opt is None else int(horizon_opt)
        if horizon <= 0:
            horizon = 1

        limit_opt = options.get("limit", None)
        limit = 0 if limit_opt is None else int(limit_opt)

        force = bool(options.get("force"))
        dry_run = bool(options.get("dry_run"))
        include_today = bool(options.get("include_today"))

        now_local = timezone.localtime()
        today = now_local.date()  # JST

        # 市場前に今日を含めると no_bars だらけになるので、原則は今日を除外（合意）
        # ただし手動テストで include_today を付ければ入る
        date_max = today
        if (not include_today) and (now_local.time() < _time(15, 35)):
            # 15:35未満なら今日の5分足が揃ってない/未取得が多い → 今日は外す
            # （金曜朝のテスト問題を潰す）
            date_max = today - _timedelta(days=1)
            # date_max が土日なら直近営業日に寄せる（祝日は無視）
            while date_max.weekday() >= 5:
                date_max = date_max - _timedelta(days=1)

        if days <= 0:
            date_min = date_max
        else:
            date_min = date_max - _timedelta(days=days)

        # future trade_date を拾わない（date_maxで止める）
        qs = VirtualTrade.objects.filter(trade_date__gte=date_min, trade_date__lte=date_max)

        if not force:
            # 未評価("") と carry のみ
            qs = qs.filter(eval_exit_reason__in=["", "carry"])

        qs = qs.order_by("-opened_at")
        if limit and limit > 0:
            qs = qs[:limit]

        targets = list(qs)

        self.stdout.write(
            f"[ai_sim_eval] start days={days} horizon={horizon} date_min={date_min} date_max={date_max} "
            f"targets={len(targets)} force={force} dry_run={dry_run}"
        )

        updated = 0
        skipped = 0
        touched_run_ids: set[str] = set()

        for v in targets:
            try:
                res = _evaluate_one(v, horizon_bd=horizon, verbose=verbose)

                # 評価不能系（データが無い/壊れてる/前提不足）は “評価済み扱い”にしたいならここで落とすが、
                # 今回は carry を優先して、no_bars系は理由を入れて止める（再現性は維持される）
                if not res.ok and res.reason in ("no_bars", "no_bars_after_active", "no_ts", "no_ohlc", "bad_ts", "no_opened_at", "no_entry", "no_sl", "bad_r"):
                    skipped += 1
                    if verbose >= 2:
                        self.stdout.write(
                            f"  skip id={v.id} code={v.code} trade_date={v.trade_date} reason={res.reason}"
                        )
                    if not dry_run:
                        VirtualTrade.objects.filter(id=v.id).update(
                            eval_exit_reason=res.reason,
                        )
                        updated += 1
                        touched_run_ids.add(v.run_id)
                    continue

                # no_position / carry / ok(hit_tp/hit_sl/time_stop) をここで反映
                if res.eval_exit_reason == "":
                    # 保険
                    res.eval_exit_reason = res.reason

                # carry は損益未確定なので 0 として扱う（表示は carry）
                pl_per_share = _safe_float(res.pl_per_share)
                if res.eval_exit_reason == "carry":
                    pl_per_share = None

                qty_r = int(v.qty_rakuten or 0)
                qty_s = int(v.qty_sbi or 0)
                qty_m = int(v.qty_matsui or 0)

                # 数量反映損益
                eval_pl_r = None if pl_per_share is None else (float(pl_per_share) * float(qty_r))
                eval_pl_s = None if pl_per_share is None else (float(pl_per_share) * float(qty_s))
                eval_pl_m = None if pl_per_share is None else (float(pl_per_share) * float(qty_m))

                # labels（carry は carry のまま表示したいので label は win/lose にはしない）
                if res.eval_exit_reason == "carry":
                    lab_r = "carry" if qty_r > 0 else "no_position"
                    lab_s = "carry" if qty_s > 0 else "no_position"
                    lab_m = "carry" if qty_m > 0 else "no_position"
                else:
                    # CLOSED済み
                    # 数量>0 で pl が None は基本ないが、保険で0扱い
                    pr = float(eval_pl_r or 0.0) if qty_r > 0 else 0.0
                    ps = float(eval_pl_s or 0.0) if qty_s > 0 else 0.0
                    pm = float(eval_pl_m or 0.0) if qty_m > 0 else 0.0
                    lab_r = _label(qty_r, pr)
                    lab_s = _label(qty_s, ps)
                    lab_m = _label(qty_m, pm)

                # closed_at は CLOSED 時のみ入れる（carryはNone）
                closed_at = None
                if res.eval_exit_reason not in ("", "carry"):
                    closed_at = res.eval_exit_ts if res.eval_exit_ts is not None else timezone.now()

                # EV_true_pro（A案: all/all）
                ev_true_pro = _ev_true_from_behavior(v.code)

                # replay last_eval
                replay = v.replay if isinstance(v.replay, dict) else {}
                replay["last_eval"] = {
                    "trade_date": str(v.trade_date),
                    "opened_at": str(_to_local(v.opened_at) or v.opened_at),
                    "result": res.reason,
                    "entry_px": res.eval_entry_px,
                    "entry_ts": str(res.eval_entry_ts) if res.eval_entry_ts else None,
                    "exit_px": res.eval_exit_px,
                    "exit_ts": str(res.eval_exit_ts) if res.eval_exit_ts else None,
                    "exit_reason": res.eval_exit_reason,
                    "pl_per_share": pl_per_share,
                    "horizon_bd": horizon,
                    "policy": "A(R_fixed_relocate_tp_sl)",
                }
                pro = replay.get("pro")
                if isinstance(pro, dict):
                    pro["ev_true_pro"] = ev_true_pro
                    replay["pro"] = pro

                if not dry_run:
                    v.eval_entry_px = res.eval_entry_px
                    v.eval_entry_ts = res.eval_entry_ts
                    v.eval_exit_px = res.eval_exit_px
                    v.eval_exit_ts = res.eval_exit_ts
                    v.eval_exit_reason = str(res.eval_exit_reason or "")

                    v.eval_label_rakuten = lab_r
                    v.eval_label_sbi = lab_s
                    v.eval_label_matsui = lab_m

                    v.eval_pl_rakuten = eval_pl_r
                    v.eval_pl_sbi = eval_pl_s
                    v.eval_pl_matsui = eval_pl_m

                    v.closed_at = closed_at
                    v.ev_true_pro = ev_true_pro
                    v.replay = replay

                    # R 再計算（モデル側が est_loss を分母にする仕様を維持）
                    v.recompute_r()

                    v.save(update_fields=[
                        "eval_entry_px", "eval_entry_ts",
                        "eval_exit_px", "eval_exit_ts",
                        "eval_exit_reason",
                        "eval_label_rakuten", "eval_label_sbi", "eval_label_matsui",
                        "eval_pl_rakuten", "eval_pl_sbi", "eval_pl_matsui",
                        "result_r_rakuten", "result_r_sbi", "result_r_matsui",
                        "closed_at",
                        "ev_true_pro",
                        "replay",
                    ])

                    updated += 1
                    touched_run_ids.add(v.run_id)

                    if verbose >= 2:
                        self.stdout.write(
                            f"  ok id={v.id} code={v.code} trade_date={v.trade_date} "
                            f"exit={v.eval_exit_reason} entry={v.eval_entry_px} exit_px={v.eval_exit_px}"
                        )

            except Exception as e:
                skipped += 1
                if verbose >= 2:
                    self.stdout.write(
                        f"  skip id={v.id} code={v.code} trade_date={v.trade_date} reason=exception({type(e).__name__}) {e}"
                    )
                if not dry_run:
                    VirtualTrade.objects.filter(id=v.id).update(
                        eval_exit_reason="exception",
                    )
                    updated += 1
                    touched_run_ids.add(v.run_id)
                continue

        ranked_rows = 0
        if not dry_run:
            for rid in sorted(touched_run_ids):
                ranked_rows += _rank_within_run(rid)

        self.stdout.write(
            f"[ai_sim_eval] done updated={updated} skipped={skipped} touched_run_ids={len(touched_run_ids)} "
            f"ranked_rows={ranked_rows} dry_run={dry_run}"
        )