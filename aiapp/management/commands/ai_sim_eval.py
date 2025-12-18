# aiapp/management/commands/ai_sim_eval.py
# -*- coding: utf-8 -*-
"""
ai_sim_eval

AI紙シミュ評価（5分足）→ VirtualTrade に eval_* / R / EV_true_pro / rank_pro を反映

重要方針（あなたのルール）：
- 評価開始時刻は固定しない
- opened_at（注文を作った時刻＝現実世界で注文を出した時刻）から評価開始
- ただし時刻比較は必ず tz を揃える（DBはUTC保持、5分足の ts はJST）

★今回の仕様（プロ仕様）
- 評価期間：horizon 営業日（デフォ3、あなたの指定で3営業日）
  - 「営業日」判定：可能なら JPX カレンダー（pandas_market_calendars）を使う
  - 無い環境なら “土日除外” のみでフォールバック
- entry（指値約定判定）は “起票日（trade_date）当日のみ”
  - 刺さらなければ即CLOSED（no_position）
- TP/SLにヒットした時点で即CLOSED
- horizon 最終営業日 15:30 の最後の足で未達なら強制CLOSED（exit_reason="time_stop"）
- carry（途中状態）は CLOSED になるまで毎日評価対象に残す
  - eval_exit_reason="carry" の間は毎回再評価する（--force不要）

★9020の件の本丸
- BUYの指値は「上限価格」なので、寄り/直後の open が entry 以下なら entry 到達を待たずに open で約定する（marketable limit）
- SELLの指値は「下限価格」なので、寄り/直後の open が entry 以上なら open で約定する
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
    """
    その営業日のザラ場時間（簡易）
    - 09:00〜15:30
    """
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


def _label(qty: Optional[int], pl_per_share: float) -> str:
    if qty is None or qty <= 0:
        return "no_position"
    if pl_per_share > 0:
        return "win"
    if pl_per_share < 0:
        return "lose"
    return "flat"


def _find_ohlc_columns(df) -> Tuple[Optional[Any], Optional[Any], Optional[Any], Optional[Any]]:
    """
    df.columns が str でも MultiIndex でも、
    'open' / 'low' / 'high' / 'close'(or 'adj close') を拾う。
    戻り値は df[...] のキーとして使える実カラム値。
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
    """
    row["ts"] を確実に datetime にする（SeriesやTimestampでもOK）。
    """
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

    # MultiIndex columns の場合でも "ts" がある想定（('ts','') みたいなケース）
    ts_col = None
    for c in df.columns:
        if c == "ts":
            ts_col = c
            break
        if isinstance(c, tuple) and len(c) >= 1 and str(c[0]).lower() == "ts":
            ts_col = c
            break

    if ts_col is None:
        return None

    try:
        s = pd.to_datetime(df[ts_col], errors="coerce")
    except Exception:
        return None

    try:
        if getattr(s.dt, "tz", None) is not None:
            s = s.dt.tz_convert("Asia/Tokyo")
        else:
            s = s.dt.tz_localize("Asia/Tokyo")
    except Exception:
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

    df[ts_col] = s

    # 呼び出し側が "ts" を参照してるので、ts_col が tuple なら別名 "ts" を作る
    if ts_col != "ts":
        df["ts"] = df[ts_col]

    return df


def _jpx_trading_days(start_date: _date, count_days: int) -> List[_date]:
    """
    start_date を含めて count_days 営業日分の日付リストを返す。
    - 可能なら pandas_market_calendars の JPX を使う
    - 無いなら土日除外でフォールバック
    """
    if count_days <= 0:
        return [start_date]

    # 1) JPXカレンダーが使えるならそれを優先
    try:
        import pandas as pd
        import pandas_market_calendars as mcal  # type: ignore
        cal = mcal.get_calendar("JPX")

        # 余裕を持って広めにschedule取る（祝日が多くても足りるように）
        end_guess = start_date + _timedelta(days=count_days * 10)
        sched = cal.schedule(start_date=start_date, end_date=end_guess)
        days = [d.date() for d in sched.index.to_pydatetime()]

        if start_date not in days:
            # scheduleが何かでズレた場合の保険
            pass

        # start_date 以降から count_days 取り出し
        out = []
        for d in days:
            if d >= start_date:
                out.append(d)
            if len(out) >= count_days:
                break
        if len(out) >= count_days:
            return out
    except Exception:
        pass

    # 2) フォールバック：土日だけ除外
    out2: List[_date] = []
    d = start_date
    while len(out2) < count_days:
        if d.weekday() < 5:
            out2.append(d)
        d += _timedelta(days=1)
    return out2


