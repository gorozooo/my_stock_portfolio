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

# コメント生成（新規サービス）
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
    help = "AIデイリーブリーフを生成し、LINEに配信（コメントのみモード対応／メール廃止）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--date", type=str, default="", help="対象日(YYYY-MM-DD)。未指定は今日")
        parser.add_argument("--days", type=int, default=90, help="週次サマリのlookback（日数）")

        # コメント生成（GPT切替）
        parser.add_argument("--ai-model", type=str, default="", help="コメント生成モデル（gpt-4-turbo / gpt-5 など）")

        # 時間帯（温度感の目的を明確化）
        parser.add_argument("--phase", type=str, default="",
                            choices=["preopen", "postopen", "noon", "afternoon", "outlook"],
                            help="コメントの時間帯スタイル")

        # LINE 送信
        parser.add_argument("--line", action="store_true", help="LINEへ送信する")
        parser.add_argument("--comment-only", action="store_true", help="コメントだけ送る（カード1枚）")
        parser.add_argument("--line-text", action="store_true", help="テキストで送る（デバッグ用）")
        parser.add_argument("--line-to", type=str, default="", help="送信先user_id（カンマ区切り）")
        parser.add_argument("--line-all", action="store_true", help="登録済み全員に送る")
        parser.add_argument("--line-title", type=str, default="", help="タイトル（未指定は自動）")

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

        # ---- 指数（併用データ／コメント-onlyでも背後で使うことあり）
        idx = fetch_indexes_snapshot() or {}

        # ---- セクターRS（上位抽出）
        rs_tbl = latest_sector_strength() or {}
        sectors_view: List[Dict[str, Any]] = []
        for raw_sec, row in rs_tbl.items():
            sectors_view.append({
                "sector": normalize_sector(raw_sec),
                "rs": _safe_float(row.get("rs_score")),
                "date": row.get("date") or "",
            })
        sectors_view.sort(key=lambda r: r["rs"], reverse=True)

        # ---- 週次サマリ（シグナル採用率用）
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
        ai_model = (opts.get("ai_model") or "").strip() or None  # Noneなら既定
        ai_comment = make_ai_comment(
            regime=regime.get("regime", "NEUTRAL"),
            score=float(regime.get("score", 0.0)),
            sectors=sectors_view,
            adopt_rate=float(week_stats.get("rate", 0.0)),
            prev_score=prev_score,
            seed=asof_str,
            engine=ai_model,
            phase=(opts.get("phase") or None),
            snapshot=None,  # 将来: 先物/VIX/為替等を渡す
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
            self.stdout.write(self.style.SUCCESS(f"AI COMMENT: {ctx.ai_comment}"))
            return

        targets = self._resolve_line_targets(opts)
        if not targets:
            self.stdout.write(self.style.WARNING("LINE送信先が見つかりません。"))
            return

        if opts.get("line_text"):
            self._send_line_text(targets, ctx, ctx.ai_comment, opts)
        elif opts.get("comment_only"):
            self._send_line_comment_only(targets, ctx, opts)
        else:
            # 互換：従来の詳細カード（必要なら残す）。今回はコメントOnly推し。
            self._send_line_comment_only(targets, ctx, opts)

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

    # ---------- コメントだけ（Flex） ----------
    def _phase_label(self, phase: Optional[str], fallback: str) -> str:
        mp = {
            "preopen":   "寄付き前の温度感（7:20現在）",
            "postopen":  "寄付き直後の温度感（9:50現在）",
            "noon":      "前場の総括と後場の温度感（12:00現在）",
            "afternoon": "引け前の温度感（14:55現在）",
            "outlook":   "明日への展望（17:00現在）",
        }
        return mp.get((phase or "").lower(), fallback)

    def _build_comment_only(self, ctx: BriefContext, phase: Optional[str]) -> dict:
        regime = str(ctx.breadth_view.get("regime","NEUTRAL")).upper()
        # 自動配色（ON/NEUTRAL/OFF）
        primary = "#16a34a" if "ON" in regime else "#dc2626" if "OFF" in regime else "#2563eb"

        label = self._phase_label(phase, ctx.asof)

        return {
          "type":"bubble","size":"mega",
          "body":{"type":"box","layout":"vertical","spacing":"md","paddingAll":"16px",
            "contents":[
              {"type":"text","text":"AI デイリーブリーフ","weight":"bold","size":"lg","color":primary},
              {"type":"text","text":label,"size":"xs","color":"#9ca3af"},
              {"type":"box","layout":"vertical","backgroundColor":primary+"22","cornerRadius":"10px","paddingAll":"12px",
                "contents":[
                  {"type":"text","text":"今日のひとこと","size":"xs","color":primary},
                  {"type":"text","text":ctx.ai_comment or "—","size":"md","wrap":True,"color":"#111827"}
                ]
              }
            ]
          }
        }

    def _send_line_comment_only(self, user_ids: List[str], ctx: BriefContext, opts) -> bool:
        bubble = self._build_comment_only(ctx, opts.get("phase"))
        alt = (opts.get("line_title") or "AIコメント").strip()
        ok = False
        for uid in user_ids:
            try:
                r = line_push_flex(uid, alt, bubble)
                code = getattr(r, "status_code", None)
                ok = ok or (code == 200)
                self.stdout.write(self.style.SUCCESS(f"LINE comment-only to {uid}: {code}"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"LINE comment-only exception (uid={uid}): {e}"))
        return ok

    # ---------- LINE送信（テキスト：デバッグ用） ----------
    def _send_line_text(self, user_ids: List[str], ctx: BriefContext, text: str, opts) -> None:
        title = (opts.get("line_title") or self._phase_label(opts.get("phase"), ctx.asof)).strip()
        header = f"{title}\n\n"
        for uid in user_ids:
            for i, ch in enumerate(_split_chunks(header + (text or "—"), limit=4500), 1):
                try:
                    r = line_push(uid, ch)
                    self.stdout.write(self.style.SUCCESS(f"LINE text to {uid} part {i}: {getattr(r,'status_code',None)}"))
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"LINE text exception (uid={uid}, part={i}): {e}"))