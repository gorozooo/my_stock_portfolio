# aiapp/management/commands/ai_sim_eval.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade
from aiapp.services.sim_eval_service import eval_sim_record


def _ensure_trade_date(rec: Dict[str, Any]) -> None:
    td = rec.get("trade_date")
    if isinstance(td, str) and td:
        return

    run_date = rec.get("run_date")
    if isinstance(run_date, str) and run_date:
        rec["trade_date"] = run_date
        return

    price_date = rec.get("price_date")
    if isinstance(price_date, str) and price_date:
        rec["trade_date"] = price_date
        return

    ts_str = rec.get("ts")
    if isinstance(ts_str, str) and ts_str:
        try:
            dt = timezone.datetime.fromisoformat(ts_str)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_default_timezone())
            rec["trade_date"] = timezone.localtime(dt).date().isoformat()
            return
        except Exception:
            pass


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


def _pick_ev_true_pack(updated: Dict[str, Any]) -> Dict[str, Any]:
    """
    UIが取りやすい形で replay に置く EV_true パック。
    - 既に ev_true_rakuten 等がある前提
    - ついでに “短縮キー” も作っておく（R/M/S）
    """
    ev_r = updated.get("ev_true_rakuten")
    ev_m = updated.get("ev_true_matsui")
    ev_s = updated.get("ev_true_sbi")
    return {
        "rakuten": ev_r,
        "matsui": ev_m,
        "sbi": ev_s,
        "R": ev_r,
        "M": ev_m,
        "S": ev_s,
    }


