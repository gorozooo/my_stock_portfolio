# aiapp/management/commands/ai_sim_eval.py
# -*- coding: utf-8 -*-
"""
ai_sim_eval

目的:
- VirtualTrade(OPEN) を 5分足で評価して、eval_* と result_r_* を埋める
- EV_true（-1〜+1にクリップした“真の期待値っぽい指標”）を replay に保存
- trade_date 単位で EV_true のランキング(rank)を replay に保存

ポイント:
- EV_true は「R = PL / |想定損失|」を -1〜+1 にクリップしたもの
- 想定損失(est_loss_*) はあなたのデータだと負で入っている想定（absで分母化）
- Rank は “同じ trade_date の evaluated のみ” で付ける
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, datetime as _dt, time as _time, timedelta
from typing import Any, Dict, Optional, Tuple, List

import pandas as pd

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade

# 5分足ローダ（既存の仕組みを再利用）
try:
    from aiapp.services.bars_5m import load_5m_bars
except Exception:  # pragma: no cover
    load_5m_bars = None  # type: ignore


# =========================================================
# 小ユーティリティ
# =========================================================
def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        f = float(x)
        if f != f:  # NaN
            return None
        return f
    except Exception:
        return None


def _safe_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


def _clip(x: Optional[float], lo: float, hi: float) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
        if v != v:
            return None
        if v < lo:
            return lo
        if v > hi:
            return hi
        return v
    except Exception:
        return None


def _jst_session_range(d: _date) -> Tuple[_dt, _dt]:
    """
    その営業日のザラ場時間（簡易）: 9:00〜15:00 JST
    """
    tz = timezone.get_default_timezone()
    start = timezone.make_aware(_dt.combine(d, _time(9, 0)), tz)
    end = timezone.make_aware(_dt.combine(d, _time(15, 0)), tz)
    return start, end


def _label_for_side_pl(qty: float, pl_per_share: float) -> str:
    """
    ラベル:
      - qty<=0 → no_position
      - pl>0   → win
      - pl<0   → lose
      - else   → flat
    """
    if qty is None or qty <= 0:
        return "no_position"
    if pl_per_share > 0:
        return "win"
    if pl_per_share < 0:
        return "lose"
    return "flat"


def _coerce_ts_scalar(val: Any, fallback: _dt) -> _dt:
    """
    row["ts"] から安全に Timestamp を取り出す。
    Series でも datetime でも潰して 1個にする。NaT は fallback。
    """
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
    # pandas Timestamp -> python datetime
    return ts.to_pydatetime()


def _find_ohlc_columns(df: pd.DataFrame) -> Tuple[Optional[Any], Optional[Any], Optional[Any]]:
    """
    df.columns が str でも MultiIndex でも、
    'Low' / 'High' / 'Close'(or 'Adj Close') を拾う。
    戻り値は (low_col, high_col, close_col) で df[...] にそのまま使える実カラム。
    """
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


# =========================================================
# 評価コア
# =========================================================
@dataclass
class EvalResult:
    status: str  # evaluated / no_bars / no_entry / error
    eval_entry_px: Optional[float]
    eval_entry_ts: Optional[_dt]
    eval_exit_px: Optional[float]
    eval_exit_ts: Optional[_dt]
    eval_exit_reason: str  # hit_tp / hit_sl / horizon_close / no_entry / no_bars / error
    pl_per_share: Optional[float]


def _evaluate_one_trade(vt: VirtualTrade) -> EvalResult:
    """
    5分足で以下の簡易ルール評価:
    - 指値 entry_px が Low<=entry<=High で初回タッチしたら約定
    - TP/SL が先にタッチした方で exit、無ければ終値クローズ
    """
    try:
        if load_5m_bars is None:
            return EvalResult(
                status="error",
                eval_entry_px=None,
                eval_entry_ts=None,
                eval_exit_px=None,
                eval_exit_ts=None,
                eval_exit_reason="error_no_loader",
                pl_per_share=None,
            )

        trade_date = vt.trade_date
        entry = _safe_float(vt.entry_px)
        tp = _safe_float(vt.tp_px)
        sl = _safe_float(vt.sl_px)

        if entry is None:
            return EvalResult(
                status="error",
                eval_entry_px=None,
                eval_entry_ts=None,
                eval_exit_px=None,
                eval_exit_ts=None,
                eval_exit_reason="error_no_entry",
                pl_per_share=None,
            )

        bars = load_5m_bars(vt.code, trade_date)
        if bars is None or len(bars) == 0:
            return EvalResult(
                status="no_bars",
                eval_entry_px=None,
                eval_entry_ts=None,
                eval_exit_px=None,
                eval_exit_ts=None,
                eval_exit_reason="no_bars",
                pl_per_share=None,
            )

        df = bars.copy()

        # ts カラムが無ければ index から復元
        if "ts" not in df.columns:
            if isinstance(df.index, pd.DatetimeIndex):
                df = df.reset_index().rename(columns={df.index.name or "index": "ts"})
            else:
                return EvalResult(
                    status="error",
                    eval_entry_px=None,
                    eval_entry_ts=None,
                    eval_exit_px=None,
                    eval_exit_ts=None,
                    eval_exit_reason="error_no_ts",
                    pl_per_share=None,
                )

        df["ts"] = pd.to_datetime(df["ts"])

        low_col, high_col, close_col = _find_ohlc_columns(df)
        if low_col is None or high_col is None or close_col is None:
            return EvalResult(
                status="error",
                eval_entry_px=None,
                eval_entry_ts=None,
                eval_exit_px=None,
                eval_exit_ts=None,
                eval_exit_reason="error_no_ohlc",
                pl_per_share=None,
            )

        session_start, session_end = _jst_session_range(trade_date)

        # 有効開始: opened_at がザラ場より後なら opened_at、そうでなければ 9:00
        opened_at = vt.opened_at
        if timezone.is_naive(opened_at):
            opened_at = timezone.make_aware(opened_at, timezone.get_default_timezone())
        opened_at = timezone.localtime(opened_at)

        active_start = opened_at if opened_at > session_start else session_start

        # 有効バー
        df = df[(df["ts"] >= active_start) & (df["ts"] <= session_end)]
        if df.empty:
            return EvalResult(
                status="no_bars",
                eval_entry_px=None,
                eval_entry_ts=None,
                eval_exit_px=None,
                eval_exit_ts=None,
                eval_exit_reason="no_bars_after_active",
                pl_per_share=None,
            )

        # エントリー判定
        hit_mask = (df[low_col] <= entry) & (df[high_col] >= entry)
        if not hit_mask.to_numpy().any():
            return EvalResult(
                status="no_entry",
                eval_entry_px=None,
                eval_entry_ts=None,
                eval_exit_px=None,
                eval_exit_ts=None,
                eval_exit_reason="no_entry",
                pl_per_share=0.0,
            )

        hit_df = df[hit_mask]
        if hit_df.empty:
            return EvalResult(
                status="no_entry",
                eval_entry_px=None,
                eval_entry_ts=None,
                eval_exit_px=None,
                eval_exit_ts=None,
                eval_exit_reason="no_entry",
                pl_per_share=0.0,
            )

        first_hit = hit_df.iloc[0]
        entry_ts = _coerce_ts_scalar(first_hit["ts"], fallback=active_start)
        exec_entry_px = float(entry)

        # exit 判定（entry_ts以降）
        eval_df = df[df["ts"] >= entry_ts].copy()
        if eval_df.empty:
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

        return EvalResult(
            status="evaluated",
            eval_entry_px=exec_entry_px,
            eval_entry_ts=entry_ts,
            eval_exit_px=float(exit_px),
            eval_exit_ts=exit_ts,
            eval_exit_reason=exit_reason,
            pl_per_share=pl_per_share,
        )

    except Exception:
        return EvalResult(
            status="error",
            eval_entry_px=None,
            eval_entry_ts=None,
            eval_exit_px=None,
            eval_exit_ts=None,
            eval_exit_reason="error_exception",
            pl_per_share=None,
        )


def _compute_ev_true_from_r(r: Optional[float]) -> Optional[float]:
    # -1〜+1 にクリップ（あなたが理解したやつ）
    return _clip(r, -1.0, 1.0)


# =========================================================
# ランキング付け（trade_date 単位）
# =========================================================
def _assign_ranks_for_trade_date(trade_date: _date) -> int:
    """
    trade_date の evaluated のみ対象に、
    EV_true（R/M/S）をそれぞれ降順に並べて順位を replay に保存する。

    保存先:
      replay["ev_true"] = {"rakuten": x, "matsui": y, "sbi": z}
      replay["rank"]    = {"rakuten": 1, "matsui": 3, "sbi": 2}
    """
    qs = VirtualTrade.objects.filter(trade_date=trade_date)

    rows: List[VirtualTrade] = list(qs)
    if not rows:
        return 0

    # EV_true を取り出す（無いものは除外）
    def get_ev(vt: VirtualTrade, key: str) -> Optional[float]:
        try:
            ev = (vt.replay or {}).get("ev_true", {}).get(key)
            return _safe_float(ev)
        except Exception:
            return None

    # evaluated 判定は replay["eval_status"] == "evaluated" を優先、無ければ eval_exit_reason で判定
    def is_evaluated(vt: VirtualTrade) -> bool:
        try:
            st = (vt.replay or {}).get("eval_status")
            if st:
                return str(st) == "evaluated"
        except Exception:
            pass
        return bool(vt.eval_exit_reason) and (vt.eval_exit_reason not in ("", "no_bars", "error"))

    evaluated = [vt for vt in rows if is_evaluated(vt)]
    if not evaluated:
        return 0

    def apply_rank(key: str) -> None:
        scored = []
        for vt in evaluated:
            ev = get_ev(vt, key)
            if ev is None:
                continue
            scored.append((vt, ev))

        # 降順（同値は同順位でも良いが、ここは単純に順番で振る）
        scored.sort(key=lambda x: x[1], reverse=True)

        for i, (vt, _ev) in enumerate(scored, start=1):
            rp = vt.replay or {}
            rp_rank = rp.get("rank") or {}
            if not isinstance(rp_rank, dict):
                rp_rank = {}
            rp_rank[key] = i
            rp["rank"] = rp_rank
            vt.replay = rp

    apply_rank("rakuten")
    apply_rank("matsui")
    apply_rank("sbi")

    # bulk更新
    VirtualTrade.objects.bulk_update(evaluated, ["replay"])
    return len(evaluated)


# =========================================================
# コマンド
# =========================================================
class Command(BaseCommand):
    help = "AI紙シミュ評価（5分足）→ VirtualTrade に eval_* / R / EV_true / Rank を反映"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=5, help="何日前まで評価対象に含めるか（trade_date基準）")
        parser.add_argument("--limit", type=int, default=0, help="0なら全件。>0なら最大件数（新しいopened_at優先）")
        parser.add_argument("--force", action="store_true", help="すでに評価済みでも再評価する")
        parser.add_argument("--dry-run", action="store_true", help="DB更新せずログだけ")

    def handle(self, *args, **opts) -> None:
        days = int(opts.get("days") or 5)
        limit = int(opts.get("limit") or 0)
        force = bool(opts.get("force"))
        dry = bool(opts.get("dry_run"))

        today = timezone.localdate()
        date_min = today - timedelta(days=days)

        qs = VirtualTrade.objects.filter(trade_date__gte=date_min).order_by("-opened_at")

        if limit and limit > 0:
            qs = qs[:limit]

        targets: List[VirtualTrade] = list(qs)

        self.stdout.write(
            f"[ai_sim_eval] start days={days} date_min={date_min} targets={len(targets)} force={force} dry_run={dry}"
        )

        updated = 0
        skipped = 0

        # trade_date ごとに rank を振りたいので、後でまとめる
        touched_trade_dates: set[_date] = set()

        for vt in targets:
            try:
                # すでに評価済みを飛ばす（forceなら再評価）
                already = bool(vt.eval_exit_reason) and (vt.eval_exit_reason not in ("", "no_bars", "error"))
                if already and not force:
                    skipped += 1
                    continue

                res = _evaluate_one_trade(vt)

                # qty
                qr = float(vt.qty_rakuten or 0)
                qm = float(vt.qty_matsui or 0)
                qsbi = float(vt.qty_sbi or 0)

                # qtyが全部0なら skip 扱い（＝評価しても意味なし）
                qty_any = (qr > 0) or (qm > 0) or (qsbi > 0)

                # PL（share→口座）
                plps = _safe_float(res.pl_per_share)
                pl_r = (plps * qr) if (plps is not None and qr > 0) else 0.0
                pl_m = (plps * qm) if (plps is not None and qm > 0) else 0.0
                pl_s = (plps * qsbi) if (plps is not None and qsbi > 0) else 0.0

                # ラベル（口座ごと）
                label_r = _label_for_side_pl(qr, plps or 0.0)
                label_m = _label_for_side_pl(qm, plps or 0.0)
                label_s = _label_for_side_pl(qsbi, plps or 0.0)

                # eval_* をセット
                vt.eval_entry_px = res.eval_entry_px
                vt.eval_entry_ts = res.eval_entry_ts
                vt.eval_exit_px = res.eval_exit_px
                vt.eval_exit_ts = res.eval_exit_ts
                vt.eval_exit_reason = res.eval_exit_reason
                vt.eval_horizon_days = None  # 将来拡張用

                vt.eval_label_rakuten = label_r
                vt.eval_label_matsui = label_m
                vt.eval_label_sbi = label_s

                # 口座PL（現状は単純計算）
                vt.eval_pl_rakuten = float(pl_r) if qty_any else 0.0
                vt.eval_pl_matsui = float(pl_m) if qty_any else 0.0
                vt.eval_pl_sbi = float(pl_s) if qty_any else 0.0

                # R再計算（モデルメソッド）
                vt.recompute_r()

                # EV_true（replayへ）
                ev_r = _compute_ev_true_from_r(_safe_float(vt.result_r_rakuten))
                ev_m = _compute_ev_true_from_r(_safe_float(vt.result_r_matsui))
                ev_s = _compute_ev_true_from_r(_safe_float(vt.result_r_sbi))

                rp = vt.replay or {}
                rp["eval_status"] = "evaluated" if (res.status == "evaluated" and qty_any) else (
                    "skip" if (not qty_any) else res.status
                )
                rp["evaluated_at"] = timezone.now().isoformat()

                rp_ev = rp.get("ev_true") or {}
                if not isinstance(rp_ev, dict):
                    rp_ev = {}
                rp_ev["rakuten"] = ev_r
                rp_ev["matsui"] = ev_m
                rp_ev["sbi"] = ev_s
                rp["ev_true"] = rp_ev

                vt.replay = rp

                touched_trade_dates.add(vt.trade_date)

                if not dry:
                    vt.save(update_fields=[
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
                        "result_r_rakuten",
                        "result_r_matsui",
                        "result_r_sbi",
                        "replay",
                    ])

                updated += 1

            except Exception:
                skipped += 1
                continue

        # rank を trade_date ごとに付与
        ranked = 0
        if touched_trade_dates and not dry:
            for td in sorted(touched_trade_dates):
                try:
                    ranked += _assign_ranks_for_trade_date(td)
                except Exception:
                    continue

        self.stdout.write(
            self.style.SUCCESS(
                f"[ai_sim_eval] done updated={updated} skipped={skipped} "
                f"touched_trade_dates={len(touched_trade_dates)} ranked_rows={ranked} dry_run={dry}"
            )
        )