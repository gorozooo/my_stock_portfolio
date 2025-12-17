# aiapp/management/commands/ai_sim_eval.py
# -*- coding: utf-8 -*-
"""
ai_sim_eval

AI紙シミュ評価（5分足）→ VirtualTrade に eval_* / R / EV_true / Rank を反映

今回の修正ポイント（あなたの方針どおり）:
- 評価開始時刻を「固定しない」
  → opened_at（＝現実の注文を出した時刻）を基準に、その直後から評価開始
  → ただし “現実に約定し得ない時間帯” は市場ルールに沿って次の有効開始へ丸める
    * 09:00前        → 当日09:00へ
    * 11:30〜12:30   → 当日12:30へ（後場開始）
    * 15:00以降      → 次の営業日09:00へ
- --days 0 を 0 として扱う（0がFalse扱いで5に戻るバグを潰す）
- -v/--verbosity を self.verbosity に頼らず options から安全に使う（AttributeError回避）

注意:
- 祝日判定まではやらず、週末（土日）だけを飛ばす簡易 “次営業日” です。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, datetime as _dt, time as _time, timedelta as _td
from typing import Any, Dict, Optional, Tuple, List

import pandas as pd
from django.core.management.base import BaseCommand
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade
from aiapp.services.bars_5m import load_5m_bars


# =========================================================
# 時刻・市場ルール（JST前提）
# =========================================================
def _local(dt: _dt) -> _dt:
    """
    aware/naive を問わず、DjangoのTZで localtime へ揃える。
    """
    try:
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt, timezone.get_default_timezone())
    except Exception:
        # 最後の手段：timezone.now() に寄せる
        return timezone.localtime(timezone.now(), timezone.get_default_timezone())


def _session_range_jst(d: _date) -> Tuple[_dt, _dt]:
    """
    ザラ場（仮）：09:00〜15:00 JST
    """
    tz = timezone.get_default_timezone()
    start = timezone.make_aware(_dt.combine(d, _time(9, 0)), tz)
    end = timezone.make_aware(_dt.combine(d, _time(15, 0)), tz)
    return start, end


def _is_weekend(d: _date) -> bool:
    return d.weekday() >= 5  # 5=Sat, 6=Sun


def _next_business_day(d: _date) -> _date:
    """
    土日だけ飛ばす簡易版の次営業日。
    """
    nd = d + _td(days=1)
    while _is_weekend(nd):
        nd = nd + _td(days=1)
    return nd


def _normalize_active_start(
    *,
    trade_date: _date,
    opened_at: _dt,
) -> Tuple[_date, _dt]:
    """
    “評価開始時刻固定しない” を守りつつ、現実に約定し得ない時間帯だけ市場ルールで丸める。

    戻り値:
      (effective_trade_date, active_start_dt)
    """
    opened_local = _local(opened_at)

    # 基本は trade_date を使う（あなたの設計どおり “trade_date基準”）
    eff_date = trade_date
    sess_start, sess_end = _session_range_jst(eff_date)

    # opened_at の “日付” が trade_date とズレていたら、
    # まずはその日のセッション開始へ寄せる（ズレを拡大させない）
    if opened_local.date() != eff_date:
        return eff_date, sess_start

    t = opened_local.time()

    # 寄り前 → 当日9:00
    if t < _time(9, 0):
        return eff_date, sess_start

    # 前場と後場の間 → 当日12:30
    if _time(11, 30) <= t < _time(12, 30):
        tz = timezone.get_default_timezone()
        return eff_date, timezone.make_aware(_dt.combine(eff_date, _time(12, 30)), tz)

    # 引け後 → 次営業日9:00
    # ここは “固定開始” ではなく、「現実に約定し得る最速」を返しているだけ
    if t >= _time(15, 0):
        next_d = _next_business_day(eff_date)
        ns, _ne = _session_range_jst(next_d)
        return next_d, ns

    # 場中（前場/後場）→ opened_at そのまま
    return eff_date, opened_local


# =========================================================
# DataFrameカラム検出（MultiIndex対応）
# =========================================================
def _find_ohlc_columns(df: pd.DataFrame) -> Tuple[Optional[Any], Optional[Any], Optional[Any]]:
    """
    df.columns が str でも MultiIndex でも、
    'Low' / 'High' / 'Close' (or 'Adj Close') をうまく拾う。
    戻り値は (low_col, high_col, close_col)。
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
    # Timestamp -> python datetime（tz保持）
    try:
        return ts.to_pydatetime()
    except Exception:
        return fallback


# =========================================================
# EV_true（true outcome）: -1 / 0 / +1
# =========================================================
def _ev_true_from_label(label: str) -> float:
    """
    label は win/lose/flat/no_position を想定
    """
    lab = (label or "").strip().lower()
    if lab == "win":
        return 1.0
    if lab == "lose":
        return -1.0
    return 0.0


def _label_for_side_pl(qty: float, pl_per_share: float) -> str:
    if qty is None or qty <= 0:
        return "no_position"
    if pl_per_share > 0:
        return "win"
    if pl_per_share < 0:
        return "lose"
    return "flat"


