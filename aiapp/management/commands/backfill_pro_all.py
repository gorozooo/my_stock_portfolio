# aiapp/management/commands/backfill_pro_all.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade
from aiapp.models.behavior_stats import BehaviorStats


# =========================================================
# 小ヘルパ
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


def _as_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


# =========================================================
# BehaviorStats 取得（ここが本題：フォールバック）
# =========================================================
@dataclass
class BSRow:
    mode_period: str
    mode_aggr: str
    n: int
    win: int
    lose: int
    flat: int


def _get_behavior_stats_with_fallback(
    code: str,
    *,
    prefer_period: str,
    prefer_aggr: str,
    fallback_period: str = "all",
    fallback_aggr: str = "all",
) -> Optional[BSRow]:
    """
    1) (prefer_period, prefer_aggr) を最優先
    2) 無ければ (all, all) を使う
    """
    # 1) prefer
    row = (
        BehaviorStats.objects
        .filter(code=str(code), mode_period=prefer_period, mode_aggr=prefer_aggr)
        .values("mode_period", "mode_aggr", "n", "win", "lose", "flat")
        .first()
    )
    if row:
        return BSRow(
            mode_period=str(row.get("mode_period") or ""),
            mode_aggr=str(row.get("mode_aggr") or ""),
            n=int(row.get("n") or 0),
            win=int(row.get("win") or 0),
            lose=int(row.get("lose") or 0),
            flat=int(row.get("flat") or 0),
        )

    # 2) fallback
    row = (
        BehaviorStats.objects
        .filter(code=str(code), mode_period=fallback_period, mode_aggr=fallback_aggr)
        .values("mode_period", "mode_aggr", "n", "win", "lose", "flat")
        .first()
    )
    if row:
        return BSRow(
            mode_period=str(row.get("mode_period") or ""),
            mode_aggr=str(row.get("mode_aggr") or ""),
            n=int(row.get("n") or 0),
            win=int(row.get("win") or 0),
            lose=int(row.get("lose") or 0),
            flat=int(row.get("flat") or 0),
        )

    return None


def _ev_true_from_behavior(bs: Optional[BSRow]) -> float:
    """
    EV_true（-1〜+1）:
      win を +1、lose を -1、flat を 0 とした期待値。
    ※ n==0 or bs無し → 0.0（中立）
    """
    if bs is None:
        return 0.0
    n = int(bs.n or 0)
    if n <= 0:
        return 0.0

    win = int(bs.win or 0)
    lose = int(bs.lose or 0)
    flat = int(bs.flat or 0)

    # 安全側：合計がnとズレてても分母はnを使う（ログのnを正とみなす）
    ev = (win - lose) / float(n)
    # クリップ（念のため）
    if ev != ev:
        return 0.0
    if ev < -1.0:
        return -1.0
    if ev > 1.0:
        return 1.0
    return float(ev)


# =========================================================
# ランク付け（run_id ごとに ev_true_pro 降順）
# =========================================================
def _assign_rank_for_run_id(run_id: str, *, dry_run: bool) -> int:
    """
    run_id 内で ev_true_pro の降順で rank_pro を 1..N に付与。
    同値は安定ソート（code昇順）で決める。
    戻り値：更新件数
    """
    qs = VirtualTrade.objects.filter(run_id=run_id).only("id", "code", "ev_true_pro", "rank_pro")
    rows = list(qs.values("id", "code", "ev_true_pro"))

    # ev_true_pro None は 0.0 扱い
    def sort_key(r: Dict[str, Any]):
        ev = r.get("ev_true_pro")
        evf = float(ev) if ev is not None else 0.0
        return (-evf, str(r.get("code") or ""))

    rows.sort(key=sort_key)

    updated = 0
    for i, r in enumerate(rows, start=1):
        rid = r["id"]
        if not dry_run:
            VirtualTrade.objects.filter(id=rid).update(rank_pro=i)
        updated += 1

    return updated


