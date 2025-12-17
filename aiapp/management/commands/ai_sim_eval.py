# aiapp/management/commands/ai_sim_eval.py
# -*- coding: utf-8 -*-
"""
ai_sim_eval

AI紙シミュ評価（5分足）→ VirtualTrade に eval_* / ラベル / PL / R を反映。

今回の修正ポイント（重要）:
1) self.verbosity を options から先に確定（例外時に落ちない）
2) trade_date が未来日のレコードは評価対象から除外（no_bars の主因）
3) no_bars は例外で全体を落とさず、skip として eval_exit_reason に残す
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, datetime as _dt, time as _time
from typing import Any, Optional, Tuple

import pandas as pd
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade
from aiapp.services.bars_5m import load_5m_bars


# -------------------------
# helpers
# -------------------------
def _jst_session_range(d: _date) -> Tuple[_dt, _dt]:
    """
    その営業日のザラ場時間（仮）：9:00〜15:00 JST
    """
    tz = timezone.get_default_timezone()
    start = timezone.make_aware(_dt.combine(d, _time(9, 0)), tz)
    end = timezone.make_aware(_dt.combine(d, _time(15, 0)), tz)
    return start, end

def _coerce_ts_scalar(val: Any, fallback: _dt) -> _dt:
    if isinstance(val, pd.Series):
        if not val.empty:
            val = val.iloc[0]
        else:
            return fallback
    if isinstance(val, (pd.Timestamp, _dt)):
        ts = pd.Timestamp(val)
    else:
        ts = pd.to_datetime(val, errors="coerce")
    if pd.isna(ts):
        return fallback
    return ts.to_pydatetime()

def _find_ohlc_columns(df: pd.DataFrame) -> Tuple[Optional[Any], Optional[Any], Optional[Any]]:
    low_col = high_col = close_col = None
    for col in df.columns:
        if isinstance(col, tuple):
            parts = [str(p).lower() for p in col if p is not None]
        else:
            parts = [str(col).lower()]
        if low_col is None and any(p == "low" for p in parts):
            low_col = col
        if high_col is None and any(p == "high" for p in parts):
            high_col = col
        if close_col is None and any(p in ("close", "adj close") for p in parts):
            close_col = col
    return low_col, high_col, close_col

def _label_for_side_pl(qty: float, pl_per_share: float) -> str:
    if qty is None or qty <= 0:
        return "no_position"
    if pl_per_share > 0:
        return "win"
    if pl_per_share < 0:
        return "lose"
    return "flat"

def _as_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        f = float(x)
        if f != f:
            return None
        return f
    except Exception:
        return None

def _as_qty(x) -> float:
    try:
        if x is None:
            return 0.0
        return float(x)
    except Exception:
        return 0.0


@dataclass
class EvalResult:
    ok: bool
    reason: str

    eval_entry_px: Optional[float] = None
    eval_entry_ts: Optional[_dt] = None
    eval_exit_px: Optional[float] = None
    eval_exit_ts: Optional[_dt] = None
    eval_exit_reason: str = ""
    eval_horizon_days: Optional[int] = None

    eval_label_r: str = ""
    eval_label_m: str = ""
    eval_label_s: str = ""

    eval_pl_r: Optional[float] = None
    eval_pl_m: Optional[float] = None
    eval_pl_s: Optional[float] = None


def _evaluate_one(v: VirtualTrade) -> EvalResult:
    """
    5分足で:
    - 指値 entry がタッチしたら約定（entry_ts確定）
    - 以降、TP/SL の先着を exit とする
    - どちらもタッチしなければ終値クローズ
    """
    trade_date: _date = v.trade_date
    session_start, session_end = _jst_session_range(trade_date)

    # opened_at がセッション開始より後なら、その時刻以降でのみ評価
    opened_at = timezone.localtime(v.opened_at)
    active_start = opened_at if opened_at > session_start else session_start

    bars = load_5m_bars(v.code, trade_date)
    if bars is None or len(bars) == 0:
        return EvalResult(ok=False, reason="no_bars", eval_exit_reason="no_bars")

    df = bars.copy()

    # ts カラムが無ければ index から復元
    if "ts" not in df.columns:
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index().rename(columns={df.index.name or "index": "ts"})
        else:
            return EvalResult(ok=False, reason="no_ts", eval_exit_reason="no_ts")

    df["ts"] = pd.to_datetime(df["ts"])
    low_col, high_col, close_col = _find_ohlc_columns(df)
    if low_col is None or high_col is None or close_col is None:
        return EvalResult(ok=False, reason="no_ohlc", eval_exit_reason="no_ohlc")

    # 有効時間帯に絞る
    df = df[(df["ts"] >= active_start) & (df["ts"] <= session_end)]
    if len(df) == 0:
        return EvalResult(ok=False, reason="no_bars_after_active", eval_exit_reason="no_bars_after_active")

    entry = _as_float(v.entry_px) if v.entry_px is not None else _as_float(v.last_close)
    tp = _as_float(v.tp_px)
    sl = _as_float(v.sl_px)

    if entry is None:
        return EvalResult(ok=False, reason="no_entry", eval_exit_reason="no_entry")

    # エントリー判定（指値：Low<=entry<=High）
    hit_mask = (df[low_col] <= entry) & (df[high_col] >= entry)
    if not hit_mask.to_numpy().any():
        # その日一度もタッチしなかった → no_position 扱い
        pl_per_share = 0.0
        qty_r = _as_qty(v.qty_rakuten)
        qty_m = _as_qty(v.qty_matsui)
        qty_s = _as_qty(v.qty_sbi)

        return EvalResult(
            ok=True,
            reason="no_touch",
            eval_entry_px=None,
            eval_entry_ts=None,
            eval_exit_px=None,
            eval_exit_ts=None,
            eval_exit_reason="no_touch",
            eval_label_r=_label_for_side_pl(qty_r, pl_per_share),
            eval_label_m=_label_for_side_pl(qty_m, pl_per_share),
            eval_label_s=_label_for_side_pl(qty_s, pl_per_share),
            eval_pl_r=0.0 if qty_r > 0 else 0.0,
            eval_pl_m=0.0 if qty_m > 0 else 0.0,
            eval_pl_s=0.0 if qty_s > 0 else 0.0,
        )

    hit_df = df[hit_mask]
    first_hit = hit_df.iloc[0]
    entry_ts = _coerce_ts_scalar(first_hit["ts"], fallback=active_start)
    exec_entry_px = float(entry)

    # entry_ts 以降
    eval_df = df[df["ts"] >= entry_ts].copy()
    if len(eval_df) == 0:
        exit_ts = entry_ts
        exit_px = exec_entry_px
        exit_reason = "horizon_close"
    else:
        hit_tp_idx = None
        hit_sl_idx = None

        if tp is not None:
            tp_mask = eval_df[high_col] >= tp
            if tp_mask.to_numpy().any():
                hit_tp_idx = eval_df[tp_mask].index[0]

        if sl is not None:
            sl_mask = eval_df[low_col] <= sl
            if sl_mask.to_numpy().any():
                hit_sl_idx = eval_df[sl_mask].index[0]

        if hit_tp_idx is not None or hit_sl_idx is not None:
            if hit_tp_idx is not None and hit_sl_idx is not None:
                if hit_tp_idx <= hit_sl_idx:
                    row = eval_df.loc[hit_tp_idx]
                    exit_ts = _coerce_ts_scalar(row["ts"], fallback=entry_ts)
                    exit_px = float(tp)
                    exit_reason = "hit_tp"
                else:
                    row = eval_df.loc[hit_sl_idx]
                    exit_ts = _coerce_ts_scalar(row["ts"], fallback=entry_ts)
                    exit_px = float(sl)
                    exit_reason = "hit_sl"
            elif hit_tp_idx is not None:
                row = eval_df.loc[hit_tp_idx]
                exit_ts = _coerce_ts_scalar(row["ts"], fallback=entry_ts)
                exit_px = float(tp)
                exit_reason = "hit_tp"
            else:
                row = eval_df.loc[hit_sl_idx]
                exit_ts = _coerce_ts_scalar(row["ts"], fallback=entry_ts)
                exit_px = float(sl)
                exit_reason = "hit_sl"
        else:
            last_row = eval_df.iloc[-1]
            exit_ts = _coerce_ts_scalar(last_row["ts"], fallback=entry_ts)
            exit_px = float(last_row[close_col])
            exit_reason = "horizon_close"

    pl_per_share = float(exit_px) - float(exec_entry_px)

    qty_r = _as_qty(v.qty_rakuten)
    qty_m = _as_qty(v.qty_matsui)
    qty_s = _as_qty(v.qty_sbi)

    # ここは“簡易”として株数×値幅（手数料等は別パイプラインがある前提）
    pl_r = pl_per_share * qty_r
    pl_m = pl_per_share * qty_m
    pl_s = pl_per_share * qty_s

    return EvalResult(
        ok=True,
        reason="ok",
        eval_entry_px=exec_entry_px,
        eval_entry_ts=entry_ts,
        eval_exit_px=float(exit_px),
        eval_exit_ts=exit_ts,
        eval_exit_reason=exit_reason,
        eval_label_r=_label_for_side_pl(qty_r, pl_per_share),
        eval_label_m=_label_for_side_pl(qty_m, pl_per_share),
        eval_label_s=_label_for_side_pl(qty_s, pl_per_share),
        eval_pl_r=float(pl_r),
        eval_pl_m=float(pl_m),
        eval_pl_s=float(pl_s),
    )


class Command(BaseCommand):
    help = "AI紙シミュ評価（5分足）→ VirtualTrade に eval_* / R / 反映"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=5, help="何日前まで評価対象に含めるか（trade_date基準）")
        parser.add_argument("--limit", type=int, default=0, help="0なら全件。>0なら最大件数（新しい opened_at 優先）")
        parser.add_argument("--force", action="store_true", help="評価済みでも再評価する")
        parser.add_argument("--dry-run", action="store_true", help="DB更新せずログだけ")

    def handle(self, *args, **options):
        # ★ここが今回の致命傷修正：例外処理でも参照できるように先に確定
        self.verbosity = int(options.get("verbosity") or 1)

        days = int(options.get("days") or 5)
        limit = int(options.get("limit") or 0)
        force = bool(options.get("force"))
        dry_run = bool(options.get("dry_run"))

        today = timezone.localdate()
        date_min = today if days <= 0 else (today - timezone.timedelta(days=days))

        # ★未来日の trade_date は最初から除外（no_bars の主因）
        qs = VirtualTrade.objects.filter(trade_date__gte=date_min, trade_date__lte=today)

        if not force:
            qs = qs.filter(closed_at__isnull=True)

        qs = qs.order_by("-opened_at")
        if limit and limit > 0:
            qs = qs[:limit]

        targets = qs.count()
        self.stdout.write(
            f"[ai_sim_eval] start days={days} date_min={date_min} date_max={today} targets={targets} force={force} dry_run={dry_run}"
        )

        updated = 0
        skipped = 0
        touched_trade_dates = set()

        for v in qs.iterator():
            try:
                res = _evaluate_one(v)

                if not res.ok:
                    skipped += 1
                    if self.verbosity >= 2:
                        self.stdout.write(
                            f"  skip id={v.id} code={v.code} trade_date={v.trade_date} reason={res.reason}"
                        )
                    if not dry_run:
                        # “落とさず”理由だけ残す
                        v.eval_exit_reason = res.eval_exit_reason or res.reason
                        v.save(update_fields=["eval_exit_reason"])
                    continue

                if dry_run:
                    updated += 1
                    touched_trade_dates.add(v.trade_date)
                    continue

                with transaction.atomic():
                    v.eval_entry_px = res.eval_entry_px
                    v.eval_entry_ts = res.eval_entry_ts
                    v.eval_exit_px = res.eval_exit_px
                    v.eval_exit_ts = res.eval_exit_ts
                    v.eval_exit_reason = res.eval_exit_reason or ""

                    v.eval_label_rakuten = res.eval_label_r or ""
                    v.eval_label_matsui = res.eval_label_m or ""
                    v.eval_label_sbi = res.eval_label_s or ""

                    v.eval_pl_rakuten = res.eval_pl_r
                    v.eval_pl_matsui = res.eval_pl_m
                    v.eval_pl_sbi = res.eval_pl_s

                    # closed_at は「勝敗確定した」扱い（no_touch も確定）
                    v.closed_at = timezone.now()

                    # R 再計算（モデルメソッド）
                    v.recompute_r()

                    v.save()

                updated += 1
                touched_trade_dates.add(v.trade_date)

            except Exception as e:
                # ★絶対に落とさない：ログだけ出して skip
                skipped += 1
                if self.verbosity >= 2:
                    self.stdout.write(
                        f"  skip id={v.id} code={v.code} trade_date={v.trade_date} reason=exception({type(e).__name__})"
                    )
                if not dry_run:
                    try:
                        v.eval_exit_reason = f"exception:{type(e).__name__}"
                        v.save(update_fields=["eval_exit_reason"])
                    except Exception:
                        pass
                continue

        self.stdout.write(
            f"[ai_sim_eval] done updated={updated} skipped={skipped} touched_trade_dates={len(touched_trade_dates)} dry_run={dry_run}"
        )