# =========================================================
# 1件評価
# =========================================================
@dataclass
class EvalResult:
    updated: bool
    skip_reason: Optional[str] = None


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


def _evaluate_one(v: VirtualTrade) -> Tuple[EvalResult, Dict[str, Any]]:
    """
    1件の VirtualTrade を評価して v に反映する。

    返り値:
      (EvalResult, debug_dict)
    """
    debug: Dict[str, Any] = {}

    # opened_at を基準に “その直後から” 評価開始（市場外だけ丸める）
    eff_date, active_start = _normalize_active_start(trade_date=v.trade_date, opened_at=v.opened_at)
    debug["effective_trade_date"] = eff_date.isoformat()
    debug["active_start"] = active_start.isoformat()

    # 5分足ロード（trade_date ではなく effective_trade_date）
    bars = load_5m_bars(v.code, eff_date)
    if bars is None or len(bars) == 0:
        raise RuntimeError("no_bars")

    df = bars.copy()

    # tsカラムが無ければ index から復元
    if "ts" not in df.columns:
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index().rename(columns={df.index.name or "index": "ts"})
        else:
            raise RuntimeError("no_ts_column")

    df["ts"] = pd.to_datetime(df["ts"])

    low_col, high_col, close_col = _find_ohlc_columns(df)
    if low_col is None or high_col is None or close_col is None:
        raise RuntimeError("no_ohlc_columns")

    # セッション範囲（effective_trade_date）
    sess_start, sess_end = _session_range_jst(eff_date)

    # “active_start 以降”のバーだけに絞る（これがあなたの主張そのもの）
    eff_df = df[(df["ts"] >= active_start) & (df["ts"] <= sess_end)].copy()
    if eff_df.empty:
        raise RuntimeError("no_bars_after_active")

    # entry/tp/sl は DBのAIスナップショット優先
    entry = _safe_float(v.entry_px)
    tp = _safe_float(v.tp_px)
    sl = _safe_float(v.sl_px)

    # entry が無いと評価不能
    if entry is None:
        raise RuntimeError("no_entry")

    # -------- エントリー判定（指値） --------
    hit_mask = (eff_df[low_col] <= entry) & (eff_df[high_col] >= entry)
    if not hit_mask.to_numpy().any():
        # 指値が一度もタッチしない → no_position として “クローズ扱い”
        v.eval_exit_reason = "no_position"
        v.eval_horizon_days = v.eval_horizon_days or 0
        v.eval_entry_px = None
        v.eval_entry_ts = None
        v.eval_exit_px = None
        v.eval_exit_ts = None

        # broker label
        qr = float(v.qty_rakuten or 0)
        qm = float(v.qty_matsui or 0)
        qs = float(v.qty_sbi or 0)
        v.eval_label_rakuten = _label_for_side_pl(qr, 0.0)  # no_position
        v.eval_label_matsui = _label_for_side_pl(qm, 0.0)
        v.eval_label_sbi = _label_for_side_pl(qs, 0.0)

        v.eval_pl_rakuten = 0.0 if qr > 0 else None
        v.eval_pl_matsui = 0.0 if qm > 0 else None
        v.eval_pl_sbi = 0.0 if qs > 0 else None

        v.closed_at = timezone.now()
        v.recompute_r()
        return EvalResult(updated=True), debug

    hit_df = eff_df[hit_mask]
    first_hit = hit_df.iloc[0]
    entry_ts = _coerce_ts_scalar(first_hit["ts"], fallback=active_start)
    exec_entry_px = float(entry)

    # -------- エグジット判定（TP / SL / horizon_close） --------
    eval_df = eff_df[eff_df["ts"] >= entry_ts].copy()
    if eval_df.empty:
        # ありえにくいが保険
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

    # ---- broker PL ----
    qr = float(v.qty_rakuten or 0)
    qm = float(v.qty_matsui or 0)
    qs = float(v.qty_sbi or 0)

    v.eval_entry_px = float(exec_entry_px)
    v.eval_entry_ts = entry_ts
    v.eval_exit_px = float(exit_px)
    v.eval_exit_ts = exit_ts
    v.eval_exit_reason = exit_reason
    v.eval_horizon_days = v.eval_horizon_days or 0

    v.eval_label_rakuten = _label_for_side_pl(qr, pl_per_share)
    v.eval_label_matsui = _label_for_side_pl(qm, pl_per_share)
    v.eval_label_sbi = _label_for_side_pl(qs, pl_per_share)

    v.eval_pl_rakuten = (pl_per_share * qr) if qr > 0 else None
    v.eval_pl_matsui = (pl_per_share * qm) if qm > 0 else None
    v.eval_pl_sbi = (pl_per_share * qs) if qs > 0 else None

    # ---- close ----
    v.closed_at = timezone.now()

    # ---- R ----
    v.recompute_r()

    # ---- PRO (replay.pro があればそこも更新) ----
    replay = v.replay if isinstance(v.replay, dict) else {}
    pro = replay.get("pro") if isinstance(replay.get("pro"), dict) else None
    if isinstance(pro, dict):
        qty_pro = _safe_float(pro.get("qty_pro")) or 0.0
        label_pro = _label_for_side_pl(qty_pro, pl_per_share)
        ev_true_pro = _ev_true_from_label(label_pro)

        # DBカラム（存在する前提：あなたが移行済み）
        try:
            setattr(v, "ev_true_pro", float(ev_true_pro))
        except Exception:
            pass

        try:
            setattr(v, "rank_group_pro", str(label_pro))
        except Exception:
            pass

        # replay側も追随（デバッグ＆後段バッチ用）
        pro["ev_true_pro"] = float(ev_true_pro)
        pro["rank_group_pro"] = str(label_pro)
        replay["pro"] = pro
        v.replay = replay

    return EvalResult(updated=True), debug


