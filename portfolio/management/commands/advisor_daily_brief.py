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

# LINE
from ...models_line import LineContact
from ...services.line_api import push as line_push, push_flex as line_push_flex


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

def _fmt_num(x, nd=0):
    try:
        v = float(x)
    except Exception:
        return "—"
    if nd == 0:
        return f"{v:,.0f}"
    return f"{v:,.{nd}f}"

def _fmt_pct_from_ratio(x: float, nd: int = 1) -> str:
    """0.51 → 51.0%"""
    try:
        return f"{float(x)*100:.{nd}f}%"
    except Exception:
        return "-"

def _fmt_signed(x: float, nd: int = 2) -> str:
    """+/- 付き小数表記"""
    try:
        return f"{float(x):+.{nd}f}"
    except Exception:
        return "—"

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
        parser.add_argument("--line", action="store_true", help="LINEへも送信する（既定はFlex送信）")
        parser.add_argument("--line-text", action="store_true", help="Flexではなくテキストで送る")
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
            user_ids = self._resolve_line_targets(opts)
            if not user_ids:
                self.stdout.write(self.style.WARNING("LINE送信先が見つかりません（--line-to か --line-all を指定するか、LineContactを作成してください）。"))
                return
            # Flex or Text
            if opts.get("line_text"):
                self._send_line_text(user_ids, ctx, md, opts)
            else:
                # Flex（失敗時はテキストにフォールバック）
                ok = self._send_line_flex(user_ids, ctx, opts)
                if not ok:
                    self._send_line_text(user_ids, ctx, md, opts)

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
        # テキスト（メールtext/LINEフォールバック共用）
        idx_syms = list(ctx.indexes.keys())[:8]
        idx_lines = [
            f"- {sym}: 5日={_fmt_signed(ctx.indexes.get(sym,{}).get('ret_5d',0.0), 2)} / 20日={_fmt_signed(ctx.indexes.get(sym,{}).get('ret_20d',0.0), 2)}"
            for sym in idx_syms
        ]
        top_secs = "\n".join([f"- {r['sector']}: RS {_fmt_signed(r['rs'], 2)}" for r in ctx.sectors[:sector_top]]) or "- なし"
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
- 採用率: {_fmt_pct_from_ratio(ctx.week_stats['rate'], 1)}

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

    # ---------- LINE: 送信対象解決 ----------
    def _resolve_line_targets(self, opts) -> List[str]:
        ids = [x.strip() for x in (opts.get("line_to") or "").split(",") if x.strip()]
        if ids:
            return ids
        if opts.get("line_all"):
            return list(LineContact.objects.values_list("user_id", flat=True))
        try:
            latest = LineContact.objects.latest("created_at")
            return [latest.user_id]
        except Exception:
            return []

    # ---------- LINE: Flex ----------
    def _build_flex(self, ctx: BriefContext) -> dict:
        base_url = getattr(settings, "SITE_BASE_URL", "").rstrip("/")
        public_url = ""
        if base_url:
            public_url = f"{base_url}/media/reports/daily_brief_{ctx.asof}.html"

        def kv(k, v):
            return {
                "type":"box","layout":"baseline","spacing":"sm",
                "contents":[
                    {"type":"text","text":k,"size":"sm","color":"#9aa4b2","flex":4},
                    {"type":"text","text":str(v),"size":"sm","wrap":True,"flex":8}
                ]
            }

        # セクター上位
        sec_lines = []
        for r in ctx.sectors[:10]:
            sec_lines.append(
                {"type":"box","layout":"baseline","spacing":"sm","contents":[
                    {"type":"text","text":r["sector"],"size":"sm","flex":9,"wrap":True},
                    {"type":"text","text":_fmt_signed(r["rs"], 2),"size":"sm","flex":3,"align":"end"}
                ]}
            )

        body = {
          "type": "box",
          "layout": "vertical",
          "spacing": "md",
          "contents": [
            {"type": "text", "text": "AI デイリーブリーフ", "weight":"bold", "size":"lg"},
            {"type": "text", "text": ctx.asof, "size":"xs", "color":"#9aa4b2"},
            {"type": "separator"},
            {"type":"text","text":"地合い（Breadth）","weight":"bold","size":"md","margin":"md"},
            {"type":"box","layout":"vertical","spacing":"sm","contents":[
                kv("Regime", ctx.breadth_view.get("regime","NEUTRAL")),
                kv("Score",  ctx.breadth_view.get("score", 0.0)),
                kv("A/D",    ctx.breadth_view.get("ad_ratio", 1.0)),
                kv("VOL",    ctx.breadth_view.get("vol_ratio", 1.0)),
                kv("H-L",    ctx.breadth_view.get("hl_diff", 0)),
            ]},
            {"type":"separator","margin":"md"},
            {"type":"text","text":"セクターRS（上位10）","weight":"bold","size":"md","margin":"md"},
            {"type":"box","layout":"vertical","spacing":"sm","contents": sec_lines or [{"type":"text","text":"データなし","size":"sm"}]},
            {"type":"separator","margin":"md"},
            {"type":"text","text":"今週の通知サマリ","weight":"bold","size":"md","margin":"md"},
            {"type":"box","layout":"vertical","spacing":"sm","contents":[
                kv("通知", f'{ctx.week_stats["total"]:,}'),
                kv("採用", f'{ctx.week_stats["taken"]:,}'),
                kv("採用率", _fmt_pct_from_ratio(ctx.week_stats["rate"], 1)),
            ]},
          ]
        }

        footer_contents = []
        if public_url:
            footer_contents.append({
                "type":"button","style":"primary","height":"sm",
                "action":{"type":"uri","label":"詳細を開く","uri": public_url }
            })

        bubble = {
          "type": "bubble",
          "size": "mega",
          "body": body,
          "footer": {"type":"box","layout":"vertical","spacing":"sm","contents": footer_contents} if footer_contents else None
        }
        if bubble["footer"] is None:
            del bubble["footer"]
        return bubble

    def _send_line_flex(self, user_ids: List[str], ctx: BriefContext, opts) -> bool:
        """Flexを送信。1件でも成功すれば True"""
        flex = self._build_flex(ctx)
        alt = (opts.get("line_title") or f"AIデイリーブリーフ {ctx.asof}").strip()
        any_ok = False
        for uid in user_ids:
            try:
                r = line_push_flex(uid, alt, flex)
                ok = getattr(r, "status_code", None) == 200
                any_ok = any_ok or ok
                self.stdout.write(self.style.SUCCESS(f"LINE Flex to {uid}: {getattr(r,'status_code',None)}"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"LINE Flex exception (uid={uid}): {e}"))
        return any_ok

    # ---------- LINE: Text ----------
    def _send_line_text(self, user_ids: List[str], ctx: BriefContext, md_text: str, opts) -> None:
        title = (opts.get("line_title") or f"AIデイリーブリーフ {ctx.asof}").strip()
        header = f"{title}\n\n"
        for uid in user_ids:
            chunks = _split_chunks(header + md_text, limit=4500)
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
                self.stdout.write(self.style.SUCCESS(f"LINE text sent to {uid} ({len(chunks)} msg)"))
                
