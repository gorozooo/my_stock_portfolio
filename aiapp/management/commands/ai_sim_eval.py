# aiapp/management/commands/ai_sim_eval.py
# -*- coding: utf-8 -*-
"""
ai_sim_eval

AI紙シミュ評価（5分足）→ VirtualTrade に eval_* / R / EV_true_pro / rank_pro を反映

重要方針（あなたのルール）：
- 評価開始時刻は固定しない
- opened_at（注文を作った時刻＝現実世界で注文を出した時刻）から評価開始
- ただし時刻比較は必ず tz を揃える（DBはUTC保持、5分足の ts はJST）

既存の修正点：
1) 対象抽出を trade_date 基準に統一（--days 0 なら trade_date=今日だけ）
2) 評価開始 = opened_at を JST に localtime した上で 5分足 ts と比較
3) 例外時ログの self.verbosity AttributeError を潰す（options['verbosity'] 参照）
4) options.get("days") が 0 のとき `or 5` で 5 に化ける事故を防ぐ
5) df["ts"] の tz を必ず Asia/Tokyo に寄せて比較する（UTC/naive混在で no_bars を防ぐ）

今回の追加修正（9020の件の本丸）：
- BUYの指値は「上限価格」なので、寄り/直後の open が entry 以下なら entry 到達を待たずに open で約定する（marketable limit）
- SELLの指値は「下限価格」なので、寄り/直後の open が entry 以上なら open で約定する

今回の追加修正（プロ仕様：評価期間=3営業日、carryを毎日評価）：
- 評価期間は --horizon（デフォルト 3）営業日（休日=休場日）で進める
  - 可能なら pandas_market_calendars（JPX）で営業日判定
  - 無ければ土日除外の簡易営業日（最低限）
- entry は「起票日（trade_date）当日のみ」評価する
  - 当日中に刺さらなければ CLOSED（exit_reason=no_position）
- entry が刺さったら、TP/SLにヒットした時点で即 CLOSED
- 最終営業日（horizon最終日）の 15:30 最後の足で未達なら強制CLOSED（exit_reason=time_stop）
- carry（途中状態）は CLOSED になるまで毎日評価対象に残すため、
  「time_stop / hit_tp / hit_sl / no_position」以外（horizon_close等）では閉じない
  ※この実装では horizon_close は使わず、最終日は time_stop に統一
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, datetime as _dt, time as _time, timedelta as _timedelta
from typing import Any, Dict, List, Optional, Tuple

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
    load_5m_bars の実装やデータ形状で tz がブレても、ここで吸収する。
    """
    try:
        import pandas as pd
    except Exception:
        return None

    # ts が MultiIndex/tuple のケースもあるので、列存在判定は「完全一致」前提
    if "ts" not in df.columns:
        # ただし load_5m_bars の実績だと ('ts','') の MultiIndex があり得るので拾う
        for c in df.columns:
            if isinstance(c, tuple) and len(c) >= 1 and str(c[0]).lower() == "ts":
                df = df.rename(columns={c: "ts"})
                break

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


def _trading_days_jpx(start_day: _date, n: int) -> List[_date]:
    """
    start_day を含めて n営業日ぶん返す。

    優先：pandas_market_calendars の JPX
    無ければ：土日除外（簡易）
    """
    if n <= 0:
        return [start_day]

    # まずは JPX カレンダーが使えればそれを使う
    try:
        import pandas as pd
        import pandas_market_calendars as mcal  # type: ignore

        cal = mcal.get_calendar("JPX")
        # start_day から少し余裕を持って先までスケジュール取得
        end_guess = start_day + _timedelta(days=40)
        sched = cal.schedule(start_date=start_day, end_date=end_guess)

        # sched.index は Timestamp(UTC) になりがちなので date に落とす
        days: List[_date] = []
        for ts in list(sched.index):
            try:
                d = ts.to_pydatetime().date()
            except Exception:
                d = pd.Timestamp(ts).to_pydatetime().date()
            if d >= start_day:
                days.append(d)
            if len(days) >= n:
                break

        if len(days) >= n:
            return days[:n]
    except Exception:
        pass

    # フォールバック：土日を飛ばす
    days2: List[_date] = []
    d = start_day
    while len(days2) < n:
        if d.weekday() < 5:  # Mon=0..Fri=4
            days2.append(d)
        d = d + _timedelta(days=1)
        # 念のため無限ループ回避
        if len(days2) == 0 and (d - start_day).days > 60:
            break
    return days2[:n] if days2 else [start_day]


