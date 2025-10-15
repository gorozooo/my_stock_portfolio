# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, List

from django.core.management.base import BaseCommand, CommandParser
from django.core.mail import send_mail
from django.conf import settings

from ...models_advisor import AdviceSession, AdviceItem
from ...services.insights import generate_insights  # 追加：改善要因

def _fmt_pct(v):
    return "--" if v is None else f"{v:.2f}%"

def _read_self_score() -> str:
    """policy.json の self_score を読む（なければ '—'）"""
    base = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
    for rel in ("media/advisor/policy.json", "advisor/policy.json"):
        p = os.path.join(base, rel)
        if os.path.exists(p):
            try:
                import json
                with open(p, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                sc = obj.get("self_score", None)
                return f"{sc:+.3f}" if sc is not None else "—"
            except Exception:
                pass
    return "—"

def _summarize_latest() -> Dict:
    s = AdviceSession.objects.order_by("-created_at").first()
    if not s:
        return {"exists": False}

    k = s.context_json or {}
    items = list(AdviceItem.objects.filter(session=s).order_by("-score", "-id"))[:5]

    lines: List[str] = []
    lines.append(f"🧠 AI週次レポート（{s.created_at:%Y-%m-%d}）")
    lines.append("")
    lines.append(f"総資産: ¥{k.get('total_assets', 0):,}")
    lines.append(f"評価ROI: {_fmt_pct(k.get('roi_eval_pct'))} / 現金ROI: {_fmt_pct(k.get('roi_liquid_pct'))}")
    gap = k.get("roi_gap_abs")
    if gap is not None:
        lines.append(f"ROI乖離: {gap:.1f}pt")
    lines.append(f"現金: ¥{k.get('cash_total', 0):,} / 流動性: {k.get('liquidity_rate_pct', 0):.1f}% / 信用比率: {k.get('margin_ratio_pct', 0):.1f}%")
    # ▼ 追加：AI 自己評価
    lines.append(f"AI自己評価(self_score): {_read_self_score()}")
    lines.append("")
    lines.append("▶ 提案（上位）")
    if not items:
        lines.append("・提案なし")
    else:
        for it in items:
            chk = "✅" if it.taken else "☐"
            lines.append(f"{chk} {it.message}  (優先度 {it.score:.2f})")

    body = "\n".join(lines)
    return {"exists": True, "body": body}

class Command(BaseCommand):
    help = "最新セッションから週次レポートを作成し、メール送信します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--to",
            type=str,
            default=getattr(settings, "ADMIN_EMAIL", ""),
            help="送信先メール（カンマ区切り可）。未指定なら settings.ADMIN_EMAIL を使用",
        )
        parser.add_argument("--subject", type=str, default="🧠 AI週次レポート", help="件名")

    def handle(self, *args, **opts):
        summary = _summarize_latest()
        if not summary["exists"]:
            self.stdout.write(self.style.WARNING("No sessions yet."))
            return

        to_raw = (opts["to"] or getattr(settings, "ADMIN_EMAIL", "") or "").strip()
        to = [x.strip() for x in to_raw.split(",") if x.strip()]
        if not to:
            self.stdout.write(self.style.WARNING("No recipient (--to or settings.ADMIN_EMAIL). Only printing."))
            self.stdout.write(summary["body"])
            return

        send_mail(
            subject=opts["subject"],
            message=summary["body"],
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
            recipient_list=to,
            fail_silently=False,
        )
        self.stdout.write(self.style.SUCCESS(f"Weekly digest sent to {', '.join(to)}"))