# ===== Flex: デイリーブリーフ（RISK配色 自動切替） =========================
def _flex_theme(regime: str) -> dict:
    r = (regime or "").upper()
    # アクセント色・バッジ色など
    if r == "RISK_ON":
        return dict(
            accent="#16a34a",       # green-600
            accent_soft="#22c55e",  # green-500
            badge_bg="#065f46",     # green-800
            badge_fg="#a7f3d0",     # green-200
        )
    if r == "RISK_OFF":
        return dict(
            accent="#ef4444",       # red-500
            accent_soft="#f87171",  # red-400
            badge_bg="#7f1d1d",     # red-900
            badge_fg="#fecaca",     # red-200
        )
    # NEUTRAL / その他
    return dict(
        accent="#3b82f6",           # blue-500
        accent_soft="#60a5fa",      # blue-400
        badge_bg="#1e3a8a",         # blue-900
        badge_fg="#bfdbfe",         # blue-200
    )


def _build_flex_daily_brief(ctx: "BriefContext") -> dict:
    """RISKレジームに応じて配色が切り替わるFlex Bubbleを返す"""
    regime = (ctx.breadth_view or {}).get("regime", "NEUTRAL")
    th = _flex_theme(regime)

    # 小さめの行ユーティリティ
    def _kv(key, val, bold=False):
        return {
            "type": "box", "layout": "baseline", "spacing": "sm",
            "contents": [
                {"type": "text", "text": key, "size": "xs", "color": "#9aa4b2", "flex": 2},
                {"type": "text", "text": val, "size": "xs", "color": "#e5e7eb", "flex": 5, "wrap": True, **({"weight": "bold"} if bold else {})},
            ],
        }

    # 指数（上位8）
    idx_syms = list(ctx.indexes.keys())[:8]
    idx_items = []
    for sym in idx_syms:
        row = ctx.indexes.get(sym, {})
        v5 = f"{float(row.get('ret_5d', 0.0))*100:+.1f}%"
        v20 = f"{float(row.get('ret_20d', 0.0))*100:+.1f}%"
        idx_items.append(_kv(sym, f"5日 {v5} / 20日 {v20}"))

    # セクター上位10
    sec_items = []
    for r in ctx.sectors[:10]:
        sec_items.append(_kv(r["sector"], f"RS {float(r['rs']):+.2f}"))

    # ヘッダーバー（レジーム色）
    header = {
        "type": "box", "layout": "vertical", "paddingAll": "12px",
        "backgroundColor": th["accent"],
        "contents": [
            {
                "type": "box", "layout": "baseline", "contents": [
                    {"type": "text", "text": f"AI デイリーブリーフ  {ctx.asof}", "color": "#ffffff", "weight": "bold", "wrap": True, "size": "md", "flex": 6},
                    {"type": "box", "layout": "vertical", "cornerRadius": "14px",
                     "backgroundColor": th["badge_bg"], "paddingAll": "6px",
                     "contents": [{"type": "text", "text": regime, "color": th["badge_fg"], "weight": "bold", "size": "xs"}]}
                ]
            },
            {"type": "text", "text": f"Generated at {ctx.generated_at}", "size": "xs", "color": "#e5e7eb", "margin": "sm"},
        ]
    }

    # 地合いカード
    breadth_box = {
        "type": "box", "layout": "vertical", "paddingAll": "12px", "backgroundColor": "#0f172a", "cornerRadius": "12px",
        "contents": [
            {"type": "text", "text": "地合い（Breadth）", "weight": "bold", "color": th["accent_soft"], "size": "sm"},
            {"type": "separator", "margin": "sm", "color": "rgba(148,163,184,0.25)"},
            _kv("Regime", regime, bold=True),
            _kv("Score", f"{ctx.breadth_view.get('score', 0.0):.2f}"),
            _kv("A/D",   f"{ctx.breadth_view.get('ad_ratio', 1.0):.3f}"),
            _kv("VOL",  f"{ctx.breadth_view.get('vol_ratio', 1.0):.3f}"),
            _kv("H-L",  f"{ctx.breadth_view.get('hl_diff', 0)}"),
        ]
    }

    # 指数カード
    indexes_box = {
        "type": "box", "layout": "vertical", "paddingAll": "12px", "backgroundColor": "#0b1220", "cornerRadius": "12px",
        "contents": [{"type": "text", "text": "指数スナップショット（抜粋）", "weight": "bold", "color": th["accent_soft"], "size": "sm"},
                     {"type": "separator", "margin": "sm", "color": "rgba(148,163,184,0.25)"},
                     *idx_items]
    }

    # セクターカード
    sectors_box = {
        "type": "box", "layout": "vertical", "paddingAll": "12px", "backgroundColor": "#0b1220", "cornerRadius": "12px",
        "contents": [{"type": "text", "text": "セクターRS（上位10）", "weight": "bold", "color": th["accent_soft"], "size": "sm"},
                     {"type": "separator", "margin": "sm", "color": "rgba(148,163,184,0.25)"},
                     *sec_items]
    }

    # 週次サマリー
    wk = ctx.week_stats or {}
    weekly_box = {
        "type": "box", "layout": "horizontal", "spacing": "8px",
        "contents": [
            {"type": "box", "layout": "vertical", "paddingAll": "10px", "cornerRadius": "12px",
             "backgroundColor": "#0b1220",
             "contents": [{"type": "text", "text": "通知", "size": "xs", "color": "#9aa4b2"},
                          {"type": "text", "text": f"{int(wk.get('total',0)):,}", "size": "md", "weight": "bold", "color": "#e5e7eb"}]},
            {"type": "box", "layout": "vertical", "paddingAll": "10px", "cornerRadius": "12px",
             "backgroundColor": "#0b1220",
             "contents": [{"type": "text", "text": "採用", "size": "xs", "color": "#9aa4b2"},
                          {"type": "text", "text": f"{int(wk.get('taken',0)):,}", "size": "md", "weight": "bold", "color": "#e5e7eb"}]},
            {"type": "box", "layout": "vertical", "paddingAll": "10px", "cornerRadius": "12px",
             "backgroundColor": "#0b1220",
             "contents": [{"type": "text", "text": "採用率", "size": "xs", "color": "#9aa4b2"},
                          {"type": "text", "text": f"{float(wk.get('rate',0.0))*100:.1f}%", "size": "md", "weight": "bold", "color": "#e5e7eb"}]},
        ]
    }

    # Notes
    notes_list = ctx.notes or []
    notes_box = {
        "type": "box", "layout": "vertical", "paddingAll": "12px", "cornerRadius": "12px",
        "backgroundColor": "#0b1220",
        "contents": [{"type": "text", "text": "Notes", "weight": "bold", "color": th["accent_soft"], "size": "sm"},
                     {"type": "separator", "margin": "sm", "color": "rgba(148,163,184,0.25)"}] +
                    ([{"type":"text","text":"なし","size":"xs","color":"#9aa4b2"}] if not notes_list else
                     [{"type":"text","text":f"・{n}","wrap":True,"size":"xs","color":"#e5e7eb"} for n in notes_list])
    }

    return {
        "type": "flex",
        "altText": f"AI デイリーブリーフ {ctx.asof}",
        "contents": {
            "type": "bubble",
            "size": "mega",
            "styles": {"body": {"backgroundColor": "#0a0f1f"}},
            "header": header,
            "body": {
                "type": "box", "layout": "vertical", "spacing": "12px",
                "contents": [
                    breadth_box,
                    indexes_box,
                    sectors_box,
                    weekly_box,
                    notes_box,
                ],
            },
            "footer": {
                "type": "box", "layout": "baseline", "paddingAll": "10px",
                "contents": [
                    {"type": "text", "text": "AIアドバイザー通知Bot", "size": "xs", "color": "#94a3b8"},
                    {"type": "filler"},
                    {"type": "text", "text": regime, "size": "xs", "color": th["accent_soft"], "weight": "bold"},
                ],
            },
        },
    }
