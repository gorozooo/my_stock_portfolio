# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Any, List, Optional
import json
import os

from django.core.management.base import BaseCommand, CommandParser
from django.conf import settings
from django.utils import timezone

# 既存サービス
from ...services.market import (
    latest_breadth, breadth_regime,
    fetch_indexes_snapshot, latest_sector_strength
)
from ...services.sector_map import normalize_sector
from ...models_advisor import AdviceItem

# コメント生成（新規サービス化）
from ...services.ai_comment import make_ai_comment

# LINE
from ...models_line import LineContact
from ...services.line_api import push as line_push, push_flex as line_push_flex


# ---------- 小ユーティリティ ----------
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
    try:
        return f"{float(x)*100:.{nd}f}%"
    except Exception:
        return "-"

def _fmt_signed(x: float, nd: int = 2) -> str:
    try:
        return f"{float(x):+.{nd}f}"
    except Exception:
        return "—"

def _split_chunks(s: str, limit: int = 4500) -> List[str]:
    if len(s) <= limit:
        return [s]
    out, buf, size = [], [], 0
    for line in s.splitlines(True):
        if size + len(line) > limit and buf:
            out.append("".join(buf).rstrip()); buf, size = [line], len(line)
        else:
            buf.append(line); size += len(line)
    if buf:
        out.append("".join(buf).rstrip())
    return out

def _media_root() -> str:
    return getattr(settings, "MEDIA_ROOT", "") or os.getcwd()

