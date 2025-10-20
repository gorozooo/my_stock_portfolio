# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Any, List, Optional
import json, os

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

# コメント生成（新：ディーラー視点＋モード＋スナップショット）
from ...services.ai_comment import make_ai_comment

# LINE
from ...models_line import LineContact
from ...services.line_api import push as line_push, push_flex as line_push_flex


# ---------- utils ----------
def _today_str(d: Optional[date] = None) -> str:
    return (d or date.today()).strftime("%Y-%m-%d")

def _safe_float(x, d=0.0) -> float:
    try: return float(x)
    except Exception: return d

def _fmt_num(x, nd=0):
    try: v = float(x)
    except Exception: return "—"
    return f"{v:,.0f}" if nd == 0 else f"{v:,.{nd}f}"

def _fmt_pct_from_ratio(x: float, nd: int = 1) -> str:
    try: return f"{float(x)*100:.{nd}f}%"
    except Exception: return "-"

def _fmt_signed(x: float, nd: int = 2) -> str:
    try: return f"{float(x):+.{nd}f}"
    except Exception: return "—"

def _split_chunks(s: str, limit: int = 4500) -> List[str]:
    if len(s) <= limit: return [s]
    out, buf, size = [], [], 0
    for line in s.splitlines(True):
        if size + len(line) > limit and buf:
            out.append("".join(buf).rstrip()); buf, size = [line], len(line)
        else:
            buf.append(line); size += len(line)
    if buf: out.append("".join(buf).rstrip())
    return out

def _media_root() -> str:
    return getattr(settings, "MEDIA_ROOT", "") or os.getcwd()

