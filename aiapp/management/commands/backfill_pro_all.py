# aiapp/management/commands/backfill_pro_all.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from aiapp.models.vtrade import VirtualTrade
from aiapp.models.behavior_stats import BehaviorStats


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


def _ev_true_or_zero(ev: Any) -> float:
    """
    ev_true_pro が None / NaN のときは 0.0 に正規化する。
    """
    f = _safe_float(ev)
    if f is None:
        return 0.0
    return float(f)


def _get_behavior_row(code: str, mode_period: str, mode_aggr: str) -> Optional[Dict[str, Any]]:
    row = (
        BehaviorStats.objects
        .filter(code=str(code), mode_period=str(mode_period), mode_aggr=str(mode_aggr))
        .values()
        .first()
    )
    return row


def _compute_ev_true_from_behavior(
    *,
    code: str,
    mode_period: str,
    mode_aggr: str,
    est_loss_pro: Optional[float],
) -> Optional[float]:
    """
    既存思想に沿って「行動データ（BehaviorStats）」から ev_true を出す。
    ただし n=0 や avg_pl=None の場合は “計算不能” として None を返す（→呼び出し側で 0.0 にする）。
    """
    row = _get_behavior_row(code, mode_period, mode_aggr)
    if not row:
        return None

    n = row.get("n")
    avg_pl = row.get("avg_pl")

    try:
        n_i = int(n) if n is not None else 0
    except Exception:
        n_i = 0

    # 実績0件は「期待値の材料なし」
    if n_i <= 0:
        return None

    avg_pl_f = _safe_float(avg_pl)
    if avg_pl_f is None:
        return None

    denom = abs(_safe_float(est_loss_pro) or 0.0)
    if denom <= 0:
        # 分母なし（想定損失が無い/壊れてる）なら計算不能
        return None

    # ここは「平均損益を想定損失で割ったR換算」を素直に採用（過去の値域とも整合しやすい）
    ev_r = avg_pl_f / denom

    # 暴発防止の軽いクリップ（表示/ランキング用）
    if ev_r > 1.0:
        ev_r = 1.0
    if ev_r < -1.0:
        ev_r = -1.0

    return float(ev_r)


def _assign_ranks_for_run_id(run_id: str) -> int:
    """
    同一 run_id の中で ev_true_pro の降順で rank_pro を 1..N で振り直す。
    ev_true_pro が None の場合は 0.0 扱いで順位付けする。
    """
    qs = list(
        VirtualTrade.objects
        .filter(run_id=run_id)
        .only("id", "ev_true_pro", "replay", "rank_pro", "rank_group_pro")
    )

    def key(v: VirtualTrade) -> float:
        return _ev_true_or_zero(getattr(v, "ev_true_pro", None))

    qs.sort(key=key, reverse=True)

    updated: List[VirtualTrade] = []
    for i, v in enumerate(qs, start=1):
        if v.rank_pro != i:
            v.rank_pro = i
            updated.append(v)
        # rank_group は今回は設計不要なので空で統一（既存値が入ってても触らないならここ外してOK）
        if (v.rank_group_pro or "") != "":
            v.rank_group_pro = ""
            updated.append(v)

        # replay["pro"] の rank も同期
        rp = v.replay or {}
        pro = rp.get("pro") if isinstance(rp, dict) else None
        if isinstance(pro, dict):
            if pro.get("rank_pro") != i:
                pro["rank_pro"] = i
                rp["pro"] = pro
                v.replay = rp
                updated.append(v)

    if updated:
        # bulk_update は重複が混ざるので id でユニーク化
        seen = set()
        uniq: List[VirtualTrade] = []
        for v in updated:
            if v.id in seen:
                continue
            seen.add(v.id)
            uniq.append(v)
        VirtualTrade.objects.bulk_update(uniq, ["rank_pro", "rank_group_pro", "replay"])

    return len(qs)


class Command(BaseCommand):
    help = "既存 VirtualTrade の PRO項目（ev_true_pro / rank_pro）を再計算・補完する（Noneは0.0正規化）"

    def add_arguments(self, parser):
        parser.add_argument("--policy", type=str, default=None, help="保存用に replay['pro']['policy'] に入れる（任意）")
        parser.add_argument("--dry-run", action="store_true", help="DB更新せず件数だけ表示")
        parser.add_argument("--run-id", type=str, default=None, help="指定 run_id のみ対象（任意）")
        parser.add_argument("--from-id", type=int, default=None, help="id の下限（任意）")
        parser.add_argument("--to-id", type=int, default=None, help="id の上限（任意）")

    def handle(self, *args, **options):
        policy = options.get("policy") or None
        dry_run = bool(options.get("dry_run"))
        run_id = options.get("run_id") or None
        from_id = options.get("from_id")
        to_id = options.get("to_id")

        q = Q()
        if run_id:
            q &= Q(run_id=run_id)
        if from_id is not None:
            q &= Q(id__gte=int(from_id))
        if to_id is not None:
            q &= Q(id__lte=int(to_id))

        qs = VirtualTrade.objects.filter(q).order_by("id")
        total = qs.count()

        self.stdout.write(f"[backfill_pro_all] target={total} dry_run={dry_run} policy={policy or '-'} run_id={run_id or '-'}")

        updated = 0
        touched_run_ids: set[str] = set()

        with transaction.atomic():
            batch: List[VirtualTrade] = []
            for v in qs.iterator(chunk_size=500):
                rp = v.replay or {}
                if not isinstance(rp, dict):
                    rp = {}

                pro = rp.get("pro")
                if not isinstance(pro, dict):
                    pro = {}

                # 既に pro があるなら既存値を尊重しつつ、ev_true_pro だけ “None→0.0” に正規化
                est_loss_pro = _safe_float(pro.get("est_loss_pro"))
                # DB側の mode_period/mode_aggr を使う（全期間 short/aggr でも将来拡張で崩れない）
                mode_period = (v.mode_period or "short").strip().lower()
                mode_aggr = (v.mode_aggr or "aggr").strip().lower()

                # 1) 行動データから算出（算出不能なら None）
                ev_calc = _compute_ev_true_from_behavior(
                    code=v.code,
                    mode_period=mode_period,
                    mode_aggr=mode_aggr,
                    est_loss_pro=est_loss_pro,
                )

                # 2) 正規化（None→0.0）
                ev_final = _ev_true_or_zero(ev_calc)

                # 3) DB列も replay["pro"] も同期
                changed = False
                if v.ev_true_pro != ev_final:
                    v.ev_true_pro = ev_final
                    changed = True

                if pro.get("ev_true_pro") != ev_final:
                    pro["ev_true_pro"] = ev_final
                    changed = True

                if policy:
                    if pro.get("policy") != policy:
                        pro["policy"] = policy
                        changed = True

                rp["pro"] = pro
                if v.replay != rp:
                    v.replay = rp
                    changed = True

                if changed:
                    batch.append(v)
                    updated += 1
                    touched_run_ids.add(v.run_id)

                # flush
                if len(batch) >= 300:
                    if not dry_run:
                        VirtualTrade.objects.bulk_update(batch, ["ev_true_pro", "replay"])
                    batch.clear()

            if batch:
                if not dry_run:
                    VirtualTrade.objects.bulk_update(batch, ["ev_true_pro", "replay"])
                batch.clear()

            # Rank を run_id 単位で振り直す（Noneは0.0扱い）
            if not dry_run:
                for rid in sorted(touched_run_ids):
                    _assign_ranks_for_run_id(rid)

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(f"[backfill_pro_all] done updated={updated} touched_run_ids={len(touched_run_ids)} dry_run={dry_run}")