class Command(BaseCommand):
    help = "AIシミュレログに結果（PL / ラベル / exit情報）を付与 + VirtualTrade同期"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=getattr(settings, "AIAPP_SIM_HORIZON_DAYS", 5),
            help="評価に使う営業日数",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="既に eval_* が付与されていても再評価する",
        )

    def handle(self, *args, **options) -> None:
        horizon_days: int = options["days"]
        force: bool = options["force"]

        sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"
        if not sim_dir.exists():
            self.stdout.write(self.style.WARNING(f"[ai_sim_eval] dir not found: {sim_dir}"))
            return

        self.stdout.write(f"[ai_sim_eval] dir={sim_dir} horizon_days={horizon_days} force={force}")

        total = 0
        evaluated = 0
        db_updated = 0
        db_missed = 0

        for path in sorted(sim_dir.glob("*.jsonl")):
            self.stdout.write(f"  読み込み中: {path.name}")

            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"    読み込み失敗: {e}"))
                continue

            new_lines: List[str] = []

            for line in lines:
                raw = line.strip()
                if not raw:
                    continue

                try:
                    rec: Dict[str, Any] = json.loads(raw)
                except Exception:
                    new_lines.append(raw)
                    continue

                total += 1

                already_has_eval = (
                    "eval_label_rakuten" in rec
                    or "eval_label_matsui" in rec
                    or "eval_close_px" in rec
                )
                if already_has_eval and not force:
                    new_lines.append(json.dumps(rec, ensure_ascii=False))
                    continue

                _ensure_trade_date(rec)

                try:
                    updated = eval_sim_record(rec, horizon_days=horizon_days)
                except Exception as e:
                    code = rec.get("code")
                    ts = rec.get("ts")
                    self.stdout.write(self.style.ERROR(f"    評価エラー: {e} (file={path.name}, code={code}, ts={ts})"))
                    updated = rec

                new_lines.append(json.dumps(updated, ensure_ascii=False))
                evaluated += 1

                # ---- DB sync ----
                try:
                    user_id = updated.get("user_id")
                    run_id = updated.get("run_id")
                    code = updated.get("code")

                    if not (user_id and run_id and code):
                        db_missed += 1
                        continue

                    try:
                        vt = VirtualTrade.objects.get(user_id=int(user_id), run_id=str(run_id), code=str(code))
                    except VirtualTrade.DoesNotExist:
                        db_missed += 1
                        continue

                    vt.eval_horizon_days = updated.get("eval_horizon_days")

                    vt.eval_label_rakuten = str(updated.get("eval_label_rakuten") or "")
                    vt.eval_label_matsui = str(updated.get("eval_label_matsui") or "")
                    vt.eval_label_sbi = str(updated.get("eval_label_sbi") or "")

                    vt.eval_pl_rakuten = updated.get("eval_pl_rakuten")
                    vt.eval_pl_matsui = updated.get("eval_pl_matsui")
                    vt.eval_pl_sbi = updated.get("eval_pl_sbi")

                    vt.eval_exit_px = updated.get("eval_close_px")
                    vt.eval_exit_reason = str(updated.get("eval_exit_reason") or "")

                    vt.eval_entry_px = updated.get("eval_entry_px")

                    entry_ts = _parse_dt_iso(updated.get("eval_entry_ts"))
                    exit_ts = _parse_dt_iso(updated.get("eval_exit_ts"))
                    vt.eval_entry_ts = entry_ts
                    vt.eval_exit_ts = exit_ts

                    # “強制クローズ完了” の本体：closed_at を埋める
                    # ※ SKIP の場合、vt側で既に closed_at が入っている可能性あり（それはそれでOK）
                    if exit_ts is not None and vt.closed_at is None:
                        vt.closed_at = exit_ts

                    # replayにも最新評価を残す（デバッグ用）
                    rp = vt.replay or {}
                    rp["last_eval"] = updated

                    # ★今回の肝：UIで直接見えるキーを replay のトップにも置く
                    # - rank
                    # - rank_group
                    # - ev_true（R/M/S）
                    # - ついでに eval_status/evaluated_at も置く（表示や原因追跡が楽）
                    if "rank" in updated:
                        rp["rank"] = updated.get("rank")
                    if "rank_group" in updated:
                        rp["rank_group"] = updated.get("rank_group")

                    rp["ev_true"] = _pick_ev_true_pack(updated)

                    if "eval_status" in updated:
                        rp["eval_status"] = updated.get("eval_status")
                    if "evaluated_at" in updated:
                        rp["evaluated_at"] = updated.get("evaluated_at")

                    # skip 情報も “トップ” に持ち上げ（探しやすさ）
                    if "skip_reason" in updated:
                        rp["skip_reason"] = updated.get("skip_reason")
                    if "skip_msg" in updated:
                        rp["skip_msg"] = updated.get("skip_msg")

                    vt.replay = rp

                    # R を計算して保存
                    vt.recompute_r()

                    vt.save(update_fields=[
                        "eval_horizon_days",
                        "eval_label_rakuten", "eval_label_matsui", "eval_label_sbi",
                        "eval_pl_rakuten", "eval_pl_matsui", "eval_pl_sbi",
                        "eval_exit_px", "eval_exit_reason",
                        "eval_entry_px", "eval_entry_ts",
                        "eval_exit_ts", "closed_at",
                        "replay",
                        "result_r_rakuten", "result_r_sbi", "result_r_matsui",
                    ])
                    db_updated += 1

                except Exception:
                    db_missed += 1
                    continue

            backup = path.with_suffix(path.suffix + ".bak")
            try:
                if backup.exists():
                    backup.unlink()
                path.replace(backup)
                path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                self.stdout.write(self.style.SUCCESS(f"  → 書き込み完了: {path.name} (backup: {backup.name})"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  書き込み失敗: {e}"))
                if backup.exists() and not path.exists():
                    backup.replace(path)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"[ai_sim_eval] 全レコード: {total} / 評価: {evaluated}"))
        self.stdout.write(self.style.SUCCESS(f"[ai_sim_eval] DB更新: {db_updated} / DB取りこぼし: {db_missed}"))
        self.stdout.write(self.style.SUCCESS("[ai_sim_eval] 完了"))