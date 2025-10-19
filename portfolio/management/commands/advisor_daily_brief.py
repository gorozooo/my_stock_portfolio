# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Any, List, Optional

from django.core.management.base import BaseCommand, CommandParser
from django.template.loader import render_to_string
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from django.utils import timezone

# 既存サービスを利用
from ...services.market import (
    latest_breadth, breadth_regime,
    fetch_indexes_snapshot, latest_sector_strength
)
from ...services.sector_map import normalize_sector
from ...models_advisor import AdviceItem

# ---------- ユーティリティ ----------
def _media_root() -> str:
    return getattr(settings, "MEDIA_ROOT", "") or os.getcwd()

def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)

def _today_str(d: Optional[date] = None) -> str:
    d = d or date.today()
    return d.strftime("%Y-%m-%d")

def _safe_float(x, d=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return d

@dataclass
class BriefContext:
    asof: str
    generated_at: str
    breadth: Dict[str, Any]
    breadth_view: Dict[str, Any]
    indexes: Dict[str, Dict[str, Any]]
    sectors: List[Dict[str, Any]]
    week_stats: Dict[str, Any]
    notes: List[str]

# ---------- コマンド本体 ----------
class Command(BaseCommand):
    help = "朝のAIデイリーブリーフを生成（HTML/MD保存＆任意でメール送信）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--date", type=str, default="", help="対象日(YYYY-MM-DD)。未指定は今日")
        parser.add_argument("--to", type=str, default="", help="送信先メール（カンマ区切り可）")
        parser.add_argument("--subject", type=str, default="", help="メール件名を上書き")
        parser.add_argument("--outdir", type=str, default="media/reports", help="保存ディレクトリ")
        parser.add_argument("--days", type=int, default=90, help="週次サマリのlookback（日数）")

    def handle(self, *args, **opts):
        asof_str = opts["date"] or _today_str()
        try:
            asof_date = datetime.fromisoformat(asof_str).date()
        except Exception:
            return self.stdout.write(self.style.ERROR(f"invalid --date: {asof_str}"))

        # ---- 1) 市況（breadth）
        b = latest_breadth() or {}
        regime = breadth_regime(b)

        # ---- 2) 指数スナップショット
        idx = fetch_indexes_snapshot() or {}

        # ---- 3) セクターRS（最新テーブルを可視化向けに整形）
        rs_tbl = latest_sector_strength() or {}
        sectors_view: List[Dict[str, Any]] = []
        for raw_sec, row in rs_tbl.items():
            sec = normalize_sector(raw_sec)
            sectors_view.append({
                "sector": sec,
                "rs": _safe_float(row.get("rs_score")),
                "date": row.get("date") or "",
            })
        # 強い順
        sectors_view.sort(key=lambda r: r["rs"], reverse=True)

        # ---- 4) 通知採用の週次サマリ（今週）
        now = timezone.localtime()
        monday = now - timedelta(days=now.weekday())
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)

        since = timezone.now() - timedelta(days=int(opts["days"] or 90))
        qs_all = AdviceItem.objects.filter(created_at__gte=since)
        week_qs = qs_all.filter(created_at__gte=monday)
        week_total = week_qs.count()
        week_taken = week_qs.filter(taken=True).count()
        week_rate = (week_taken / week_total) if week_total > 0 else 0.0

        week_stats = dict(total=week_total, taken=week_taken, rate=round(week_rate, 4))

        # ---- 5) 付記（注意書き）
        notes: List[str] = []
        if not b:
            notes.append("breadth.json が見つからないため既定ハンドリング。")
        if not rs_tbl:
            notes.append("セクターRSが見つからない（latest_sector_strength() 空）。")
        if not idx:
            notes.append("indexes snapshot が空。")

        ctx = BriefContext(
            asof=asof_str,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            breadth=b,
            breadth_view=regime,
            indexes=idx,
            sectors=sectors_view,
            week_stats=week_stats,
            notes=notes,
        )

        # ---- 6) レンダリング（HTML & Markdown）
        outdir = opts["outdir"] or "media/reports"
        _ensure_dir(outdir)

        html = self._render_html(ctx)
        md = self._render_md(ctx)

        html_path = os.path.join(outdir, f"daily_brief_{asof_str}.html")
        md_path = os.path.join(outdir, f"daily_brief_{asof_str}.md")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)

        self.stdout.write(self.style.SUCCESS(f"Wrote: {html_path}"))
        self.stdout.write(self.style.SUCCESS(f"Wrote: {md_path}"))

        # ---- 7) メール送信（任意）
        to = [x.strip() for x in (opts["to"] or "").split(",") if x.strip()]
        if to:
            subject = opts["subject"] or f"[AI Brief] 市況ダイジェスト {asof_str}"
            self._send_mail(subject, to, html, md)
            self.stdout.write(self.style.SUCCESS(f"Mail sent to: {', '.join(to)}"))

    # ---------- テンプレ描画 ----------
    def _render_html(self, ctx: BriefContext) -> str:
        try:
            return render_to_string("emails/advisor_daily_brief.html", {"ctx": ctx})
        except Exception:
            # テンプレが無い場合の簡易HTML
            return f"""<!doctype html><meta charset="utf-8">
            <h2>AI デイリーブリーフ {ctx.asof}</h2>
            <p>Generated at {ctx.generated_at}</p>
            <h3>地合い</h3>
            <pre>{json.dumps(ctx.breadth_view, ensure_ascii=False, indent=2)}</pre>
            <h3>指数</h3>
            <pre>{json.dumps(ctx.indexes, ensure_ascii=False, indent=2)}</pre>
            <h3>セクターRS（上位）</h3>
            <pre>{json.dumps(ctx.sectors[:10], ensure_ascii=False, indent=2)}</pre>
            <h3>今週の通知</h3>
            <pre>{json.dumps(ctx.week_stats, ensure_ascii=False, indent=2)}</pre>
            <h3>Notes</h3>
            <pre>{json.dumps(ctx.notes, ensure_ascii=False, indent=2)}</pre>
            """

    def _render_md(self, ctx: BriefContext) -> str:
        # 軽量なMarkdown（メールのtextパートや保存用）
        top_secs = "\n".join([f"- {r['sector']}: RS {r['rs']:+.2f}" for r in ctx.sectors[:10]])
        return (
f"""# AI デイリーブリーフ {ctx.asof}

生成: {ctx.generated_at}

## 地合い（Breadth）
- Regime: **{ctx.breadth_view.get('regime', 'NEUTRAL')}**
- Score: {ctx.breadth_view.get('score', 0.0)}
- A/D: {ctx.breadth_view.get('ad_ratio', 1.0)} / VOL: {ctx.breadth_view.get('vol_ratio', 1.0)} / H-L: {ctx.breadth_view.get('hl_diff', 0)}

## 指数スナップショット（抜粋）
"""
        +
"\n".join([f"- {sym}: 5日={ctx.indexes.get(sym,{}).get('ret_5d',0.0):+.2f} / 20日={ctx.indexes.get(sym,{}).get('ret_20d',0.0):+.2f}"
           for sym in list(ctx.indexes.keys())[:8]]) +
f"""

## セクターRS（上位10）
{top_secs or '- なし'}

## 今週の通知サマリ
- 通知: {ctx.week_stats['total']:,}
- 採用: {ctx.week_stats['taken']:,}
- 採用率: {ctx.week_stats['rate']*100:.1f}%

## Notes
"""
        + ("\n".join([f"- {n}" for n in (ctx.notes or ["なし"])]) )
        )

    # ---------- メール送信 ----------
    def _send_mail(self, subject: str, to: List[str], html: str, text: str) -> None:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=to
        )
        msg.attach_alternative(html, "text/html")
        msg.send(fail_silently=False)