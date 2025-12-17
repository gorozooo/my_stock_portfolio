# aiapp/management/commands/ai_sim_eval.py
# -*- coding: utf-8 -*-
"""
ai_sim_eval

AI紙シミュ評価（5分足）→ VirtualTrade に eval_* / R / EV_true_pro / rank_pro を反映

重要方針（あなたのルール）：
- 評価開始時刻は固定しない
- opened_at（注文を作った時刻＝現実世界で注文を出した時刻）から評価開始
- ただし時刻比較は必ず tz を揃える（DBはUTC保持、5分足の ts はJST）

今回の修正点（3点まとめ）：
1) 対象抽出を trade_date 基準に統一（--days 0 なら trade_date=今日だけ）
2) 評価開始 = opened_at を JST に localtime した上で 5分足 ts と比較
3) 例外時ログの self.verbosity AttributeError を潰す（options['verbosity'] 参照）

★追加で重要（今回の不具合の本丸）：
- options.get("days") が 0 のとき `or 5` で 5 に化けるのを修正
- df["ts"] の tz を必ず Asia/Tokyo に寄せて比較する（UTC/naive混在で no_bars を防ぐ）
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
    - 前場 09:00〜11:30
    - 後場 12:30〜15:30
    ただしここでは「評価開始を固定しない」ため、単に場全体の範囲を持つ。
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


def _find_ohlc_columns(df) -> Tuple[Optional[Any], Optional[Any], Optional[Any]]:
    """
    df.columns が str でも MultiIndex でも、
    'Low' / 'High' / 'Close'(or 'Adj Close') を拾う。
    戻り値は df[...] のキーとして使える実カラム値。
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

    if "ts" not in df.columns:
        return None

    try:
        s = pd.to_datetime(df["ts"], errors="coerce")
    except Exception:
        return None

    # ここが肝：tz-naive / tz-aware を判別して JST へ寄せる
    try:
        # tz-aware の場合
        if getattr(s.dt, "tz", None) is not None:
            s = s.dt.tz_convert("Asia/Tokyo")
        else:
            # tz-naive の場合：JST として localize
            s = s.dt.tz_localize("Asia/Tokyo")
    except Exception:
        # object dtype などで dt が使えない場合の最後の砦
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