@dataclass
class EvalResult:
    ok: bool
    reason: str

    eval_entry_px: Optional[float] = None
    eval_entry_ts: Optional[_dt] = None

    eval_exit_px: Optional[float] = None
    eval_exit_ts: Optional[_dt] = None
    eval_exit_reason: str = ""  # hit_tp/hit_sl/time_stop/no_position/carry/...

    pl_per_share: Optional[float] = None


# ==============================
# core evaluation
# ==============================

def _load_day_bars(code: str, day: _date) -> Tuple[Optional[Any], str]:
    bars = load_5m_bars(code, day)
    if bars is None or len(bars) == 0:
        return None, "no_bars"
    df = bars.copy()

    # ts カラムがなければ index から復元
    if "ts" not in df.columns:
        try:
            import pandas as pd
            if isinstance(df.index, pd.DatetimeIndex):
                df = df.reset_index().rename(columns={df.index.name or "index": "ts"})
            else:
                return None, "no_ts"
        except Exception:
            return None, "no_ts"

    df2 = _ensure_ts_jst(df)
    if df2 is None:
        return None, "bad_ts"

    return df2, "ok"


def _evaluate_entry_on_trade_date(
    v: VirtualTrade,
    *,
    verbose: int,
) -> EvalResult:
    """
    entry判定は trade_date 当日のみ。
    opened_at 以降から、marketable limit を含めて最初の約定バーを探す。
    """
    trade_date = v.trade_date

    df, st = _load_day_bars(v.code, trade_date)
    if df is None:
        return EvalResult(ok=False, reason=st)

    open_col, low_col, high_col, close_col = _find_ohlc_columns(df)
    if low_col is None or high_col is None or close_col is None:
        return EvalResult(ok=False, reason="no_ohlc")

    opened_local = _to_local(v.opened_at)
    if opened_local is None:
        return EvalResult(ok=False, reason="no_opened_at")

    session_start, session_end = _jst_session_range(trade_date)

    # active_start = opened_at を場内に丸める
    if opened_local < session_start:
        active_start = session_start
    elif opened_local > session_end:
        # 当日場が終わった後に起票 → 当日entryは不可能
        return EvalResult(ok=True, reason="no_position", eval_exit_reason="no_position", pl_per_share=0.0)
    else:
        active_start = opened_local

    df_eff = df[(df["ts"] >= active_start) & (df["ts"] <= session_end)]
    if df_eff is None or len(df_eff) == 0:
        return EvalResult(ok=True, reason="no_position", eval_exit_reason="no_position", pl_per_share=0.0)

    entry = _safe_float(v.entry_px) or _safe_float((v.replay or {}).get("sim_order", {}).get("entry"))
    tp = _safe_float(v.tp_px) or _safe_float((v.replay or {}).get("sim_order", {}).get("tp"))
    sl = _safe_float(v.sl_px) or _safe_float((v.replay or {}).get("sim_order", {}).get("sl"))

    if entry is None:
        return EvalResult(ok=False, reason="no_entry")

    side = str(v.side or "BUY").upper().strip()

    exec_entry_px: Optional[float] = None
    entry_ts: Optional[_dt] = None

    # 5分足を上から舐めて「最初に約定するバー」を決める
    for _, row in df_eff.iterrows():
        lo = _safe_float(row[low_col])
        hi = _safe_float(row[high_col])

        o = None
        if open_col is not None:
            o = _safe_float(row[open_col])

        bar_ts = _coerce_ts(row["ts"], fallback=active_start)

        if side == "SELL":
            # SELL指値：open >= entry なら open で即約定（より有利）
            if o is not None and o >= entry:
                exec_entry_px = float(o)
                entry_ts = bar_ts
                break
            # 通常：バー内で entry 到達
            if lo is not None and hi is not None and lo <= entry <= hi:
                exec_entry_px = float(entry)
                entry_ts = bar_ts
                break
        else:
            # BUY指値：open <= entry なら open で即約定（より有利）
            if o is not None and o <= entry:
                exec_entry_px = float(o)
                entry_ts = bar_ts
                break
            # 通常：バー内で entry 到達
            if lo is not None and hi is not None and lo <= entry <= hi:
                exec_entry_px = float(entry)
                entry_ts = bar_ts
                break

    if exec_entry_px is None or entry_ts is None:
        # 当日刺さらなかった → 即CLOSED
        return EvalResult(
            ok=True,
            reason="no_position",
            eval_entry_px=None,
            eval_entry_ts=None,
            eval_exit_px=None,
            eval_exit_ts=None,
            eval_exit_reason="no_position",
            pl_per_share=0.0,
        )

    # entry成功（exitはまだ）
    return EvalResult(
        ok=True,
        reason="entry_ok",
        eval_entry_px=float(exec_entry_px),
        eval_entry_ts=entry_ts,
        eval_exit_reason="carry",  # いったんcarry扱い（毎日再評価対象に残す）
        pl_per_share=None,
    )