# ==============================
# result container
# ==============================

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

def _evaluate_entry_on_trade_date(
    v: VirtualTrade,
    *,
    trade_date: _date,
    active_start: _dt,
    session_end: _dt,
    entry: float,
    side: str,
    verbose: int = 1,
) -> Tuple[Optional[float], Optional[_dt], Optional[str]]:
    """
    entry は trade_date 当日のみ。
    marketable limit（寄りギャップ）もここで処理する。

    Returns:
      (exec_entry_px, entry_ts, fail_reason)
    """
    bars = load_5m_bars(v.code, trade_date)
    if bars is None or len(bars) == 0:
        return None, None, "no_bars"

    df = bars.copy()

    # ts が無い場合は index から復元
    if "ts" not in df.columns:
        try:
            import pandas as pd
            if isinstance(df.index, pd.DatetimeIndex):
                df = df.reset_index().rename(columns={df.index.name or "index": "ts"})
            else:
                return None, None, "no_ts"
        except Exception:
            return None, None, "no_ts"

    df2 = _ensure_ts_jst(df)
    if df2 is None:
        return None, None, "bad_ts"
    df = df2

    open_col, low_col, high_col, close_col = _find_ohlc_columns(df)
    if low_col is None or high_col is None or close_col is None:
        return None, None, "no_ohlc"

    # open が取れない形なら marketable limit を諦めて通常判定へ
    # （ただ、あなたの load_5m_bars 実データでは open は取れてる）
    df_eff = df[(df["ts"] >= active_start) & (df["ts"] <= session_end)]
    if df_eff is None or len(df_eff) == 0:
        return None, None, "no_bars_after_active"

    exec_entry_px: Optional[float] = None
    entry_ts: Optional[_dt] = None

    for _, row in df_eff.iterrows():
        bar_ts = _coerce_ts(row["ts"], fallback=active_start)

        o = None
        if open_col is not None:
            try:
                o = _safe_float(row[open_col])
            except Exception:
                o = None

        lo = hi = None
        try:
            lo = _safe_float(row[low_col])
            hi = _safe_float(row[high_col])
        except Exception:
            lo = hi = None

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
        # 当日中に刺さらなかった
        return None, None, None

    return exec_entry_px, entry_ts, None