# =========================================================
# Rank（同一 trade_date 内）
# =========================================================
def _rank_for_trade_date(d: _date) -> int:
    """
    trade_date=d のうち、ev_true_pro が埋まっている行を rank_pro で順位付け。
    優先:
      1) ev_true_pro (desc)
      2) closed_at (asc)  ※同値の並びは “先に終わった順”
      3) id (asc)
    """
    qs = VirtualTrade.objects.filter(trade_date=d).exclude(ev_true_pro__isnull=True)

    # rank_pro がある前提（あなたが移行済み）
    rows = list(qs.order_by("-ev_true_pro", "closed_at", "id").values_list("id", flat=True))
    if not rows:
        return 0

    # 一括更新（素朴にループでOK：件数は日次で小さい想定）
    updated = 0
    for i, vid in enumerate(rows, start=1):
        VirtualTrade.objects.filter(id=vid).update(rank_pro=i)
        updated += 1
    return updated


# =========================================================
# Command
# =========================================================
class Command(BaseCommand):
    help = "AI紙シミュ評価（5分足）→ VirtualTrade に eval_* / R / EV_true / Rank を反映"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=5, help="何日前まで評価対象に含めるか（trade_date基準）")
        parser.add_argument("--limit", type=int, default=0, help="0なら全件。>0なら最大件数（新しいopened_at優先）")
        parser.add_argument("--force", action="store_true", help="すでに評価済みでも再評価する")
        parser.add_argument("--dry-run", action="store_true", help="DB更新せずログだけ")

    def handle(self, *args, **options):
        # verbosity は options から取る（self.verbosity 依存しない）
        verbosity = int(options.get("verbosity", 1) or 1)

        # days=0 を “0” として扱う（Noneの時だけデフォルトに寄せる）
        days_opt = options.get("days")
        days = 5 if days_opt is None else int(days_opt)

        limit = int(options.get("limit") or 0)
        force = bool(options.get("force"))
        dry_run = bool(options.get("dry_run"))

        today = timezone.localdate()
        date_min = today - _td(days=days)

        # 対象：trade_date >= date_min
        qs = VirtualTrade.objects.filter(trade_date__gte=date_min)

        # forceでなければ未評価（closed_at is null）のみ
        if not force:
            qs = qs.filter(closed_at__isnull=True)

        # limit>0 なら新しい opened_at 優先で絞る
        qs = qs.order_by("-opened_at")
        if limit > 0:
            qs = qs[:limit]

        targets = list(qs)
        # date_max はログ用
        date_max = today

        self.stdout.write(
            f"[ai_sim_eval] start days={days} date_min={date_min} date_max={date_max} "
            f"targets={len(targets)} force={force} dry_run={dry_run}"
        )

        updated = 0
        skipped = 0
        touched_trade_dates: set[_date] = set()

        for v in targets:
            try:
                res, dbg = _evaluate_one(v)

                if dry_run:
                    if verbosity >= 2:
                        self.stdout.write(f"  dry-run id={v.id} code={v.code} trade_date={v.trade_date} ok dbg={dbg}")
                    continue

                if res.updated:
                    v.save()
                    updated += 1
                    touched_trade_dates.add(v.trade_date)

            except Exception as e:
                skipped += 1
                reason = str(e) if str(e) else e.__class__.__name__

                # “スキップ”でも reason を残しておく（あなたのログ運用に合わせる）
                if not dry_run:
                    try:
                        v.eval_exit_reason = reason
                        # ここは “閉じない” （現状運用に合わせる）
                        # v.closed_at は触らない
                        v.save(update_fields=["eval_exit_reason"])
                    except Exception:
                        pass

                if verbosity >= 2:
                    self.stdout.write(f"  skip id={v.id} code={v.code} trade_date={v.trade_date} reason={reason}")

        # Rank（trade_dateごと）
        rank_rows_total = 0
        if not dry_run:
            for d in sorted(touched_trade_dates):
                rank_rows_total += _rank_for_trade_date(d)

        self.stdout.write(
            f"[ai_sim_eval] done updated={updated} skipped={skipped} "
            f"touched_trade_dates={len(touched_trade_dates)} ranked_rows={rank_rows_total} dry_run={dry_run}"
        )