def _scan_tp_sl_in_df(
    eval_df,
    *,
    tp: Optional[float],
    sl: Optional[float],
    low_col,
    high_col,
    entry_ts: _dt,
) -> Tuple[Optional[_dt], Optional[float], str]:
    """
    eval_df 内で TP/SL のどちらが先にヒットしたかを返す。
    ヒットしなければ (None, None, "")。
    """
    hit_tp_idx = None
    hit_sl_idx = None

    if tp is not None:
        tp_mask = eval_df[high_col] >= float(tp)
        if tp_mask.to_numpy().any():
            hit_tp_idx = eval_df[tp_mask].index[0]

    if sl is not None:
        sl_mask = eval_df[low_col] <= float(sl)
        if sl_mask.to_numpy().any():
            hit_sl_idx = eval_df[sl_mask].index[0]

    if hit_tp_idx is None and hit_sl_idx is None:
        return None, None, ""

    if hit_tp_idx is not None and hit_sl_idx is not None:
        if hit_tp_idx <= hit_sl_idx:
            row2 = eval_df.loc[hit_tp_idx]
            exit_ts = _coerce_ts(row2["ts"], fallback=entry_ts)
            return exit_ts, float(tp), "hit_tp"
        row2 = eval_df.loc[hit_sl_idx]
        exit_ts = _coerce_ts(row2["ts"], fallback=entry_ts)
        return exit_ts, float(sl), "hit_sl"

    if hit_tp_idx is not None:
        row2 = eval_df.loc[hit_tp_idx]
        exit_ts = _coerce_ts(row2["ts"], fallback=entry_ts)
        return exit_ts, float(tp), "hit_tp"

    row2 = eval_df.loc[hit_sl_idx]
    exit_ts = _coerce_ts(row2["ts"], fallback=entry_ts)
    return exit_ts, float(sl), "hit_sl"