def _load_latest_snapshot() -> Optional[Dict[str, Any]]:
    path = os.path.join(_media_root(), "market", "snapshots", "latest.json")
    if not os.path.exists(path): return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _load_breadth_for(day: date) -> Optional[Dict[str, Any]]:
    mdir = os.path.join(_media_root(), "market")
    path = os.path.join(mdir, f"breadth_{day.strftime('%Y-%m-%d')}.json")
    if not os.path.exists(path): return None
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
# コマンド本体
# =========================
class Command(BaseCommand):
    help = "AIデイリーブリーフをLINEに配信。--comment-only + --mode で“温度感コメントだけ”運用が可能。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--date", type=str, default="", help="対象日(YYYY-MM-DD)。未指定は今日")
        parser.add_argument("--days", type=int, default=90, help="週次サマリのlookback（日数）")

        # コメント生成（GPT切替/モード）
        parser.add_argument("--ai-model", type=str, default="", help="コメント生成モデル（gpt-4-turbo / gpt-5 / gpt-4o-mini など）")
        parser.add_argument("--mode", type=str, default="preopen", help="preopen/postopen/noon/afternoon/outlook")
        parser.add_argument("--comment-only", action="store_true", help="コメントだけを送信（カードも簡素化）")
        parser.add_argument("--persona", type=str, default="現場感のある日本株ディーラー視点で。", help="口調・視点の上書き")

        # LINE
        parser.add_argument("--line", action="store_true", help="LINEへ送信する")
        parser.add_argument("--line-text", action="store_true", help="Flexではなくテキストで送る")
        parser.add_argument("--line-to", type=str, default="", help="送信先user_id（カンマ区切り）")
        parser.add_argument("--line-all", action="store_true", help="登録済み全員に送る")
        parser.add_argument("--line-title", type=str, default="", help="タイトル（未指定は自動）")

    def handle(self, *args, **opts):
        asof_str = opts["date"] or _today_str()
        try:
            the_day = datetime.fromisoformat(asof_str).date()
        except Exception:
            return self.stdout.write(self.style.ERROR(f"invalid --date: {asof_str}"))

        mode = (opts.get("mode") or "preopen").strip().lower()
        persona = (opts.get("persona") or "現場感のある日本株ディーラー視点で。").strip()

        # ---- 市況（当日 breadth） ※comment-onlyでも基礎指標として利用
        b = latest_breadth() or {}
        regime = breadth_regime(b)

        # 前日スコア
        prev_score = None
        yday = the_day - timedelta(days=1)
        prev_b = _load_breadth_for(yday)
        if prev_b:
            try: prev_score = float(breadth_regime(prev_b).get("score", 0.0))
            except Exception: prev_score = None

        # 指数 / セクター（comment-only でもバックグラウンドとして評価に使える）
        idx = fetch_indexes_snapshot() or {}
        rs_tbl = latest_sector_strength() or {}
        sectors_view: List[Dict[str, Any]] = []
        for raw_sec, row in rs_tbl.items():
            sectors_view.append({
                "sector": normalize_sector(raw_sec),
                "rs": _safe_float(row.get("rs_score")),
                "date": row.get("date") or "",
            })
        sectors_view.sort(key=lambda r: r["rs"], reverse=True)

        # 週次サマリ（採用率は精度コメントに使う）
        now = timezone.localtime()
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        since = timezone.now() - timedelta(days=int(opts["days"] or 90))
        qs_all = AdviceItem.objects.filter(created_at__gte=since)
        week_qs = qs_all.filter(created_at__gte=monday)
        week_stats = dict(total=week_qs.count(), taken=week_qs.filter(taken=True).count(), rate=0.0)
        week_stats["rate"] = round(week_stats["taken"]/week_stats["total"], 4) if week_stats["total"] else 0.0

        # スナップショット（先物/VIX/為替など）
        snapshot = _load_latest_snapshot()

        # ---- 今日のひとこと
        ai_comment = make_ai_comment(
            mode=mode,
            persona=persona,
            regime=regime.get("regime", "NEUTRAL"),
            score=float(regime.get("score", 0.0)),
            sectors=sectors_view,
            adopt_rate=float(week_stats.get("rate", 0.0)),
            prev_score=prev_score,
            snapshot=snapshot,
            seed=asof_str + "|" + mode,
            engine=(opts.get("ai_model") or "").strip() or None,
        )

        ctx = BriefContext(
            asof=asof_str,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            breadth=b, breadth_view=regime, indexes=idx,
            sectors=sectors_view, week_stats=week_stats,
            notes=[], ai_comment=ai_comment,
        )

        if not opts["line"]:
            self.stdout.write(self.style.SUCCESS(f"generated (no LINE send). comment={ai_comment}"))
            return

        targets = self._resolve_line_targets(opts)
        if not targets:
            self.stdout.write(self.style.WARNING("LINE送信先が見つかりません。"))
            return

        if opts.get("line_text"):
            self._send_line_text(targets, ctx, ai_only=bool(opts.get("comment_only")))
        else:
            self._send_line_flex(targets, ctx, ai_only=bool(opts.get("comment_only")), title=opts.get("line_title"))

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

    # ---------- Flex ----------
    def _build_flex_comment_only(self, ctx: BriefContext, title: Optional[str]) -> dict:
        # simple card with comment only
        T = dict(primary="#2563eb", heading="#111827", muted="#9aa4b2", accent="#3b82f6")
        head = (title or f"AI デイリーブリーフ {ctx.asof}").strip()

        body = {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": head, "weight": "bold", "size": "md", "color": T["primary"]},
                {"type": "box", "layout": "vertical",
                 "backgroundColor": T["accent"] + "22", "cornerRadius": "8px", "paddingAll": "10px",
                 "contents": [
                    {"type":"text","text":"今日のひとこと","size":"xs","color":T["primary"]},
                    {"type":"text","text":ctx.ai_comment or "—","size":"sm","wrap":True,"color":T["heading"]},
                 ]}
            ]
        }
        return {"type": "bubble", "size": "mega", "body": body}

    def _send_line_flex(self, user_ids: List[str], ctx: BriefContext, ai_only: bool, title: Optional[str]) -> bool:
        bubble = self._build_flex_comment_only(ctx, title) if ai_only else self._build_flex_full(ctx, title)
        alt = (title or f"AIデイリーブリーフ {ctx.asof}").strip()
        any_ok = False
        for uid in user_ids:
            try:
                r = line_push_flex(uid, alt, bubble)
                any_ok = any_ok or (getattr(r, "status_code", None) == 200)
                self.stdout.write(self.style.SUCCESS(f"LINE Flex to {uid}: {getattr(r,'status_code',None)}"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"LINE Flex exception (uid={uid}): {e}"))
        return any_ok

    # 既存フル版（必要なら従来カードで送る）
    def _build_flex_full(self, ctx: BriefContext, title: Optional[str]) -> dict:
        # 簡略（コメント＋地合い/セクター/週次）— 以前の実装を薄く再現
        T = dict(primary="#2563eb", heading="#111827", muted="#9aa4b2", accent="#3b82f6")
        def row(k, v): return {"type":"box","layout":"horizontal","contents":[
            {"type":"text","text":k,"size":"sm","color":T["muted"],"flex":6},
            {"type":"text","text":str(v),"size":"sm","align":"end","flex":6}
        ]}
        sec_lines=[]
        for s in ctx.sectors[:8]:
            sec_lines.append(row(str(s.get("sector","—")), f"{float(s.get('rs',0.0)):+.2f}"))
        if not sec_lines: sec_lines=[{"type":"text","text":"データなし","size":"sm","color":T["muted"]}]
        b = ctx.breadth_view
        body = {
          "type":"box","layout":"vertical","spacing":"md","contents":[
            {"type":"text","text":(title or f"AI デイリーブリーフ {ctx.asof}"),"weight":"bold","size":"md","color":T["primary"]},
            {"type":"box","layout":"vertical","backgroundColor":T["accent"]+"22","cornerRadius":"8px","paddingAll":"10px",
             "contents":[
                {"type":"text","text":"今日のひとこと","size":"xs","color":T["primary"]},
                {"type":"text","text":ctx.ai_comment or "—","size":"sm","wrap":True,"color":T["heading"]},
            ]},
            {"type":"separator"},
            {"type":"text","text":"地合い","weight":"bold","size":"md","color":T["heading"]},
            row("Regime", b.get("regime","NEUTRAL")),
            row("Score", f"{float(b.get('score',0.0)):.2f}"),
            {"type":"separator"},
            {"type":"text","text":"セクターRS（上位8）","weight":"bold","size":"md"},
            {"type":"box","layout":"vertical","contents":sec_lines},
            {"type":"separator"},
            {"type":"text","text":"今週の通知","weight":"bold","size":"md"},
            row("通知", f"{ctx.week_stats.get('total',0):,}"),
            row("採用", f"{ctx.week_stats.get('taken',0):,}"),
            row("採用率", f"{float(ctx.week_stats.get('rate',0.0))*100:.1f}%"),
          ]
        }
        return {"type":"bubble","size":"mega","body":body}

    # ---------- Text fallback ----------
    def _send_line_text(self, user_ids: List[str], ctx: BriefContext, ai_only: bool) -> None:
        if ai_only:
            text = f"AI デイリーブリーフ {ctx.asof}\n\n{ctx.ai_comment or '—'}"
        else:
            text = (
f"""AI デイリーブリーフ {ctx.asof}

{ctx.ai_comment or '—'}

地合い: Regime {ctx.breadth_view.get('regime','NEUTRAL')} / Score {ctx.breadth_view.get('score',0.0)}
セクター上位: {", ".join([s['sector'] for s in ctx.sectors[:5]]) or '—'}
採用率: {float(ctx.week_stats.get('rate',0.0))*100:.1f}%
""").strip()
        for uid in user_ids:
            for i, ch in enumerate(_split_chunks(text, 4500), 1):
                try:
                    r = line_push(uid, ch)
                    self.stdout.write(self.style.SUCCESS(f"LINE text to {uid} part {i}: {getattr(r,'status_code',None)}"))
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"LINE text exception (uid={uid}, part={i}): {e}"))