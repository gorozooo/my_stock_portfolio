# aiapp/management/commands/backfill_pro_all.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, Optional

from django.core.management.base import BaseCommand
from django.db import transaction

from aiapp.models.vtrade import VirtualTrade
from aiapp.services.pro_account import load_policy_yaml, compute_pro_sizing_and_filter


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


def _pick_ev_true_from_replay(replay: Dict[str, Any]) -> Optional[float]:
    """
    既存データ互換：replay の中から取れるものを PRO代表にする。
    優先:
      replay["pro"]["ev_true_pro"]
      replay["sim_order"]["ev_true_pro"]
      replay["sim_order"]["ev_true_rakuten"] -> matsui -> sbi
      replay["ev_true"] dict の各キー
    """
    try:
        pro = replay.get("pro")
        if isinstance(pro, dict):
            v = _safe_float(pro.get("ev_true_pro"))
            if v is not None:
                return v

        so = replay.get("sim_order")
        if isinstance(so, dict):
            for k in ("ev_true_pro", "ev_true_rakuten", "ev_true_matsui", "ev_true_sbi"):
                v = _safe_float(so.get(k))
                if v is not None:
                    return v

        ev = replay.get("ev_true")
        if isinstance(ev, dict):
            for k in ("pro", "rakuten", "matsui", "sbi", "R", "M", "S"):
                v = _safe_float(ev.get(k))
                if v is not None:
                    return v
    except Exception:
        return None
    return None


def _pick_rank_from_replay(replay: Dict[str, Any]) -> Optional[int]:
    """
    rank は過去データに無いことが多いので、あれば拾う程度。
    本命の rank 再計算は将来 ai_sim_eval_pro 的にやる（必要なら）
    """
    try:
        pro = replay.get("pro")
        if isinstance(pro, dict) and pro.get("rank_pro") is not None:
            return int(pro.get("rank_pro"))
        so = replay.get("sim_order")
        if isinstance(so, dict) and so.get("rank_pro") is not None:
            return int(so.get("rank_pro"))
    except Exception:
        return None
    return None


class Command(BaseCommand):
    help = "既存VirtualTrade（全期間）を PRO統一口座の qty/cash/PL/Loss で埋め直す（学習/評価の主役をPROに固定）"

    def add_arguments(self, parser):
        parser.add_argument("--policy", type=str, default="aiapp/policies/short_aggressive.yml", help="PROポリシー yml")
        parser.add_argument("--pro-equity", type=float, default=None, help="PRO仮想総資産（円）を上書き")
        parser.add_argument("--dry-run", action="store_true", help="更新せずに件数だけ見る")
        parser.add_argument("--limit", type=int, default=None, help="最大処理件数（テスト用）")

    def handle(self, *args, **options):
        policy_path: str = str(options.get("policy") or "aiapp/policies/short_aggressive.yml")
        pro_equity_override: Optional[float] = options.get("pro_equity")
        dry_run: bool = bool(options.get("dry_run"))
        limit: Optional[int] = options.get("limit")

        try:
            policy = load_policy_yaml(policy_path)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[backfill_pro_all] policy load error: {e}"))
            return

        qs = VirtualTrade.objects.all().order_by("id")
        if limit is not None:
            qs = qs[: int(limit)]

        total = qs.count()
        self.stdout.write(f"[backfill_pro_all] target={total} policy={policy_path} dry_run={dry_run} pro_equity={pro_equity_override}")

        updated = 0
        skipped = 0

        with transaction.atomic():
            for vt in qs.iterator(chunk_size=500):
                side = (vt.side or "BUY").upper()

                # entry/tp/sl は DB のスナップショットを使う（安定）
                entry = _safe_float(vt.entry_px)
                tp = _safe_float(vt.tp_px)
                sl = _safe_float(vt.sl_px)

                res, reason = compute_pro_sizing_and_filter(
                    code=vt.code,
                    side=side,
                    entry=entry,
                    tp=tp,
                    sl=sl,
                    policy=policy,
                    total_equity_yen=_safe_float(pro_equity_override),
                )

                if res is None:
                    skipped += 1
                    continue

                if dry_run:
                    updated += 1
                    continue

                vt.qty_pro = int(res.qty_pro)
                vt.required_cash_pro = float(res.required_cash_pro)
                vt.est_pl_pro = float(res.est_pl_pro)
                vt.est_loss_pro = float(res.est_loss_pro)

                # 既存から拾えるなら入れる（なければ None のまま）
                replay = vt.replay if isinstance(vt.replay, dict) else {}
                vt.ev_true_pro = _pick_ev_true_from_replay(replay)
                rp = _pick_rank_from_replay(replay)
                vt.rank_pro = rp

                # replay にも PRO を残す（デバッグ/監査）
                try:
                    if not isinstance(replay, dict):
                        replay = {}
                    pro = replay.get("pro")
                    if not isinstance(pro, dict):
                        pro = {}
                    pro.update(
                        {
                            "policy": policy_path,
                            "qty_pro": vt.qty_pro,
                            "required_cash_pro": vt.required_cash_pro,
                            "est_pl_pro": vt.est_pl_pro,
                            "est_loss_pro": vt.est_loss_pro,
                            "ev_true_pro": vt.ev_true_pro,
                            "rank_pro": vt.rank_pro,
                        }
                    )
                    replay["pro"] = pro
                    vt.replay = replay
                except Exception:
                    pass

                vt.save(update_fields=[
                    "qty_pro", "required_cash_pro", "est_pl_pro", "est_loss_pro",
                    "ev_true_pro", "rank_pro",
                    "replay",
                ])
                updated += 1

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS(
            f"[backfill_pro_all] done updated={updated} skipped={skipped} (dry_run={dry_run})"
        ))