def _load_breadth_for(day: date) -> Optional[Dict[str, Any]]:
    """MEDIA_ROOT/market/breadth_YYYY-MM-DD.json を読む（無ければNone）"""
    mdir = os.path.join(_media_root(), "market")
    path = os.path.join(mdir, f"breadth_{day.strftime('%Y-%m-%d')}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


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
    ai_comment: str = ""


# =========================
# コマンド本体（LINE専用）
# =========================
class Command(BaseCommand):
    help = "AIデイリーブリーフを生成し、LINEに配信（メールは廃止）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--date", type=str, default="", help="対象日(YYYY-MM-DD)。未指定は今日")
        parser.add_argument("--days", type=int, default=90, help="週次サマリのlookback（日数）")

        # コメント生成（GPT切替）
        parser.add_argument("--ai-model", type=str, default="", help="コメント生成モデル（gpt-4-turbo / gpt-5）")

        # LINE 送信
        parser.add_argument("--line", action="store_true", help="LINEへ送信する")
        parser.add_argument("--line-text", action="store_true", help="テキストで送る（既定はFlex）")
        parser.add_argument("--line-to", type=str, default="", help="送信先user_id（カンマ区切り）")
        parser.add_argument("--line-all", action="store_true", help="登録済み全員に送る")
        parser.add_argument("--line-title", type=str, default="", help="タイトル（未指定は自動）")
        parser.add_argument("--line-max-sectors", type=int, default=10, help="セクター上位表示件数")
        parser.add_argument("--line-max-indexes", type=int, default=6, help="指数の表示件数（テキスト）")

    def handle(self, *args, **opts):
        asof_str = opts["date"] or _today_str()
        try:
            the_day = datetime.fromisoformat(asof_str).date()
        except Exception:
            return self.stdout.write(self.style.ERROR(f"invalid --date: {asof_str}"))

        # ---- 市況（当日）
        b = latest_breadth() or {}
        regime = breadth_regime(b)  # dict（regime/score等）

        # ---- 前日スコア（任意）
        prev_score = None
        yday = the_day - timedelta(days=1)
        prev_b = _load_breadth_for(yday)
        if prev_b:
            try:
                prev_score = float(breadth_regime(prev_b).get("score", 0.0))
            except Exception:
                prev_score = None

        # ---- 指数（スナップショット）
        idx = fetch_indexes_snapshot() or {}

        # ---- セクターRS
        rs_tbl = latest_sector_strength() or {}
        sectors_view: List[Dict[str, Any]] = []
        for raw_sec, row in rs_tbl.items():
            sectors_view.append({
                "sector": normalize_sector(raw_sec),
                "rs": _safe_float(row.get("rs_score")),
                "date": row.get("date") or "",
            })
        sectors_view.sort(key=lambda r: r["rs"], reverse=True)

        # ---- 週次サマリ
        now = timezone.localtime()
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        since = timezone.now() - timedelta(days=int(opts["days"] or 90))
        qs_all = AdviceItem.objects.filter(created_at__gte=since)
        week_qs = qs_all.filter(created_at__gte=monday)
        week_stats = dict(
            total=week_qs.count(),
            taken=week_qs.filter(taken=True).count(),
            rate=0.0,
        )
        week_stats["rate"] = round(week_stats["taken"] / week_stats["total"], 4) if week_stats["total"] else 0.0

        # ---- 注意書き
        notes: List[str] = []
        if not b: notes.append("breadth.json が見つからないため既定ハンドリング。")
        if not rs_tbl: notes.append("セクターRSが見つからない。")
        if not idx: notes.append("indexes snapshot が空。")

        # ---- 今日のひとこと（GPT / ローカル）
        ai_model = (opts.get("ai_model") or "").strip() or None  # Noneなら既定(gpt-4-turbo)
        ai_comment = make_ai_comment(
            regime=regime.get("regime", "NEUTRAL"),
            score=float(regime.get("score", 0.0)),
            sectors=sectors_view,
            adopt_rate=float(week_stats.get("rate", 0.0)),
            prev_score=prev_score,
            seed=asof_str,
            engine=ai_model,
        )

        ctx = BriefContext(
            asof=asof_str,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            breadth=b,
            breadth_view=regime,
            indexes=idx,
            sectors=sectors_view,
            week_stats=week_stats,
            notes=notes,
            ai_comment=ai_comment,
        )

        # ---- LINE 送信
        if not opts["line"]:
            self.stdout.write(self.style.SUCCESS("generated (no LINE send)."))
            return

        targets = self._resolve_line_targets(opts)
        if not targets:
            self.stdout.write(self.style.WARNING("LINE送信先が見つかりません。"))
            return

        if opts.get("line_text"):
            text = self._render_text(
                ctx,
                sector_top=int(opts["line_max_sectors"] or 10),
                idx_top=int(opts["line_max_indexes"] or 6),
            )
            self._send_line_text(targets, ctx, text, opts)
        else:
            self._send_line_flex(targets, ctx, opts)

    # ---------- 送信先解決 ----------
    def _resolve_line_targets(self, opts) -> List[str]:
        ids = [x.strip() for x in (opts.get("line_to") or "").split(",") if x.strip()]
        if ids: return ids
        if opts.get("line_all"):
            return list(LineContact.objects.values_list("user_id", flat=True))
        try:
            return [LineContact.objects.latest("created_at").user_id]
        except Exception:
            return []

    # ---------- テキスト描画（フォールバック/任意送信） ----------
    def _render_text(self, ctx: BriefContext, sector_top: int = 10, idx_top: int = 6) -> str:
        idx_syms = list(ctx.indexes.keys())[:idx_top]
        idx_lines = [
            f"- {sym}: 5日={_fmt_signed(ctx.indexes.get(sym,{}).get('ret_5d',0.0),2)} / 20日={_fmt_signed(ctx.indexes.get(sym,{}).get('ret_20d',0.0),2)}"
            for sym in idx_syms
        ]
        top_secs = "\n".join([f"- {r['sector']}: RS {_fmt_signed(r['rs'],2)}" for r in ctx.sectors[:sector_top]]) or "- なし"
        notes_lines = "\n".join([f"- {n}" for n in (ctx.notes or ["なし"])])

        text = (
f"""# AI デイリーブリーフ {ctx.asof}

💡 {ctx.ai_comment or '—'}

生成: {ctx.generated_at}

■ 地合い（Breadth）
- Regime: **{ctx.breadth_view.get('regime','NEUTRAL')}**
- Score: {ctx.breadth_view.get('score',0.0)}
- A/D: {ctx.breadth_view.get('ad_ratio',1.0)} / VOL: {ctx.breadth_view.get('vol_ratio',1.0)} / H-L: {ctx.breadth_view.get('hl_diff',0)}

■ 指数スナップショット（抜粋）
"""
        + "\n".join(idx_lines)
        + f"""

■ セクターRS（上位{sector_top}）
{top_secs}

■ 今週の通知サマリ
- 通知: {_fmt_num(ctx.week_stats['total'])}
- 採用: {_fmt_num(ctx.week_stats['taken'])}
- 採用率: {_fmt_pct_from_ratio(ctx.week_stats['rate'],1)}

■ Notes
{notes_lines}"""
        )
        return text.strip()

    # ---------- Flex 本体 ----------
    def _build_flex(self, ctx: BriefContext) -> dict:
        base_url = getattr(settings, "SITE_BASE_URL", "").rstrip("/")
        public_url = f"{base_url}/media/reports/daily_brief_{ctx.asof}.html" if base_url else ""

        # Theme by regime
        regime = str(ctx.breadth_view.get("regime", "NEUTRAL")).upper()
        def theme_for_regime(rg: str):
            if "OFF" in rg:
                return dict(primary="#dc2626", accent="#ef4444", pos="#16a34a", neg="#ef4444",
                            heading="#111827", muted="#9ca3af", card="#f9fafb", icon="📉")
            if "ON" in rg:
                return dict(primary="#16a34a", accent="#22c55e", pos="#16a34a", neg="#ef4444",
                            heading="#111827", muted="#9ca3af", card="#f9fafb", icon="📈")
            return dict(primary="#2563eb", accent="#3b82f6", pos="#16a34a", neg="#ef4444",
                        heading="#111827", muted="#9ca3af", card="#f9fafb", icon="⚖️")
        T = theme_for_regime(regime)

        def row(label, value, color=None):
            return {
                "type": "box",
                "layout": "horizontal",
                "contents": [
                    {"type": "text", "text": label, "size": "sm", "color": T["muted"], "flex": 6},
                    {"type": "text", "text": str(value), "size": "sm", "align": "end",
                     "color": color or T["heading"], "flex": 6, "wrap": False}
                ]
            }

        def signed_color(v: float):
            return T["pos"] if float(v) > 0 else T["neg"] if float(v) < 0 else T["muted"]

        # sectors top
        sector_lines = []
        for s in ctx.sectors[:8]:
            val = float(s.get("rs", 0.0))
            sector_lines.append({
                "type": "box", "layout": "horizontal", "contents": [
                    {"type": "text", "text": str(s.get("sector","—")), "size": "sm", "flex": 8, "wrap": True},
                    {"type": "text", "text": f"{val:+.2f}", "size": "sm", "flex": 4, "align": "end",
                     "color": signed_color(val)}
                ]
            })
        if not sector_lines:
            sector_lines = [{"type": "text", "text": "データなし", "size": "sm", "color": T["muted"]}]

        b = ctx.breadth_view
        score = float(b.get("score", 0.0))
        ad    = float(b.get("ad_ratio", 1.0))
        vol   = float(b.get("vol_ratio", 1.0))
        hl    = float(b.get("hl_diff", 0.0))

        body = {
          "type": "box",
          "layout": "vertical",
          "spacing": "lg",
          "backgroundColor": T["card"],
          "paddingAll": "16px",
          "contents": [
            {"type": "box", "layout": "horizontal", "contents": [
                {"type": "text", "text": f"{T['icon']}  AI デイリーブリーフ",
                 "weight": "bold", "size": "lg", "color": T["primary"], "flex": 9},
                {"type": "text", "text": ctx.asof, "size": "xs", "color": T["muted"], "align": "end", "flex": 3}
            ]},

            # 今日のひとこと（淡色帯）
            {
              "type": "box",
              "layout": "vertical",
              "backgroundColor": (T["accent"] + "22"),
              "cornerRadius": "8px",
              "paddingAll": "10px",
              "contents": [
                {"type": "text", "text": "今日のひとこと", "size": "xs", "color": T["primary"]},
                {"type": "text", "text": ctx.ai_comment or "—", "size": "sm", "wrap": True, "color": T["heading"]}
              ]
            },

            {"type": "separator", "margin": "md"},

            {"type": "text", "text": "地合い（Breadth）", "weight": "bold", "size": "md", "color": T["heading"]},
            row("Regime", b.get("regime","NEUTRAL"), color=T["primary"]),
            row("Score", f"{score:.2f}", signed_color(score)),
            row("A/D", f"{ad:.3f}", signed_color(ad-1.0)),
            row("VOL", f"{vol:.2f}", signed_color(vol-1.0)),
            row("H-L", f"{hl:.1f}", signed_color(hl)),

            {"type": "separator", "margin": "md"},

            {"type": "text", "text": "セクターRS（上位8）", "weight": "bold", "size": "md", "color": T["heading"]},
            {"type": "box", "layout": "vertical", "spacing": "sm", "contents": sector_lines},

            {"type": "separator", "margin": "md"},

            {"type": "text", "text": "今週の通知サマリ", "weight": "bold", "size": "md", "color": T["heading"]},
            row("通知", f"{ctx.week_stats.get('total',0):,}"),
            row("採用", f"{ctx.week_stats.get('taken',0):,}"),
            row("採用率", f"{float(ctx.week_stats.get('rate',0.0))*100:.1f}%"),
          ]
        }

        footer = None
        if public_url:
            footer = {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [{
                    "type": "button", "style": "primary", "height": "sm",
                    "action": {"type": "uri", "label": "詳細を開く", "uri": public_url}
                }]
            }

        bubble = {"type": "bubble", "size": "mega", "body": body}
        if footer: bubble["footer"] = footer
        return bubble

    # ---------- LINE: Flex 送信 ----------
    def _send_line_flex(self, user_ids: List[str], ctx: BriefContext, opts) -> bool:
        flex = self._build_flex(ctx)
        alt  = (opts.get("line_title") or f"AIデイリーブリーフ {ctx.asof}").strip()
        any_ok = False

        smoke = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "Flex smoke test", "weight": "bold", "size": "lg"},
                    {"type": "text", "text": "このカードが届けば Flex 自体はOK", "size": "sm", "wrap": True}
                ]
            }
        }

        for uid in user_ids:
            try:
                r = line_push_flex(uid, alt, flex)
                code = getattr(r, "status_code", None)
                any_ok = any_ok or (code == 200)
                if code != 200:
                    self.stdout.write(self.style.WARNING(
                        f"LINE Flex to {uid}: {code} {getattr(r,'text','')}"
                    ))
                    rs = line_push_flex(uid, "Flex smoke test", smoke)
                    self.stdout.write(self.styleWARNING(
                        f"  smoke test status={getattr(rs,'status_code',None)} body={getattr(rs,'text','')}"
                    ))
                else:
                    self.stdout.write(self.style.SUCCESS(f"LINE Flex to {uid}: {code}"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"LINE Flex exception (uid={uid}): {e}"))
        return any_ok

    # ---------- LINE送信（テキスト） ----------
    def _send_line_text(self, user_ids: List[str], ctx: BriefContext, md_text: str, opts) -> None:
        title = (opts.get("line_title") or f"AIデイリーブリーフ {ctx.asof}").strip()
        header = f"{title}\n\n"
        for uid in user_ids:
            for i, ch in enumerate(_split_chunks(header + md_text, limit=4500), 1):
                try:
                    r = line_push(uid, ch)
                    self.stdout.write(self.style.SUCCESS(f"LINE text to {uid} part {i}: {getattr(r,'status_code',None)}"))
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"LINE text exception (uid={uid}, part={i}): {e}"))