def _evaluate_exit_across_horizon(
(
    v: VirtualTrade,
    *,
    trading_days: List[_date],
    entry_ts: _dt,
    exec_entry_px: float,
    tp: Optional[float],
    sl: Optional[float],
    side: str,
    verbose: int = 1,
) -> Tuple[float, _dt, str]:
    """
    entry 後、trading_days の範囲で TP/SL を探索。
    最終営業日まで未達なら最終日の最後の足で強制CLOSE（time_stop）。
    """
    # BUY前提のTP/SL（SELLの逆ロジックが必要になったらここで分岐）
    # 今はあなたの運用が基本 BUY なので、そのまま。

    last_close_px: Optional[float] = None
    last_close_ts: Optional[_dt] = None

    for i, d in enumerate(trading_days):
        session_start, session_end = _jst_session_range(d)

        bars = load_5m_bars(v.code, d)
        if bars is None or len(bars) == 0:
            # データ欠損：その日はスキップ（ただし最終日なら time_stop が作れないので例外扱いへ寄せる）
            if i == len(trading_days) - 1:
                # 最終日データがないのは評価不能
                raise RuntimeError("no_bars_on_last_day")
            continue

        df = bars.copy()

        if "ts" not in df.columns:
            try:
                import pandas as pd
                if isinstance(df.index, pd.DatetimeIndex):
                    df = df.reset_index().rename(columns={df.index.name or "index": "ts"})
                else:
                    if i == len(trading_days) - 1:
                        raise RuntimeError("no_ts_on_last_day")
                    continue
            except Exception:
                if i == len(trading_days) - 1:
                    raise RuntimeError("no_ts_on_last_day")
                continue

        df2 = _ensure_ts_jst(df)
        if df2 is None:
            if i == len(trading_days) - 1:
                raise RuntimeError("bad_ts_on_last_day")
            continue
        df = df2

        open_col, low_col, high_col, close_col = _find_ohlc_columns(df)
        if low_col is None or high_col is None or close_col is None:
            if i == len(trading_days) - 1:
                raise RuntimeError("no_ohlc_on_last_day")
            continue

        # entry 当日だけは entry_ts 以降、翌日以降はセッション全体
        if d == entry_ts.date():
            day_start = max(session_start, entry_ts)
        else:
            day_start = session_start

        df_eff = df[(df["ts"] >= day_start) & (df["ts"] <= session_end)]
        if df_eff is None or len(df_eff) == 0:
            if i == len(trading_days) - 1:
                # 最終日に評価範囲が空なら time_stop が作れない
                raise RuntimeError("no_bars_eff_on_last_day")
            continue

        # 最終クローズ候補（time_stop用）を更新
        try:
            last_row = df_eff.iloc[-1]
            last_close_px = float(last_row[close_col])
            last_close_ts = _coerce_ts(last_row["ts"], fallback=session_end)
        except Exception:
            pass

        hit_tp_idx = None
        hit_sl_idx = None

        if tp is not None:
            tp_mask = df_eff[high_col] >= float(tp)
            if tp_mask.to_numpy().any():
                hit_tp_idx = df_eff[tp_mask].index[0]

        if sl is not None:
            sl_mask = df_eff[low_col] <= float(sl)
            if sl_mask.to_numpy().any():
                hit_sl_idx = df_eff[sl_mask].index[0]

        if hit_tp_idx is not None or hit_sl_idx is not None:
            # 同一日（同一df_eff）内の最初到達で確定
            if hit_tp_idx is not None and hit_sl_idx is not None:
                # 同じ足で同時到達（index同一）の場合は TP 優先にする（あなたの既存ロジック踏襲）
                if hit_tp_idx <= hit_sl_idx:
                    row2 = df_eff.loc[hit_tp_idx]
                    exit_ts = _coerce_ts(row2["ts"], fallback=entry_ts)
                    exit_px = float(tp)
                    return exit_px, exit_ts, "hit_tp"
                row2 = df_eff.loc[hit_sl_idx]
                exit_ts = _coerce_ts(row2["ts"], fallback=entry_ts)
                exit_px = float(sl)
                return exit_px, exit_ts, "hit_sl"

            if hit_tp_idx is not None:
                row2 = df_eff.loc[hit_tp_idx]
                exit_ts = _coerce_ts(row2["ts"], fallback=entry_ts)
                exit_px = float(tp)
                return exit_px, exit_ts, "hit_tp"

            row2 = df_eff.loc[hit_sl_idx]
            exit_ts = _coerce_ts(row2["ts"], fallback=entry_ts)
            exit_px = float(sl)
            return exit_px, exit_ts, "hit_sl"

        # 未ヒットなら次の営業日へ（carry）
        continue

    # 最終営業日まで未達 → time_stop
    if last_close_px is None or last_close_ts is None:
        # ここに来るのは最終日データ欠損など
        raise RuntimeError("no_time_stop_close")
    return float(last_close_px), last_close_ts, "time_stop"


def _evaluate_one(v: VirtualTrade, *, horizon: int = 3, verbose: int = 1) -> EvalResult:
    """
    3営業日評価（デフォルト）：
    - entry は trade_date 当日のみ（opened_at以降）
    - TP/SLに当たれば即CLOSED
    - 最終営業日まで未達なら time_stop でCLOSED
    - entry が当日刺さらなければ no_position でCLOSED
    """
    trade_date = v.trade_date
    if trade_date is None:
        return EvalResult(ok=False, reason="no_trade_date")

    # opened_at（JST）
    opened_local = _to_local(v.opened_at)
    if opened_local is None:
        return EvalResult(ok=False, reason="no_opened_at")

    # trade_date 当日のセッション
    session_start, session_end = _jst_session_range(trade_date)

    # 評価開始=opened_at（場外なら丸め）
    if opened_local < session_start:
        active_start = session_start
    elif opened_local > session_end:
        # 当日場が終わってから起票 → 当日エントリー不可 → no_position扱い（CLOSED）
        return EvalResult(
            ok=True,
            reason="no_position",
            eval_exit_reason="no_position",
            pl_per_share=0.0,
        )
    else:
        active_start = opened_local

    # パラメータ
    entry = _safe_float(v.entry_px) or _safe_float((v.replay or {}).get("sim_order", {}).get("entry"))
    tp = _safe_float(v.tp_px) or _safe_float((v.replay or {}).get("sim_order", {}).get("tp"))
    sl = _safe_float(v.sl_px) or _safe_float((v.replay or {}).get("sim_order", {}).get("sl"))

    if entry is None:
        return EvalResult(ok=False, reason="no_entry")

    side = str(getattr(v, "side", None) or "BUY").upper().strip()
    if side not in ("BUY", "SELL"):
        side = "BUY"

    # === entry は trade_date 当日のみ ===
    exec_entry_px, entry_ts, fail_reason = _evaluate_entry_on_trade_date(
        v,
        trade_date=trade_date,
        active_start=active_start,
        session_end=session_end,
        entry=float(entry),
        side=side,
        verbose=verbose,
    )
    if fail_reason is not None:
        # 5分足が取れない等は「評価不能系」
        return EvalResult(ok=False, reason=fail_reason)

    if exec_entry_px is None or entry_ts is None:
        # 当日刺さらない → CLOSED(no_position)
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

    # === exit は horizon 営業日（trade_date含む）で評価 ===
    trading_days = _trading_days_jpx(trade_date, horizon)

    try:
        exit_px, exit_ts, exit_reason = _evaluate_exit_across_horizon(
            v,
            trading_days=trading_days,
            entry_ts=entry_ts,
            exec_entry_px=float(exec_entry_px),
            tp=tp,
            sl=sl,
            side=side,
            verbose=verbose,
        )
    except Exception:
        # 最終日データ欠損など → 評価不能
        return EvalResult(ok=False, reason="exception")

    pl_per_share = float(exit_px) - float(exec_entry_px)

    return EvalResult(
        ok=True,
        reason="ok",
        eval_entry_px=float(exec_entry_px),
        eval_entry_ts=entry_ts,
        eval_exit_px=float(exit_px),
        eval_exit_ts=exit_ts,
        eval_exit_reason=exit_reason,
        pl_per_share=pl_per_share,
    )


def _ev_true_from_behavior(code: str) -> float:
    """
    A案（全期間）：
    - BehaviorStats の all/all を代表として EV_true_pro に使う
    - win_rate を 0〜1 に正規化して “期待値っぽい指標” として扱う
    """
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
    """
    1 run_id 内で ev_true_pro 降順に rank_pro を振る
    """
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
        parser.add_argument("--days", type=int, default=5, help="何日前まで評価対象に含めるか（trade_date基準）")
        parser.add_argument("--horizon", type=int, default=3, help="評価期間：何営業日（trade_date含む）")
        parser.add_argument("--limit", type=int, default=0, help="0なら全件。>0なら最大件数（新しい opened_at 優先）")
        parser.add_argument("--force", action="store_true", help="すでに評価済みでも再評価する")
        parser.add_argument("--dry-run", action="store_true", help="DB更新せずログだけ")

    def handle(self, *args, **options):
        # verbosity は options から必ず拾う（self.verbosity が無いケース対策）
        verbose = int(options.get("verbosity", 1) or 1)

        # ★ 0 を False 扱いして 5 に化ける事故を防ぐ
        days_opt = options.get("days", None)
        days = 5 if days_opt is None else int(days_opt)

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

        date_max = today  # ★ future trade_date を拾わない

        # 対象抽出は trade_date 基準に統一
        qs = VirtualTrade.objects.filter(trade_date__gte=date_min, trade_date__lte=date_max)

        # 「評価済み」の判定：
        # - eval_exit_reason が空なら未評価
        # - hit_tp / hit_sl / time_stop / no_position / (no_bars系の理由) が入っていれば評価済み
        if not force:
            qs = qs.filter(eval_exit_reason="")

        qs = qs.order_by("-opened_at", "-id")
        if limit and limit > 0:
            qs = qs[:limit]

        targets = list(qs)

        self.stdout.write(
            f"[ai_sim_eval] start days={days} date_min={date_min} date_max={date_max} "
            f"horizon={horizon} targets={len(targets)} force={force} dry_run={dry_run}"
        )

        updated = 0
        skipped = 0
        touched_run_ids: set[str] = set()

        for v in targets:
            try:
                res = _evaluate_one(v, horizon=horizon, verbose=verbose)

                # 評価不能系（no_bars等）は “skipped扱いで理由だけ刻む”
                if (not res.ok) and res.reason in (
                    "no_bars", "no_bars_after_active", "no_ts", "no_ohlc", "bad_ts",
                    "no_opened_at", "no_entry", "no_trade_date", "exception",
                ):
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

                # no_position / hit_tp / hit_sl / time_stop は “評価として成功” 扱い
                pl_per_share = float(res.pl_per_share or 0.0)

                qty_r = v.qty_rakuten or 0
                qty_s = v.qty_sbi or 0
                qty_m = v.qty_matsui or 0

                eval_pl_r = pl_per_share * float(qty_r)
                eval_pl_s = pl_per_share * float(qty_s)
                eval_pl_m = pl_per_share * float(qty_m)

                lab_r = _label(qty_r, pl_per_share)
                lab_s = _label(qty_s, pl_per_share)
                lab_m = _label(qty_m, pl_per_share)

                # closed_at は “評価でexitが決まった” ときのみ入れる
                closed_at = res.eval_exit_ts if res.eval_exit_ts is not None else timezone.now()

                # EV_true_pro（A案: all/all を代表）
                ev_true_pro = _ev_true_from_behavior(v.code)

                # replay の last_eval を更新
                replay = v.replay if isinstance(v.replay, dict) else {}
                replay["last_eval"] = {
                    "trade_date": str(v.trade_date),
                    "opened_at": str(_to_local(v.opened_at) or v.opened_at),
                    "active_start_rule": "opened_at_local",
                    "horizon_business_days": horizon,
                    "result": res.reason,
                    "entry_px": res.eval_entry_px,
                    "entry_ts": str(res.eval_entry_ts) if res.eval_entry_ts else None,
                    "exit_px": res.eval_exit_px,
                    "exit_ts": str(res.eval_exit_ts) if res.eval_exit_ts else None,
                    "exit_reason": res.eval_exit_reason,
                    "pl_per_share": pl_per_share,
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
                    v.eval_exit_reason = res.eval_exit_reason or res.reason

                    v.eval_label_rakuten = lab_r
                    v.eval_label_sbi = lab_s
                    v.eval_label_matsui = lab_m

                    v.eval_pl_rakuten = eval_pl_r
                    v.eval_pl_sbi = eval_pl_s
                    v.eval_pl_matsui = eval_pl_m

                    v.closed_at = closed_at
                    v.ev_true_pro = ev_true_pro
                    v.replay = replay

                    # R 再計算（est_loss を分母）
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

        # run_id ごとに rank_pro
        ranked_rows = 0
        if not dry_run:
            for rid in sorted(touched_run_ids):
                ranked_rows += _rank_within_run(rid)

        self.stdout.write(
            f"[ai_sim_eval] done updated={updated} skipped={skipped} touched_run_ids={len(touched_run_ids)} "
            f"ranked_rows={ranked_rows} dry_run={dry_run}"
        )