def _evaluate_exit_across_horizon(
    v: VirtualTrade,
    *,
    horizon_bd: int,
    verbose: int,
) -> EvalResult:
    """
    entryが成立している前提で、trade_date 起票日を含む horizon_bd 営業日で TP/SL を探索。
    - ヒットしたら即CLOSED
    - 最終営業日まで未達なら time_stop で強制CLOSED（最終日の最後の足 close）
    - まだ最終営業日が来ていない場合は carry を返す（毎日再評価）
    """
    trade_date = v.trade_date

    # entry/TP/SL
    entry_px = _safe_float(v.eval_entry_px) or _safe_float(v.entry_px) or _safe_float((v.replay or {}).get("sim_order", {}).get("entry"))
    entry_ts0 = v.eval_entry_ts
    if entry_ts0 is None:
        # entry_ts が無いのはおかしいが、最低限 opened_at から
        entry_ts0 = _to_local(v.opened_at) or timezone.make_aware(_dt.combine(trade_date, _time(9, 0)), timezone.get_default_timezone())
    else:
        entry_ts0 = _to_local(entry_ts0) or entry_ts0

    tp = _safe_float(v.tp_px) or _safe_float((v.replay or {}).get("sim_order", {}).get("tp"))
    sl = _safe_float(v.sl_px) or _safe_float((v.replay or {}).get("sim_order", {}).get("sl"))

    if entry_px is None:
        return EvalResult(ok=False, reason="no_entry")

    # 対象営業日リスト（起票日含めて horizon_bd）
    days = _jpx_trading_days(trade_date, horizon_bd)
    last_day = days[-1]

    now_jst = timezone.localtime()
    # 最終営業日を過ぎていなければ、まだ time_stop 判定はできない
    can_time_stop = now_jst.date() >= last_day

    # 1日目：entry_ts0 以降、2日目以降：場の開始から
    for i, d in enumerate(days):
        df, st = _load_day_bars(v.code, d)
        if df is None:
            # データ欠損があるなら、その時点は評価不能（プロとしては carry 継続が安全）
            return EvalResult(ok=True, reason=st, eval_exit_reason="carry", eval_entry_px=float(entry_px), eval_entry_ts=entry_ts0)

        open_col, low_col, high_col, close_col = _find_ohlc_columns(df)
        if low_col is None or high_col is None or close_col is None:
            return EvalResult(ok=True, reason="no_ohlc", eval_exit_reason="carry", eval_entry_px=float(entry_px), eval_entry_ts=entry_ts0)

        session_start, session_end = _jst_session_range(d)

        if i == 0:
            start_ts = entry_ts0
            if start_ts < session_start:
                start_ts = session_start
        else:
            start_ts = session_start

        df_eff = df[(df["ts"] >= start_ts) & (df["ts"] <= session_end)]
        if df_eff is None or len(df_eff) == 0:
            # その日の有効バーが無い → 次の日へ
            continue

        exit_ts, exit_px, exit_reason = _scan_tp_sl_in_df(
            df_eff,
            tp=tp,
            sl=sl,
            low_col=low_col,
            high_col=high_col,
            entry_ts=entry_ts0,
        )
        if exit_reason:
            pl_per_share = float(exit_px) - float(entry_px)
            return EvalResult(
                ok=True,
                reason="ok",
                eval_entry_px=float(entry_px),
                eval_entry_ts=entry_ts0,
                eval_exit_px=float(exit_px),
                eval_exit_ts=exit_ts,
                eval_exit_reason=exit_reason,
                pl_per_share=pl_per_share,
            )

    # TP/SL 未達
    if not can_time_stop:
        # まだ最終営業日が来てない → carry（毎日評価対象に残す）
        return EvalResult(
            ok=True,
            reason="carry",
            eval_entry_px=float(entry_px),
            eval_entry_ts=entry_ts0,
            eval_exit_reason="carry",
            pl_per_share=None,
        )

    # time_stop：最終営業日の最後の足 close でクローズ
    df_last, st = _load_day_bars(v.code, last_day)
    if df_last is None:
        return EvalResult(ok=True, reason=st, eval_exit_reason="carry", eval_entry_px=float(entry_px), eval_entry_ts=entry_ts0)

    open_col, low_col, high_col, close_col = _find_ohlc_columns(df_last)
    if close_col is None:
        return EvalResult(ok=True, reason="no_ohlc", eval_exit_reason="carry", eval_entry_px=float(entry_px), eval_entry_ts=entry_ts0)

    # 最終日の最後の足
    last_row = df_last.iloc[-1]
    exit_ts = _coerce_ts(last_row["ts"], fallback=timezone.make_aware(_dt.combine(last_day, _time(15, 30)), timezone.get_default_timezone()))
    exit_px = float(last_row[close_col])
    pl_per_share = float(exit_px) - float(entry_px)

    return EvalResult(
        ok=True,
        reason="ok",
        eval_entry_px=float(entry_px),
        eval_entry_ts=entry_ts0,
        eval_exit_px=float(exit_px),
        eval_exit_ts=exit_ts,
        eval_exit_reason="time_stop",
        pl_per_share=pl_per_share,
    )


