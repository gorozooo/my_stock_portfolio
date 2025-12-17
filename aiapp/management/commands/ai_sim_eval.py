# aiapp/management/commands/ai_sim_eval.py
# -*- coding: utf-8 -*-
"""
AI紙シミュ評価（5分足）→ VirtualTrade に eval_* / R を反映

今回の修正（即死修正）：
1) --days 0 が効かずに 5 になる問題を修正（0 を正しく扱う）
2) no_bars_after_active を “no_position でクローズ” に統一（未クローズ溜まり防止）
3) no_bars / no_ts_column / no_ohlc_columns なども “落とさずスキップorクローズ” に寄せる
4) self.verbosity を参照して落ちる問題を修正（options['verbosity'] を使う）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, datetime as _dt, time as _time, timedelta
from typing import Any, Optional, Tuple

import pandas as pd
from django.core.management.base import BaseCommand
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade
from aiapp.services.bars_5m import load_5m_bars


# ========= セッション（JST想定） =========
def _jst_session_range(d: _date) -> Tuple[_dt, _dt]:
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
    return ts.to_pydatetime() if isinstance(ts, pd.Timestamp) else ts


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


def _label_for_pl(qty: int | float | None, pl_per_share: float) -> str:
    try:
        q = float(qty or 0)
    except Exception:
        q = 0.0

    if q <= 0:
        return "no_position"
    if pl_per_share > 0:
        return "win"
    if pl_per_share < 0:
        return "lose"
    return "flat"


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


@dataclass
class EvalResult:
    eval_entry_px: Optional[float]
    eval_entry_ts: Optional[_dt]
    eval_exit_px: Optional[float]
    eval_exit_ts: Optional[_dt]
    eval_exit_reason: str
    pl_per_share: float


def _evaluate_one(v: VirtualTrade) -> EvalResult:
    trade_date = v.trade_date
    session_start, session_end = _jst_session_range(trade_date)

    opened_local = timezone.localtime(v.opened_at)
    active_start = opened_local if opened_local > session_start else session_start

    bars = load_5m_bars(v.code, trade_date)
    if bars is None or len(bars) == 0:
        raise RuntimeError("no_bars")

    df = bars.copy()

    if "ts" not in df.columns:
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index().rename(columns={df.index.name or "index": "ts"})
        else:
            raise RuntimeError("no_ts_column")

    df["ts"] = pd.to_datetime(df["ts"])

    low_col, high_col, close_col = _find_ohlc_columns(df)
    if low_col is None or high_col is None or close_col is None:
        raise RuntimeError("no_ohlc_columns")

    df = df[(df["ts"] >= active_start) & (df["ts"] <= session_end)]
    if len(df) == 0:
        raise RuntimeError("no_bars_after_active")

    entry = _safe_float(v.entry_px)
    tp = _safe_float(v.tp_px)
    sl = _safe_float(v.sl_px)

    if entry is None:
        raise RuntimeError("no_entry")

    hit_mask = (df[low_col] <= entry) & (df[high_col] >= entry)
    if not hit_mask.to_numpy().any():
        return EvalResult(
            eval_entry_px=None,
            eval_entry_ts=None,
            eval_exit_px=None,
            eval_exit_ts=None,
            eval_exit_reason="no_touch_entry",
            pl_per_share=0.0,
        )

    hit_df = df[hit_mask]
    first_hit = hit_df.iloc[0]
    entry_ts = _coerce_ts_scalar(first_hit["ts"], fallback=active_start)
    exec_entry_px = float(entry)

    eval_df = df[df["ts"] >= entry_ts].copy()
    if len(eval_df) == 0:
        return EvalResult(
            eval_entry_px=exec_entry_px,
            eval_entry_ts=entry_ts,
            eval_exit_px=exec_entry_px,
            eval_exit_ts=entry_ts,
            eval_exit_reason="horizon_close",
            pl_per_share=0.0,
        )

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
                reason = "hit_tp"
            else:
                row = eval_df.loc[hit_sl_idx]
                exit_ts = _coerce_ts_scalar(row["ts"], fallback=entry_ts)
                exit_px = float(sl)
                reason = "hit_sl"
        elif hit_tp_idx is not None:
            row = eval_df.loc[hit_tp_idx]
            exit_ts = _coerce_ts_scalar(row["ts"], fallback=entry_ts)
            exit_px = float(tp)
            reason = "hit_tp"
        else:
            row = eval_df.loc[hit_sl_idx]
            exit_ts = _coerce_ts_scalar(row["ts"], fallback=entry_ts)
            exit_px = float(sl)
            reason = "hit_sl"
    else:
        last_row = eval_df.iloc[-1]
        exit_ts = _coerce_ts_scalar(last_row["ts"], fallback=entry_ts)
        exit_px = float(last_row[close_col])
        reason = "horizon_close"

    pl_per_share = float(exit_px) - float(exec_entry_px)

    return EvalResult(
        eval_entry_px=exec_entry_px,
        eval_entry_ts=entry_ts,
        eval_exit_px=float(exit_px),
        eval_exit_ts=exit_ts,
        eval_exit_reason=reason,
        pl_per_share=float(pl_per_share),
    )


class Command(BaseCommand):
    help = "AI紙シミュ評価（5分足）→ VirtualTrade に eval_* / R を反映"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=5,
            help="何日前まで評価対象に含めるか（trade_date基準）。0なら当日だけ。",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="0なら全件。>0なら最大件数（新しい opened_at 優先）",
        )
        parser.add_argument("--force", action="store_true", help="すでに評価済みでも再評価する")
        parser.add_argument("--dry-run", action="store_true", help="DB更新せずログだけ")

    def handle(self, *args, **options):
        # ★ verbosity は self.verbosity ではなく options から取る
        try:
            verbosity = int(options.get("verbosity", 1))
        except Exception:
            verbosity = 1

        days_opt = options.get("days", 5)
        try:
            days = int(days_opt) if days_opt is not None else 5
        except Exception:
            days = 5

        limit_opt = options.get("limit", 0)
        try:
            limit = int(limit_opt) if limit_opt is not None else 0
        except Exception:
            limit = 0

        force = bool(options.get("force"))
        dry_run = bool(options.get("dry_run"))

        today = timezone.localdate()
        date_min = today - timedelta(days=days)

        qs = VirtualTrade.objects.filter(trade_date__gte=date_min)

        if not force:
            qs = qs.filter(eval_exit_ts__isnull=True)

        qs = qs.order_by("-opened_at")
        if limit > 0:
            qs = qs[:limit]

        targets = list(qs)

        self.stdout.write(
            f"[ai_sim_eval] start days={days} date_min={date_min} targets={len(targets)} "
            f"force={force} dry_run={dry_run}"
        )

        updated = 0
        skipped = 0
        touched_trade_dates = set()

        for v in targets:
            touched_trade_dates.add(v.trade_date)

            try:
                res = _evaluate_one(v)
            except Exception as e:
                reason = str(e) or "eval_error"

                # ここは「落とさない」が最優先
                # - no_bars_after_active は no_position close
                # - no_bars / no_ts_column / no_ohlc_columns は “スキップ” が安全（データ無いので評価不能）
                if reason in ("no_bars_after_active",):
                    if not dry_run:
                        v.eval_exit_reason = "no_bars_after_active"
                        v.eval_horizon_days = v.eval_horizon_days or 1

                        v.eval_entry_px = None
                        v.eval_entry_ts = None
                        v.eval_exit_px = None
                        v.eval_exit_ts = None

                        v.eval_label_rakuten = "no_position"
                        v.eval_label_matsui = "no_position"
                        v.eval_label_sbi = "no_position"

                        v.eval_pl_rakuten = 0.0
                        v.eval_pl_matsui = 0.0
                        v.eval_pl_sbi = 0.0

                        v.closed_at = timezone.now()
                        v.recompute_r()
                        v.save(update_fields=[
                            "eval_exit_reason",
                            "eval_horizon_days",
                            "eval_entry_px",
                            "eval_entry_ts",
                            "eval_exit_px",
                            "eval_exit_ts",
                            "eval_label_rakuten",
                            "eval_label_matsui",
                            "eval_label_sbi",
                            "eval_pl_rakuten",
                            "eval_pl_matsui",
                            "eval_pl_sbi",
                            "closed_at",
                            "result_r_rakuten",
                            "result_r_matsui",
                            "result_r_sbi",
                        ])
                    updated += 1
                    continue

                if verbosity >= 2:
                    self.stdout.write(
                        f"  skip id={v.id} code={v.code} trade_date={v.trade_date} reason={reason}"
                    )
                skipped += 1
                continue

            pl_per_share = res.pl_per_share

            if res.eval_exit_reason == "no_touch_entry":
                if not dry_run:
                    v.eval_entry_px = None
                    v.eval_entry_ts = None
                    v.eval_exit_px = None
                    v.eval_exit_ts = None
                    v.eval_exit_reason = "no_touch_entry"
                    v.eval_horizon_days = v.eval_horizon_days or 1

                    v.eval_label_rakuten = "no_position"
                    v.eval_label_matsui = "no_position"
                    v.eval_label_sbi = "no_position"

                    v.eval_pl_rakuten = 0.0
                    v.eval_pl_matsui = 0.0
                    v.eval_pl_sbi = 0.0

                    v.closed_at = timezone.now()
                    v.recompute_r()
                    v.save(update_fields=[
                        "eval_entry_px",
                        "eval_entry_ts",
                        "eval_exit_px",
                        "eval_exit_ts",
                        "eval_exit_reason",
                        "eval_horizon_days",
                        "eval_label_rakuten",
                        "eval_label_matsui",
                        "eval_label_sbi",
                        "eval_pl_rakuten",
                        "eval_pl_matsui",
                        "eval_pl_sbi",
                        "closed_at",
                        "result_r_rakuten",
                        "result_r_matsui",
                        "result_r_sbi",
                    ])
                updated += 1
                continue

            qty_r = int(v.qty_rakuten or 0)
            qty_m = int(v.qty_matsui or 0)
            qty_s = int(v.qty_sbi or 0)

            pl_r = float(pl_per_share) * float(qty_r)
            pl_m = float(pl_per_share) * float(qty_m)
            pl_s = float(pl_per_share) * float(qty_s)

            if not dry_run:
                v.eval_entry_px = res.eval_entry_px
                v.eval_entry_ts = res.eval_entry_ts
                v.eval_exit_px = res.eval_exit_px
                v.eval_exit_ts = res.eval_exit_ts
                v.eval_exit_reason = res.eval_exit_reason
                v.eval_horizon_days = v.eval_horizon_days or 1

                v.eval_label_rakuten = _label_for_pl(qty_r, pl_per_share)
                v.eval_label_matsui = _label_for_pl(qty_m, pl_per_share)
                v.eval_label_sbi = _label_for_pl(qty_s, pl_per_share)

                v.eval_pl_rakuten = pl_r
                v.eval_pl_matsui = pl_m
                v.eval_pl_sbi = pl_s

                v.closed_at = res.eval_exit_ts or timezone.now()

                v.recompute_r()

                v.save(update_fields=[
                    "eval_entry_px",
                    "eval_entry_ts",
                    "eval_exit_px",
                    "eval_exit_ts",
                    "eval_exit_reason",
                    "eval_horizon_days",
                    "eval_label_rakuten",
                    "eval_label_matsui",
                    "eval_label_sbi",
                    "eval_pl_rakuten",
                    "eval_pl_matsui",
                    "eval_pl_sbi",
                    "closed_at",
                    "result_r_rakuten",
                    "result_r_matsui",
                    "result_r_sbi",
                ])

            updated += 1

        self.stdout.write(
            f"[ai_sim_eval] done updated={updated} skipped={skipped} "
            f"touched_trade_dates={len(touched_trade_dates)} dry_run={dry_run}"
        )