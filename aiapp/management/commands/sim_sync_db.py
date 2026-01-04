# aiapp/management/commands/sim_sync_db.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone
from django.contrib.auth import get_user_model

from aiapp.models.vtrade import VirtualTrade


def _parse_date(s: Any):
    from datetime import date as _date
    if isinstance(s, _date):
        return s
    if isinstance(s, str) and s:
        return _date.fromisoformat(s)
    return None


def _parse_dt_iso(ts: Any):
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = timezone.datetime.fromisoformat(ts)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


class Command(BaseCommand):
    """
    既存の JSONL(simulate/*.jsonl) を読み込んで VirtualTrade(DB) に同期する。

    - まだDBに無い場合: OPENとして作成
    - eval_* がJSONLにある場合: CLOSE/評価も反映して closed_at まで埋める
    """

    help = "JSONL(simulate) -> VirtualTrade(DB) 同期（過去分取り込み用）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--user-id", type=int, default=None, help="ユーザーID（省略時は最初のユーザー）")
        parser.add_argument("--glob", type=str, default="*.jsonl", help="読み込むファイルパターン（default: *.jsonl）")
        parser.add_argument("--force", action="store_true", help="既存行があっても上書き反映する")
        parser.add_argument("--dry-run", action="store_true", help="DB保存せず件数だけ確認する")

    def handle(self, *args, **options) -> None:
        user_id = options.get("user_id")
        pat = options.get("glob") or "*.jsonl"
        force: bool = bool(options.get("force"))
        dry: bool = bool(options.get("dry_run"))

        User = get_user_model()
        if user_id:
            user = User.objects.filter(id=user_id).first()
        else:
            user = User.objects.order_by("id").first()

        if not user:
            self.stdout.write(self.style.ERROR("[sim_sync_db] user not found"))
            return

        sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"
        if not sim_dir.exists():
            self.stdout.write(self.style.ERROR(f"[sim_sync_db] simulate dir not found: {sim_dir}"))
            return

        paths = sorted(sim_dir.glob(pat))
        if not paths:
            self.stdout.write(self.style.WARNING(f"[sim_sync_db] no files matched: {pat}"))
            return

        total = 0
        created = 0
        updated = 0
        skipped = 0

        for path in paths:
            self.stdout.write(f"[sim_sync_db] reading: {path.name}")

            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  read failed: {e}"))
                continue

            for line in lines:
                raw = line.strip()
                if not raw:
                    continue

                try:
                    rec: Dict[str, Any] = json.loads(raw)
                except Exception:
                    continue

                total += 1

                run_id = rec.get("run_id")
                code = rec.get("code")
                if not run_id or not code:
                    skipped += 1
                    continue

                # 既存があるか
                vt = VirtualTrade.objects.filter(user=user, run_id=str(run_id), code=str(code)).first()

                # 既存あり＆forceなし → 最低限スキップ（eval反映だけ欲しい場合は force 推奨）
                if vt and (not force):
                    skipped += 1
                    continue

                # 値の共通取り出し
                run_date = _parse_date(rec.get("run_date"))
                trade_date = _parse_date(rec.get("trade_date") or rec.get("run_date"))

                opened_at = _parse_dt_iso(rec.get("ts")) or timezone.now()

                defaults = dict(
                    run_date=run_date or timezone.localdate(),
                    trade_date=trade_date or timezone.localdate(),
                    source=str(rec.get("source") or "sim_sync_db"),
                    mode=str(rec.get("mode") or "demo"),
                    code=str(code),
                    name=str(rec.get("name") or ""),
                    sector=str(rec.get("sector") or ""),
                    side=str(rec.get("side") or "BUY"),
                    universe=str(rec.get("universe") or ""),
                    style=str(rec.get("style") or ""),
                    horizon=str(rec.get("horizon") or ""),
                    topk=rec.get("topk") if isinstance(rec.get("topk"), int) else None,
                    score=float(rec["score"]) if rec.get("score") is not None else None,
                    score_100=int(rec["score_100"]) if rec.get("score_100") is not None else None,
                    stars=int(rec["stars"]) if rec.get("stars") is not None else None,
                    mode_period=str(rec.get("mode_period") or rec.get("horizon") or "short"),
                    mode_aggr=str(rec.get("mode_aggr") or "aggr"),
                    entry_px=float(rec["entry"]) if rec.get("entry") is not None else None,
                    tp_px=float(rec["tp"]) if rec.get("tp") is not None else None,
                    sl_px=float(rec["sl"]) if rec.get("sl") is not None else None,
                    last_close=float(rec["last_close"]) if rec.get("last_close") is not None else None,
                    qty_rakuten=int(rec["qty_rakuten"]) if rec.get("qty_rakuten") is not None else None,
                    qty_sbi=int(rec["qty_sbi"]) if rec.get("qty_sbi") is not None else None,
                    qty_matsui=int(rec["qty_matsui"]) if rec.get("qty_matsui") is not None else None,
                    required_cash_rakuten=float(rec["required_cash_rakuten"]) if rec.get("required_cash_rakuten") is not None else None,
                    required_cash_sbi=float(rec["required_cash_sbi"]) if rec.get("required_cash_sbi") is not None else None,
                    required_cash_matsui=float(rec["required_cash_matsui"]) if rec.get("required_cash_matsui") is not None else None,
                    est_pl_rakuten=float(rec["est_pl_rakuten"]) if rec.get("est_pl_rakuten") is not None else None,
                    est_pl_sbi=float(rec["est_pl_sbi"]) if rec.get("est_pl_sbi") is not None else None,
                    est_pl_matsui=float(rec["est_pl_matsui"]) if rec.get("est_pl_matsui") is not None else None,
                    est_loss_rakuten=float(rec["est_loss_rakuten"]) if rec.get("est_loss_rakuten") is not None else None,
                    est_loss_sbi=float(rec["est_loss_sbi"]) if rec.get("est_loss_sbi") is not None else None,
                    est_loss_matsui=float(rec["est_loss_matsui"]) if rec.get("est_loss_matsui") is not None else None,
                    opened_at=opened_at,
                    replay={"sim_order": rec},
                )

                # eval反映（JSONLにあれば）
                if "eval_close_px" in rec or "eval_exit_ts" in rec:
                    defaults["eval_horizon_days"] = rec.get("eval_horizon_days")
                    defaults["eval_label_rakuten"] = str(rec.get("eval_label_rakuten") or "")
                    defaults["eval_label_sbi"] = str(rec.get("eval_label_sbi") or "")
                    defaults["eval_label_matsui"] = str(rec.get("eval_label_matsui") or "")
                    defaults["eval_pl_rakuten"] = rec.get("eval_pl_rakuten")
                    defaults["eval_pl_sbi"] = rec.get("eval_pl_sbi")
                    defaults["eval_pl_matsui"] = rec.get("eval_pl_matsui")
                    defaults["eval_exit_px"] = rec.get("eval_close_px")
                    defaults["eval_exit_reason"] = str(rec.get("eval_exit_reason") or "")
                    defaults["eval_entry_px"] = rec.get("eval_entry_px")
                    defaults["eval_entry_ts"] = _parse_dt_iso(rec.get("eval_entry_ts"))
                    defaults["eval_exit_ts"] = _parse_dt_iso(rec.get("eval_exit_ts"))

                    # closed_at を埋める
                    exit_ts = defaults.get("eval_exit_ts")
                    if exit_ts:
                        defaults["closed_at"] = exit_ts

                if dry:
                    if vt:
                        updated += 1
                    else:
                        created += 1
                    continue

                obj, is_created = VirtualTrade.objects.update_or_create(
                    user=user,
                    run_id=str(run_id),
                    code=str(code),
                    defaults=defaults,
                )

                # R計算
                obj.recompute_r()
                obj.save(update_fields=["result_r_rakuten", "result_r_sbi", "result_r_matsui"])

                if is_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"[sim_sync_db] total={total} created={created} updated={updated} skipped={skipped} dry_run={dry}"
        ))