"""
[FILE] shihyo_fetch.py
[PATH] <project_root>/shihyo/management/commands/shihyo_fetch.py

このファイルは何？
- 指標（先物・ドル円・VIX）をStooqから取得してDBへ保存するコマンドです。
- cronからこれを叩けば、アプリは「DBの最新スナップショット」を表示するだけでOKになります。

今回の修正ポイント：
- SQLiteのJSONFieldは NaN/Infinity をJSONとして扱えず保存に失敗するため、
  raw_payload を保存する前に NaN/Infinity を None に変換して安全化します。
"""

from __future__ import annotations

import math
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from shihyo.models import MarketIndicatorSnapshot
from shihyo.services.stooq_client import StooqClient
from shihyo.services.judge import judge


def _json_safe(x: Any) -> Any:
    """
    JSONField(SQLite)が受け付けない値（NaN/Infinity）を None に変換して安全化する。
    dict/list/tuple も再帰的に処理する。
    """
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    if isinstance(x, dict):
        return {str(k): _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    return x


class Command(BaseCommand):
    help = "Fetch Nikkei futures, USDJPY, VIX and store snapshot."

    def handle(self, *args, **options):
        client = StooqClient()

        # Stooq symbols
        symbols = {
            "nikkei": "ny.f",   # Nikkei 225 futures (Stooq)
            "fx": "usdjpy",     # USDJPY
            "vix": "vi.c",      # S&P 500 VIX
        }

        raw = {}
        err = ""

        try:
            q_n = client.fetch_latest(symbols["nikkei"])
            q_f = client.fetch_latest(symbols["fx"])
            q_v = client.fetch_latest(symbols["vix"])

            n = client.calc_change(q_n.close, q_n.open)
            f = client.calc_change(q_f.close, q_f.open)
            v = client.calc_change(q_v.close, q_v.open)

            judged = judge(
                nikkei_change_pct=n["pct"],
                fx_change_pct=f["pct"],
                vix_last=q_v.close,
                vix_change_pct=v["pct"],
            )

            raw = {
                "nikkei": q_n.__dict__,
                "fx": q_f.__dict__,
                "vix": q_v.__dict__,
                "calc": {"nikkei": n, "fx": f, "vix": v},
                "fetched_at": timezone.now().isoformat(),
            }
            raw = _json_safe(raw)  # ★ここが今回のキモ

            MarketIndicatorSnapshot.objects.create(
                nikkei_futures_last=q_n.close,
                nikkei_futures_change=n["change"],
                nikkei_futures_change_pct=n["pct"],
                usdjpy_last=q_f.close,
                usdjpy_change=f["change"],
                usdjpy_change_pct=f["pct"],
                vix_last=q_v.close,
                vix_change=v["change"],
                vix_change_pct=v["pct"],
                nikkei_label=judged.nikkei_label,
                fx_label=judged.fx_label,
                vix_label=judged.vix_label,
                action_title=judged.action_title,
                action_lines=judged.action_lines,
                raw_payload=raw,
                error="",
            )

            self.stdout.write(self.style.SUCCESS("Saved snapshot OK"))

        except Exception as e:
            err = str(e)

            # エラー時も raw_payload には「安全なJSON」だけ入れる
            safe_raw = _json_safe({
                "fetched_at": timezone.now().isoformat(),
                "error": err,
                "raw": raw,
            })

            MarketIndicatorSnapshot.objects.create(
                action_title="🔴 取得失敗（前回の判断を優先）",
                action_lines=[
                    "データ取得に失敗したため、新しい判断を作れませんでした",
                    "通信が戻ったら自動で更新されます",
                ],
                raw_payload=safe_raw,
                error=err,
            )

            self.stdout.write(self.style.ERROR(f"Saved snapshot ERROR: {err}"))