def _evaluate_one(v: VirtualTrade, *, verbose: int = 1) -> EvalResult:
    """
    v.trade_date の 5分足を使って、opened_at（注文作成時刻）以降で
    指値(entry)→TP/SL→終値クローズ を判定する。
    """
    trade_date = v.trade_date

    # 5分足ロード（キャッシュ）
    bars = load_5m_bars(v.code, trade_date)
    if bars is None or len(bars) == 0:
        return EvalResult(ok=False, reason="no_bars")

    df = bars.copy()

    # ts カラムがなければ index から復元
    if "ts" not in df.columns:
        try:
            import pandas as pd
            if isinstance(df.index, pd.DatetimeIndex):
                df = df.reset_index().rename(columns={df.index.name or "index": "ts"})
            else:
                return EvalResult(ok=False, reason="no_ts")
        except Exception:
            return EvalResult(ok=False, reason="no_ts")

    # tz を必ず JST に揃える（ここが no_bars 祭りの本丸）
    df2 = _ensure_ts_jst(df)
    if df2 is None:
        return EvalResult(ok=False, reason="bad_ts")
    df = df2

    low_col, high_col, close_col = _find_ohlc_columns(df)
    if low_col is None or high_col is None or close_col is None:
        return EvalResult(ok=False, reason="no_ohlc")

    # --- 評価開始時刻 = opened_at（JSTに揃える） ---
    opened_local = _to_local(v.opened_at)
    if opened_local is None:
        return EvalResult(ok=False, reason="no_opened_at")

    # 評価範囲（場全体）
    session_start, session_end = _jst_session_range(trade_date)

    # active_start = opened_at（ただし、場外なら「その日の場の範囲内に丸める」）
    # ※ここは “固定” ではなく、現実世界の「約定可能制約」を入れてるだけ。
    if opened_local < session_start:
        active_start = session_start
    elif opened_local > session_end:
        # その日の場が終わってから注文を作った → 当日バーでは評価不能
        return EvalResult(ok=False, reason="no_bars_after_active")
    else:
        active_start = opened_local

    # active_start 以降に絞る（df["ts"] は JST tz-aware に統一済み）
    df_eff = df[(df["ts"] >= active_start) & (df["ts"] <= session_end)]
    if df_eff is None or len(df_eff) == 0:
        return EvalResult(ok=False, reason="no_bars_after_active")

    # 必要パラメータ
    entry = _safe_float(v.entry_px) or _safe_float((v.replay or {}).get("sim_order", {}).get("entry"))
    tp = _safe_float(v.tp_px) or _safe_float((v.replay or {}).get("sim_order", {}).get("tp"))
    sl = _safe_float(v.sl_px) or _safe_float((v.replay or {}).get("sim_order", {}).get("sl"))

    if entry is None:
        return EvalResult(ok=False, reason="no_entry")

    # -------- エントリー判定（指値） --------
    hit_mask = (df_eff[low_col] <= entry) & (df_eff[high_col] >= entry)
    if not hit_mask.to_numpy().any():
        # 指値未到達 → ノーポジ
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

    hit_df = df_eff[hit_mask]
    if hit_df is None or len(hit_df) == 0:
        return EvalResult(
            ok=True,
            reason="no_position",
            eval_exit_reason="no_position",
            pl_per_share=0.0,
        )

    first_hit = hit_df.iloc[0]
    entry_ts = _coerce_ts(first_hit["ts"], fallback=active_start)
    exec_entry_px = float(entry)

    # -------- エグジット判定（TP / SL / horizon_close） --------
    eval_df = df_eff[df_eff["ts"] >= entry_ts].copy()
    if eval_df is None or len(eval_df) == 0:
        exit_ts = entry_ts
        exit_px = exec_entry_px
        exit_reason = "horizon_close"
    else:
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

        if hit_tp_idx is not None or hit_sl_idx is not None:
            if hit_tp_idx is not None and hit_sl_idx is not None:
                if hit_tp_idx <= hit_sl_idx:
                    row = eval_df.loc[hit_tp_idx]
                    exit_ts = _coerce_ts(row["ts"], fallback=entry_ts)
                    exit_px = float(tp)
                    exit_reason = "hit_tp"
                else:
                    row = eval_df.loc[hit_sl_idx]
                    exit_ts = _coerce_ts(row["ts"], fallback=entry_ts)
                    exit_px = float(sl)
                    exit_reason = "hit_sl"
            elif hit_tp_idx is not None:
                row = eval_df.loc[hit_tp_idx]
                exit_ts = _coerce_ts(row["ts"], fallback=entry_ts)
                exit_px = float(tp)
                exit_reason = "hit_tp"
            else:
                row = eval_df.loc[hit_sl_idx]
                exit_ts = _coerce_ts(row["ts"], fallback=entry_ts)
                exit_px = float(sl)
                exit_reason = "hit_sl"
        else:
            last_row = eval_df.iloc[-1]
            exit_ts = _coerce_ts(last_row["ts"], fallback=entry_ts)
            exit_px = float(last_row[close_col])
            exit_reason = "horizon_close"

    pl_per_share = float(exit_px) - float(exec_entry_px)

    return EvalResult(
        ok=True,
        reason="ok",
        eval_entry_px=exec_entry_px,
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
    - まずは win_rate を 0〜1 に正規化して “期待値っぽい指標” として扱う
      （閾値の色分け設計はやらない）
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
        parser.add_argument("--limit", type=int, default=0, help="0なら全件。>0なら最大件数（新しい opened_at 優先）")
        parser.add_argument("--force", action="store_true", help="すでに評価済みでも再評価する")
        parser.add_argument("--dry-run", action="store_true", help="DB更新せずログだけ")

    def handle(self, *args, **options):
        # verbosity は options から必ず拾う（self.verbosity が無いケース対策）
        verbose = int(options.get("verbosity", 1) or 1)

        # ★ 0 を False 扱いして 5 に化ける事故を防ぐ
        days_opt = options.get("days", None)
        days = 5 if days_opt is None else int(days_opt)

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

        if not force:
            qs = qs.filter(eval_exit_reason="")

        qs = qs.order_by("-opened_at")
        if limit and limit > 0:
            qs = qs[:limit]

        targets = list(qs)

        self.stdout.write(
            f"[ai_sim_eval] start days={days} date_min={date_min} date_max={date_max} "
            f"targets={len(targets)} force={force} dry_run={dry_run}"
        )

        updated = 0
        skipped = 0
        touched_run_ids: set[str] = set()

        for v in targets:
            try:
                res = _evaluate_one(v, verbose=verbose)
                if not res.ok and res.reason in ("no_bars", "no_bars_after_active", "no_ts", "no_ohlc", "bad_ts", "no_opened_at", "no_entry"):
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

                # no_position は “評価として成功” 扱い
                pl_per_share = float(res.pl_per_share or 0.0)

                qty_r = v.qty_rakuten or 0
                qty_s = v.qty_sbi or 0
                qty_m = v.qty_matsui or 0

                eval_pl_r = pl_per_share * float(qty_r)
                eval_pl_s = pl_per_share * float(qty_s)
                eval_pl_m = pl_per_share * float(qty_m)

                # labels
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
                    "result": res.reason,
                    "entry_px": res.eval_entry_px,
                    "entry_ts": str(res.eval_entry_ts) if res.eval_entry_ts else None,
                    "exit_px": res.eval_exit_px,
                    "exit_ts": str(res.eval_exit_ts) if res.eval_exit_ts else None,
                    "exit_reason": res.eval_exit_reason,
                    "pl_per_share": pl_per_share,
                }
                # pro snapshot があれば pro 内にも反映
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