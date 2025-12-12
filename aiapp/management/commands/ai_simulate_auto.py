# aiapp/management/commands/ai_simulate_auto.py
# -*- coding: utf-8 -*-
"""
ai_simulate_auto

紙トレ自動エントリー（DEMO）コマンド。

役割:
- media/aiapp/picks/latest_full.json を読み込む
- TopK の注文を JSONL に起票する（既存パイプライン互換）
- 同時に VirtualTrade(DB) に "OPEN" として同期する（UI/⭐️集計用）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade


# ========= パス定義（MEDIA_ROOT ベース） =========
PICKS_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "picks"
SIM_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"


# ========= 時刻ユーティリティ（JST固定） =========
def _now_jst():
    from datetime import datetime, timezone as _tz, timedelta
    JST = _tz(timedelta(hours=9))
    return datetime.now(JST)

def dt_now_jst_iso() -> str:
    return _now_jst().isoformat()

def today_jst_str() -> str:
    return _now_jst().date().isoformat()

def dt_now_run_id(prefix: str = "auto") -> str:
    n = _now_jst()
    return n.strftime("%Y%m%d_%H%M%S") + f"_{prefix}"


def _parse_date(s: str):
    from datetime import date as _date
    return _date.fromisoformat(s)

def _parse_dt_iso(ts: str) -> Optional[timezone.datetime]:
    try:
        dt = timezone.datetime.fromisoformat(ts)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


class Command(BaseCommand):
    help = "AIフル自動シミュレ用：DEMO紙トレ注文を JSONL に起票 + VirtualTrade同期"

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD（指定がなければJSTの今日）")
        parser.add_argument("--overwrite", action="store_true", help="同じ日付の jsonl を上書き")
        parser.add_argument("--mode-period", type=str, default="short", help="short/mid/long（将来拡張）")
        parser.add_argument("--mode-aggr", type=str, default="aggr", help="aggr/norm/def（将来拡張）")

    def handle(self, *args, **options):
        run_date_str: str = options.get("date") or today_jst_str()
        overwrite: bool = bool(options.get("overwrite"))

        mode_period: str = (options.get("mode_period") or "short").strip().lower()
        mode_aggr: str = (options.get("mode_aggr") or "aggr").strip().lower()

        trade_date_str = run_date_str

        picks_path = PICKS_DIR / "latest_full.json"
        if not picks_path.exists():
            self.stdout.write(self.style.WARNING(f"[ai_simulate_auto] picks not found: {picks_path}"))
            return

        try:
            data = json.loads(picks_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[ai_simulate_auto] picks load error: {e}"))
            return

        meta: Dict[str, Any] = data.get("meta") or {}
        items: List[Dict[str, Any]] = data.get("items") or []
        if not items:
            self.stdout.write(self.style.WARNING("[ai_simulate_auto] items=0"))
            return

        User = get_user_model()
        user = User.objects.order_by("id").first()
        if not user:
            self.stdout.write(self.style.ERROR("[ai_simulate_auto] no user found"))
            return

        user_id = user.id

        style = (meta.get("style") or "aggressive")
        horizon = (meta.get("horizon") or "short")
        universe = (meta.get("universe") or "unknown")
        topk = meta.get("topk")

        run_id = dt_now_run_id(prefix="auto_demo")

        SIM_DIR.mkdir(parents=True, exist_ok=True)
        out_path = SIM_DIR / f"sim_orders_{run_date_str}.jsonl"
        file_mode = "w" if overwrite else "a"

        ts_iso = dt_now_jst_iso()
        opened_at_dt = _parse_dt_iso(ts_iso) or timezone.now()

        run_date = _parse_date(run_date_str)
        trade_date = _parse_date(trade_date_str)

        written = 0
        upserted = 0

        with out_path.open(file_mode, encoding="utf-8") as fw:
            for it in items:
                code = (it.get("code") or "").strip()
                if not code:
                    continue

                name = it.get("name")
                sector = it.get("sector_display")

                side = "BUY"
                rec_mode = "demo"

                entry = it.get("entry", it.get("last_close"))
                tp = it.get("tp")
                sl = it.get("sl")
                last_close = it.get("last_close")

                qty_rakuten = it.get("qty_rakuten")
                qty_sbi = it.get("qty_sbi")
                qty_matsui = it.get("qty_matsui")

                est_pl_rakuten = it.get("est_pl_rakuten")
                est_pl_sbi = it.get("est_pl_sbi")
                est_pl_matsui = it.get("est_pl_matsui")

                est_loss_rakuten = it.get("est_loss_rakuten")
                est_loss_sbi = it.get("est_loss_sbi")
                est_loss_matsui = it.get("est_loss_matsui")

                required_cash_rakuten = it.get("required_cash_rakuten")
                required_cash_sbi = it.get("required_cash_sbi")
                required_cash_matsui = it.get("required_cash_matsui")

                score = it.get("score")
                score_100 = it.get("score_100")
                stars = it.get("stars")

                rec: Dict[str, Any] = {
                    "user_id": user_id,
                    "mode": rec_mode,
                    "ts": ts_iso,
                    "run_date": run_date_str,
                    "trade_date": trade_date_str,
                    "run_id": run_id,
                    "code": code,
                    "name": name,
                    "sector": sector,
                    "side": side,
                    "entry": entry,
                    "tp": tp,
                    "sl": sl,
                    "last_close": last_close,
                    "qty_rakuten": qty_rakuten,
                    "qty_sbi": qty_sbi,
                    "qty_matsui": qty_matsui,
                    "est_pl_rakuten": est_pl_rakuten,
                    "est_pl_sbi": est_pl_sbi,
                    "est_pl_matsui": est_pl_matsui,
                    "est_loss_rakuten": est_loss_rakuten,
                    "est_loss_sbi": est_loss_sbi,
                    "est_loss_matsui": est_loss_matsui,
                    "required_cash_rakuten": required_cash_rakuten,
                    "required_cash_sbi": required_cash_sbi,
                    "required_cash_matsui": required_cash_matsui,
                    "score": score,
                    "score_100": score_100,
                    "stars": stars,
                    "style": style,
                    "horizon": horizon,
                    "universe": universe,
                    "topk": topk,
                    "source": "ai_simulate_auto",
                }

                fw.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1

                # ---- DB sync (OPEN) ----
                defaults = dict(
                    run_date=run_date,
                    trade_date=trade_date,
                    source="ai_simulate_auto",
                    mode=rec_mode,
                    code=code,
                    name=name or "",
                    sector=sector or "",
                    side=side,
                    universe=str(universe or ""),
                    style=str(style or ""),
                    horizon=str(horizon or ""),
                    topk=topk if isinstance(topk, int) else None,
                    score=score if score is None else float(score),
                    score_100=score_100 if score_100 is None else int(score_100),
                    stars=stars if stars is None else int(stars),
                    mode_period=mode_period,
                    mode_aggr=mode_aggr,
                    entry_px=entry if entry is None else float(entry),
                    tp_px=tp if tp is None else float(tp),
                    sl_px=sl if sl is None else float(sl),
                    last_close=last_close if last_close is None else float(last_close),
                    qty_rakuten=qty_rakuten if qty_rakuten is None else int(qty_rakuten),
                    qty_sbi=qty_sbi if qty_sbi is None else int(qty_sbi),
                    qty_matsui=qty_matsui if qty_matsui is None else int(qty_matsui),
                    required_cash_rakuten=required_cash_rakuten if required_cash_rakuten is None else float(required_cash_rakuten),
                    required_cash_sbi=required_cash_sbi if required_cash_sbi is None else float(required_cash_sbi),
                    required_cash_matsui=required_cash_matsui if required_cash_matsui is None else float(required_cash_matsui),
                    est_pl_rakuten=est_pl_rakuten if est_pl_rakuten is None else float(est_pl_rakuten),
                    est_pl_sbi=est_pl_sbi if est_pl_sbi is None else float(est_pl_sbi),
                    est_pl_matsui=est_pl_matsui if est_pl_matsui is None else float(est_pl_matsui),
                    est_loss_rakuten=est_loss_rakuten if est_loss_rakuten is None else float(est_loss_rakuten),
                    est_loss_sbi=est_loss_sbi if est_loss_sbi is None else float(est_loss_sbi),
                    est_loss_matsui=est_loss_matsui if est_loss_matsui is None else float(est_loss_matsui),
                    opened_at=opened_at_dt,
                    replay={"sim_order": rec},
                )

                obj, created = VirtualTrade.objects.update_or_create(
                    user=user,
                    run_id=run_id,
                    code=code,
                    defaults=defaults,
                )
                upserted += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"[ai_simulate_auto] run_id={run_id} run_date={run_date_str} user_id={user_id} "
                f"jsonl_written={written} db_upserted={upserted} -> {out_path}"
            )
        )