def _evaluate_one(v: VirtualTrade, *, horizon_bd: int, verbose: int = 1) -> EvalResult:
    """
    1) entry判定（trade_date当日のみ）
       - 刺さらなければ no_position で即CLOSED
    2) entryしたら horizon_bd 営業日の間、TP/SL探索
       - hit_tp / hit_sl / time_stop でCLOSED
       - まだ期間中なら carry（毎日再評価対象）
    """
    # すでにCLOSEDしてるなら基本触らない（force時は呼ばれるが、handle側で制御）
    # ただし carry は毎日再評価するのでOK
    if (v.eval_exit_reason or "").strip() not in ("", "carry"):
        return EvalResult(ok=True, reason="already_closed", eval_exit_reason=str(v.eval_exit_reason))

    # entryが未確定（eval_entry_ts が無い or eval_exit_reason 空）
    if v.eval_entry_ts is None and (v.eval_exit_reason or "").strip() == "":
        ent = _evaluate_entry_on_trade_date(v, verbose=verbose)
        # entry失敗系（no_position）ならここで終了
        if ent.eval_exit_reason == "no_position":
            return ent
        # entry成功なら DB へ entry を反映してから exit評価へ進む（handle側で保存）
        return ent

    # entryが既にある（carry等）
    return _evaluate_exit_across_horizon(v, horizon_bd=horizon_bd, verbose=verbose)


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
    help = "AI紙シミュ評価（5分足）→ VirtualTrade に eval_* / R / EV_true_pro / Rank を反映"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=10, help="何日前まで評価対象に含めるか（trade_date基準）")
        parser.add_argument("--horizon", type=int, default=3, help="評価期間（営業日）")
        parser.add_argument("--limit", type=int, default=0, help="0なら全件。>0なら最大件数（新しい opened_at 優先）")
        parser.add_argument("--force", action="store_true", help="すでに評価済みでも再評価する（cronでは使わない）")
        parser.add_argument("--dry-run", action="store_true", help="DB更新せずログだけ")

    def handle(self, *args, **options):
        verbose = int(options.get("verbosity", 1) or 1)

        days_opt = options.get("days", None)
        days = 10 if days_opt is None else int(days_opt)

        horizon_opt = options.get("horizon", None)
        horizon = 3 if horizon_opt is None else int(horizon_opt)
        if horizon <= 0:
            horizon = 1

        limit_opt = options.get("limit", None)
        limit = 0 if limit_opt is None else int(limit_opt)

        force = bool(options.get("force"))
        dry_run = bool(options.get("dry_run"))

        today = timezone.localdate()  # JST
        if days <= 0:
            date_min = today
        else:
            date_min = today - _timedelta(days=days)

        date_max = today  # future trade_date を拾わない

        qs = VirtualTrade.objects.filter(trade_date__gte=date_min, trade_date__lte=date_max)

        if not force:
            # 未評価（""）＋ carry は毎日再評価対象
            qs = qs.filter(eval_exit_reason__in=["", "carry"])

        qs = qs.order_by("-opened_at", "-id")
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

                # 評価不能系（データ無し等）
                if (not res.ok) and res.reason in ("no_bars", "no_bars_after_active", "no_ts", "no_ohlc", "bad_ts", "no_opened_at", "no_entry"):
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

                # carry / no_position / hit_tp / hit_sl / time_stop がここに来る
                qty_r = v.qty_rakuten or 0
                qty_s = v.qty_sbi or 0
                qty_m = v.qty_matsui or 0

                # pl_per_share は carry の場合 None → 0 として扱う（損益は確定しない）
                pl_per_share = float(res.pl_per_share or 0.0)

                eval_pl_r = pl_per_share * float(qty_r)
                eval_pl_s = pl_per_share * float(qty_s)
                eval_pl_m = pl_per_share * float(qty_m)

                lab_r = _label(qty_r, pl_per_share)
                lab_s = _label(qty_s, pl_per_share)
                lab_m = _label(qty_m, pl_per_share)

                # CLOSED は exit_reason が carry 以外（no_position/hit_tp/hit_sl/time_stop/exception など）
                is_closed = (res.eval_exit_reason or "").strip() not in ("", "carry")

                closed_at = None
                if is_closed:
                    closed_at = res.eval_exit_ts if res.eval_exit_ts is not None else timezone.now()

                ev_true_pro = _ev_true_from_behavior(v.code)

                # replay の last_eval を更新
                replay = v.replay if isinstance(v.replay, dict) else {}
                replay["last_eval"] = {
                    "trade_date": str(v.trade_date),
                    "opened_at": str(_to_local(v.opened_at) or v.opened_at),
                    "active_start_rule": "opened_at_local",
                    "horizon_bd": horizon,
                    "result": res.reason,
                    "entry_px": res.eval_entry_px,
                    "entry_ts": str(res.eval_entry_ts) if res.eval_entry_ts else None,
                    "exit_px": res.eval_exit_px,
                    "exit_ts": str(res.eval_exit_ts) if res.eval_exit_ts else None,
                    "exit_reason": res.eval_exit_reason,
                    "pl_per_share": (pl_per_share if is_closed else None),
                }
                pro = replay.get("pro")
                if isinstance(pro, dict):
                    pro["ev_true_pro"] = ev_true_pro
                    replay["pro"] = pro

                if not dry_run:
                    # entryは更新してOK（未確定→確定、carry→そのまま）
                    if res.eval_entry_px is not None:
                        v.eval_entry_px = res.eval_entry_px
                    if res.eval_entry_ts is not None:
                        v.eval_entry_ts = res.eval_entry_ts

                    # exitはCLOSED時のみ確定値を入れる。carryなら保持/空のまま
                    if is_closed:
                        v.eval_exit_px = res.eval_exit_px
                        v.eval_exit_ts = res.eval_exit_ts

                    v.eval_exit_reason = (res.eval_exit_reason or res.reason or "").strip()

                    v.eval_label_rakuten = lab_r
                    v.eval_label_sbi = lab_s
                    v.eval_label_matsui = lab_m

                    if is_closed:
                        v.eval_pl_rakuten = eval_pl_r
                        v.eval_pl_sbi = eval_pl_s
                        v.eval_pl_matsui = eval_pl_m

                    if is_closed and closed_at is not None:
                        v.closed_at = closed_at

                    v.ev_true_pro = ev_true_pro
                    v.replay = replay

                    # R 再計算（carry/no_position でも entry があるなら計算できる）
                    v.recompute_r()

                    # update_fields を丁寧に
                    fields = [
                        "eval_entry_px", "eval_entry_ts",
                        "eval_exit_reason",
                        "eval_label_rakuten", "eval_label_sbi", "eval_label_matsui",
                        "result_r_rakuten", "result_r_sbi", "result_r_matsui",
                        "ev_true_pro",
                        "replay",
                    ]
                    if is_closed:
                        fields += [
                            "eval_exit_px", "eval_exit_ts",
                            "eval_pl_rakuten", "eval_pl_sbi", "eval_pl_matsui",
                            "closed_at",
                        ]

                    v.save(update_fields=fields)

                    updated += 1
                    touched_run_ids.add(v.run_id)

                    if verbose >= 2:
                        self.stdout.write(
                            f"  ok id={v.id} code={v.code} trade_date={v.trade_date} "
                            f"exit={v.eval_exit_reason} entry_px={v.eval_entry_px} exit_px={v.eval_exit_px}"
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

        # run_id ごとに rank_pro（更新があったrunだけ）
        ranked_rows = 0
        if not dry_run:
            for rid in sorted(touched_run_ids):
                ranked_rows += _rank_within_run(rid)

        self.stdout.write(
            f"[ai_sim_eval] done updated={updated} skipped={skipped} touched_run_ids={len(touched_run_ids)} "
            f"ranked_rows={ranked_rows} dry_run={dry_run}"
        )