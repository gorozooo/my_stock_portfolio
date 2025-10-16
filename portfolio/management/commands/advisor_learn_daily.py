# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from pathlib import Path
from typing import List

from django.core.management.base import BaseCommand, CommandParser
from django.core.management import call_command
from django.core.mail import send_mail
from django.conf import settings

class Command(BaseCommand):
    help = "advisor_learn(days=90) を実行し、結果要約をメール送信（毎日運用向け）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=90, help="学習対象の過去日数（default=90）")
        parser.add_argument("--out", type=str, default="media/advisor/policy.json",
                            help="出力先（default=media/advisor/policy.json）")
        parser.add_argument("--bias", type=float, default=1.0, help="全体バイアス倍率")
        parser.add_argument("--clip-low", type=float, default=0.80, dest="clip_low", help="重みの下限")
        parser.add_argument("--clip-high", type=float, default=1.30, dest="clip_high", help="重みの上限")
        parser.add_argument("--to", type=str, default=getattr(settings, "ADMIN_EMAIL", ""),
                            help="通知先メール。カンマ区切り可。未指定なら settings.ADMIN_EMAIL")
        parser.add_argument("--subject", type=str, default="[AI Advisor] policy学習(毎日)",
                            help="メール件名")
        parser.add_argument("--print", action="store_true", help="標準出力にも要約を出す")

    def handle(self, *args, **opts):
        days      = int(opts["days"])
        out_path  = str(opts["out"])
        bias      = float(opts["bias"])
        clip_low  = float(opts["clip_low"])
        clip_high = float(opts["clip_high"])
        subject   = str(opts["subject"])
        to_list   = [x.strip() for x in str(opts["to"] or "").split(",") if x.strip()]
        do_print  = bool(opts["print"])

        # 1) 学習コマンドを実行（既存の advisor_learn をラップ）
        call_command(
            "advisor_learn",
            days=days,
            out=out_path,
            bias=bias,
            clip_low=clip_low,
            clip_high=clip_high,
            verbosity=1,
        )

        # 2) policy.json を読んで要約
        p = Path(out_path)
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            obj = {}

        kind_weight = obj.get("kind_weight") or {}
        kinds: List[str] = sorted(kind_weight.keys())
        lines = []
        lines.append(f"✅ policy.json を更新しました（days={days}, kinds={len(kinds)}）")
        lines.append(str(p.resolve()))
        lines.append("")
        if kinds:
            # 上位5件を見やすく
            top = sorted(kind_weight.items(), key=lambda x: x[1], reverse=True)[:5]
            lines.append("Top weights:")
            for k, w in top:
                lines.append(f"  - {k}: {w:.3f}")
        if obj.get("updated_at"):
            lines.append("")
            lines.append(f"updated_at: {obj['updated_at']}")
        body = "\n".join(lines)

        if do_print:
            self.stdout.write(body)

        # 3) メール送信
        if to_list:
            send_mail(
                subject=subject,
                message=body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
                recipient_list=to_list,
                fail_silently=False,
            )
            self.stdout.write(self.style.SUCCESS(f"Sent mail to {', '.join(to_list)}"))
        else:
            self.stdout.write(self.style.WARNING("No recipient (--to または settings.ADMIN_EMAIL)。メール送信はスキップしました。"))