# =========================================================
# コマンド本体
# =========================================================
class Command(BaseCommand):
    help = "既存 VirtualTrade に PRO 統一口座の値（qty/資金/PL/Loss/EV/Rank）を再計算して埋める（BehaviorStatsはshort/aggr→all/allにフォールバック）"

    def add_arguments(self, parser):
        parser.add_argument("--policy", type=str, required=True, help="PROポリシーyml（例: aiapp/policies/short_aggressive.yml）")
        parser.add_argument("--dry-run", action="store_true", help="DBに書き込まない（ログだけ）")
        parser.add_argument("--run-id", type=str, default=None, help="対象run_idを1つに絞る（省略で全期間）")
        parser.add_argument("--user-id", type=int, default=None, help="対象ユーザーを絞る（省略で全ユーザー）")
        parser.add_argument("--period", type=str, default=None, help="BehaviorStats優先 period（省略時は vtrade.mode_period）")
        parser.add_argument("--aggr", type=str, default=None, help="BehaviorStats優先 aggr（省略時は vtrade.mode_aggr）")

    def handle(self, *args, **options):
        policy_path: str = str(options["policy"])
        dry_run: bool = bool(options.get("dry_run"))
        only_run_id: Optional[str] = (options.get("run_id") or None)
        only_user_id: Optional[int] = options.get("user_id") or None
        force_period: Optional[str] = (options.get("period") or None)
        force_aggr: Optional[str] = (options.get("aggr") or None)

        base_q = Q()
        if only_run_id:
            base_q &= Q(run_id=str(only_run_id))
        if only_user_id:
            base_q &= Q(user_id=int(only_user_id))

        # 対象（全期間）
        target_qs = VirtualTrade.objects.filter(base_q).only(
            "id",
            "run_id",
            "user_id",
            "code",
            "mode_period",
            "mode_aggr",
            "replay",
            "ev_true_pro",
            "rank_pro",
        )

        total = target_qs.count()
        run_id_label = only_run_id or "-"
        self.stdout.write(
            f"[backfill_pro_all] target={total} dry_run={dry_run} policy={policy_path} run_id={run_id_label}"
        )

        if total <= 0:
            return

        touched_run_ids: set[str] = set()
        updated = 0
        skipped = 0

        # 大量更新でも落ちないように小分け
        BATCH = 200

        # ※ここでは「PROのqty/資金/PL/Loss」は、すでに replay['pro'] に入っている前提でも良いし、
        #   無い場合でも最低限 EV_true_pro / rank_pro を埋められるようにしている。
        #   もし pro_account による再計算を入れたい場合は、ここに後挿しできる構造にしてある。
        ids = list(target_qs.values_list("id", flat=True))

        for start in range(0, len(ids), BATCH):
            chunk_ids = ids[start : start + BATCH]
            chunk = list(
                VirtualTrade.objects.filter(id__in=chunk_ids).select_related("user").only(
                    "id",
                    "run_id",
                    "user_id",
                    "code",
                    "mode_period",
                    "mode_aggr",
                    "replay",
                    "ev_true_pro",
                    "rank_pro",
                )
            )

            with transaction.atomic():
                for v in chunk:
                    try:
                        touched_run_ids.add(str(v.run_id or ""))

                        replay = _as_dict(v.replay)
                        pro = _as_dict(replay.get("pro"))
                        # policy の記録は揃える
                        pro["policy"] = policy_path

                        # prefer の period/aggr は vtrade 優先（強制指定があればそれ）
                        prefer_period = (force_period or v.mode_period or "short").strip().lower()
                        prefer_aggr = (force_aggr or v.mode_aggr or "aggr").strip().lower()

                        bs = _get_behavior_stats_with_fallback(
                            code=v.code,
                            prefer_period=prefer_period,
                            prefer_aggr=prefer_aggr,
                            fallback_period="all",
                            fallback_aggr="all",
                        )

                        ev_true = _ev_true_from_behavior(bs)

                        # DB側（検索/ソート用）
                        if not dry_run:
                            v.ev_true_pro = float(ev_true)
                        # replay側（デバッグ・監査）
                        pro["ev_true_pro"] = float(ev_true)
                        pro["ev_source"] = {
                            "prefer": {"mode_period": prefer_period, "mode_aggr": prefer_aggr},
                            "used": None if bs is None else {"mode_period": bs.mode_period, "mode_aggr": bs.mode_aggr, "n": bs.n},
                        }

                        # rank は run_id 内一括で後段付け（ここでは None のままでもOK）
                        replay["pro"] = pro
                        if not dry_run:
                            v.replay = replay
                            v.save(update_fields=["ev_true_pro", "replay"])
                        updated += 1
                    except Exception:
                        skipped += 1
                        continue

        # run_id ごとに rank を付与
        rank_updated_total = 0
        for rid in sorted([x for x in touched_run_ids if x]):
            try:
                rank_updated_total += _assign_rank_for_run_id(rid, dry_run=dry_run)
            except Exception:
                continue

        self.stdout.write(
            f"[backfill_pro_all] done updated={updated} skipped={skipped} touched_run_ids={len(touched_run_ids)} rank_rows={rank_updated_total} (dry_run={dry_run})"
        )