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

# LINE 送信用（既存）
try:
    from ...models_line import LineContact
    from ...services.line_api import push as line_push
except Exception:
    LineContact = None
    line_push = None


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

def _fmt_pct(x, nd=2, signed=False):
    try:
        v = float(x)
    except Exception:
        return "—"
    s = f"{v:.{nd}f}"
    if signed and v >= 0:
        s = f"+{s}"
    return s

def _fmt_num(x, nd=0):
    try:
        v = float(x)
    except Exception:
        return "—"
    if nd == 0:
        return f"{v:,.0f}"
    return f"{v:,.{nd}f}"

def _split_chunks(s: str, limit: int = 4500) -> List[str]:
    """
    LINEのテキスト上限（5000字）を安全側でチャンク。改行で良きところで切る。
    """
    if len(s) <= limit:
        return [s]
    out, buf = [], []
    size = 0
    for line in s.splitlines(True):  # keepends
        if size + len(line) > limit and buf:
            out.append("".join(buf).rstrip())
            buf, size = [line], len(line)
        else:
            buf.append(line)
            size += len(line)
    if buf:
        out.append("".join(buf).rstrip())
    return out


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
    help = "朝のAIデイリーブリーフを生成（HTML/MD保存＆任意でメール送信／LINE送信）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--date", type=str, default="", help="対象日(YYYY-MM-DD)。未指定は今日")
        parser.add_argument("--to", type=str, default="", help="送信先メール（カンマ区切り可）")
        parser.add_argument("--subject", type=str, default="", help="メール件名を上書き")
        parser.add_argument("--outdir", type=str, default="media/reports", help="保存ディレクトリ")
        parser.add_argument("--days", type=int, default=90, help="週次サマリのlookback（日数）")

        # ==== LINE 送信用オプション ====
        parser.add_argument("--line", action="store_true", help="LINEへも送信する")
        parser.add_argument("--line-to", type=str, default="", help="LINE送信先user_id（カンマ区切り）。未指定で --line-all or 最新1件を推測")
        parser.add_argument("--line-all", action="store_true", help="登録済みLineContactの全員に送る")
        parser.add_argument("--line-title", type=str, default="", help="LINE先頭タイトル（未指定は既定）")
        parser.add_argument("--line-max-sectors", type=int, default=10, help="LINE本文のセクター上位件数")

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
        md = self._render_md(ctx, sector_top=int(opts["line_max_sectors"] or 10))

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

        # ---- 8) LINE送信（任意）
        if opts["line"]:
            if line_push is None:
                self.stdout.write(self.style.WARNING("LINE送信が有効化されていません（services.line_api / models_line 不在）。"))
            else:
                self._send_line(ctx, md, opts)

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

    def _render_md(self, ctx: BriefContext, sector_top: int = 10) -> str:
        # LINEとメールtextで共用する軽量テキスト
        # 指数は上位数件のみ
        idx_syms = list(ctx.indexes.keys())[:8]
        idx_lines = [
            f"- {sym}: 5日={_fmt_pct(ctx.indexes.get(sym,{}).get('ret_5d',0.0), 2, True)} / 20日={_fmt_pct(ctx.indexes.get(sym,{}).get('ret_20d',0.0), 2, True)}"
            for sym in idx_syms
        ]
        top_secs = "\n".join([f"- {r['sector']}: RS {float(r['rs']):+0.2f}" for r in ctx.sectors[:sector_top]]) or "- なし"

        notes_lines = "\n".join([f"- {n}" for n in (ctx.notes or ["なし"])])

        text = (
f"""# AI デイリーブリーフ {ctx.asof}

生成: {ctx.generated_at}

■ 地合い（Breadth）
- Regime: **{ctx.breadth_view.get('regime', 'NEUTRAL')}**
- Score: {ctx.breadth_view.get('score', 0.0)}
- A/D: {ctx.breadth_view.get('ad_ratio', 1.0)} / VOL: {ctx.breadth_view.get('vol_ratio', 1.0)} / H-L: {ctx.breadth_view.get('hl_diff', 0)}

■ 指数スナップショット（抜粋）
"""
        + "\n".join(idx_lines)
        + f"""

■ セクターRS（上位{sector_top}）
{top_secs}

■ 今週の通知サマリ
- 通知: {_fmt_num(ctx.week_stats['total'])}
- 採用: {_fmt_num(ctx.week_stats['taken'])}
- 採用率: {ctx.week_stats['rate']*100:.1f}%

■ Notes
{notes_lines}
"""
        )
        return text.strip()

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

    # ---------- LINE送信 ----------
    def _resolve_line_targets(self, opts) -> List[str]:
        # 1) --line-to 指定があればそれを優先
        ids = [x.strip() for x in (opts.get("line_to") or "").split(",") if x.strip()]
        if ids:
            return ids
        # 2) --line-all なら DB 全員
        if opts.get("line_all") and LineContact is not None:
            return list(LineContact.objects.values_list("user_id", flat=True))
        # 3) それ以外は最新の1件（開発/試験用）
        if LineContact is not None:
            try:
                latest = LineContact.objects.latest("created_at")
                return [latest.user_id]
            except Exception:
                return []
        return []

    def _send_line(self, ctx: BriefContext, md_text: str, opts) -> None:
        user_ids = self._resolve_line_targets(opts)
        if not user_ids:
            self.stdout.write(self.style.WARNING("LINE送信先が見つかりません（--line-to か --line-all を指定するか、LineContactを作成してください）。"))
            return

        title = (opts.get("line_title") or f"AIデイリーブリーフ {ctx.asof}").strip()
        header = f"\n"
        body = md_text

        # 上限対策で分割
        for uid in user_ids:
            chunks = _split_chunks(header + body, limit=4500)
            ok_all = True
            for i, ch in enumerate(chunks, 1):
                try:
                    resp = line_push(uid, ch)
                    code = getattr(resp, "status_code", None)
                    if code != 200:
                        ok_all = False
                        self.stdout.write(self.style.WARNING(f"LINE push failed (uid={uid}, part={i}/{len(chunks)}): {code}"))
                except Exception as e:
                    ok_all = False
                    self.stdout.write(self.style.WARNING(f"LINE push exception (uid={uid}, part={i}/{len(chunks)}): {e}"))
            if ok_all:
                self.stdout.write(self.style.SUCCESS(f"LINE sent to {uid} ({len(chunks)} msg)"))