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

# 既存サービス（コメント生成は ai_comment を使用）
from ...services.market import (
    latest_breadth, breadth_regime,
    fetch_indexes_snapshot, latest_sector_strength
)
from ...services.sector_map import normalize_sector
from ...models_advisor import AdviceItem

from ...services.ai_comment import make_ai_comment  # ← GPT/ローカル両対応の“今日のひとこと”

# LINE
from ...models_line import LineContact
from ...services.line_api import push_flex as line_push_flex


# ---------- 小ユーティリティ ----------
def _today_str(d: Optional[date] = None) -> str:
    d = d or date.today()
    return d.strftime("%Y-%m-%d")

def _safe_float(x, d=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return d

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

def _mode_label(mode: str) -> str:
    """表示用ラベル"""
    m = (mode or "").lower()
    return {
        "preopen":   "寄り付き前",
        "postopen":  "寄り直後",
        "noon":      "前場まとめ → 後場へ",
        "afternoon": "後場の温度感",
        "outlook":   "明日への展望",
    }.get(m, "マーケットコメント")


@dataclass
class BriefContext:
    asof: str                # 日付（YYYY-MM-DD）
    generated_at: str        # 生成時刻（ローカル）
    breadth_view: Dict[str, Any]
    sectors: List[Dict[str, Any]]
    week_rate: float         # 今週の採用率（0-1）
    ai_comment: str          # 今日のひとこと（本文）
    mode: str                # preopen/postopen/noon/afternoon/outlook


# =========================
# コマンド本体（LINE “コメント専用”）
# =========================
class Command(BaseCommand):
    help = "AIデイリー“コメント専用”を生成し、LINEに配信（地合い/セクター/サマリは送らない）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--date", type=str, default="", help="対象日(YYYY-MM-DD)。未指定は今日")
        parser.add_argument("--days", type=int, default=90, help="週次サマリのlookback（日数）")

        # コメント生成（GPT切替）
        parser.add_argument("--ai-model", type=str, default="", help="コメント生成モデル（例: gpt-4-turbo / gpt-5 / gpt-4o-miniなど）")

        # コメントの時間帯モード（表示ラベル用）
        parser.add_argument(
            "--mode", type=str, default="",
            help="コメントモード：preopen / postopen / noon / afternoon / outlook"
        )

        # LINE送信先
        parser.add_argument("--line", action="store_true", help="LINEへ送信する")
        parser.add_argument("--line-to", type=str, default="", help="送信先user_id（カンマ区切り）")
        parser.add_argument("--line-all", action="store_true", help="登録済み全員に送る")
        parser.add_argument("--line-title", type=str, default="", help="通知の代替テキスト（未指定は自動）")

    def handle(self, *args, **opts):
        # ====== 入力日付 ======
        asof_str = opts["date"] or _today_str()
        try:
            the_day = datetime.fromisoformat(asof_str).date()
        except Exception:
            return self.stdout.write(self.style.ERROR(f"invalid --date: {asof_str}"))

        # ====== 市況（当日 breadth -> regime/score だけ使う） ======
        b = latest_breadth() or {}
        regime = breadth_regime(b)  # dict（regime/score等）

        # ====== 前日スコア（差分コメント用・任意） ======
        prev_score = None
        yday = the_day - timedelta(days=1)
        prev_b = _load_breadth_for(yday)
        if prev_b:
            try:
                prev_score = float(breadth_regime(prev_b).get("score", 0.0))
            except Exception:
                prev_score = None

        # ====== セクターRS（上位だけコメント要素に） ======
        rs_tbl = latest_sector_strength() or {}
        sectors_view: List[Dict[str, Any]] = []
        for raw_sec, row in rs_tbl.items():
            sectors_view.append({
                "sector": normalize_sector(raw_sec),
                "rs": _safe_float(row.get("rs_score")),
                "date": row.get("date") or "",
            })
        sectors_view.sort(key=lambda r: r["rs"], reverse=True)

        # ====== 今週の採用率（シグナル精度の目安） ======
        now = timezone.localtime()
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        since = timezone.now() - timedelta(days=int(opts["days"] or 90))
        qs_all = AdviceItem.objects.filter(created_at__gte=since)
        week_qs = qs_all.filter(created_at__gte=monday)
        week_rate = 0.0
        try:
            total = week_qs.count()
            taken = week_qs.filter(taken=True).count()
            week_rate = round((taken / total), 4) if total else 0.0
        except Exception:
            week_rate = 0.0

        # ====== 今日のひとこと（GPT / ローカル） ======
        ai_model = (opts.get("ai_model") or "").strip() or None  # None→既定（ai_comment側）
        mode_str = (opts.get("mode") or "preopen").lower()
        ai_comment = make_ai_comment(
            regime=regime.get("regime", "NEUTRAL"),
            score=float(regime.get("score", 0.0)),
            sectors=sectors_view,
            adopt_rate=float(week_rate),
            prev_score=prev_score,
            seed=asof_str + mode_str,
            engine=ai_model,
            mode=mode_str,
            persona="gorozooo",
        )

        ctx = BriefContext(
            asof=asof_str,
            generated_at=timezone.localtime().strftime("%Y-%m-%d %H:%M"),
            breadth_view=regime,
            sectors=sectors_view,
            week_rate=week_rate,
            ai_comment=ai_comment,
            mode=mode_str,
        )

        # ====== LINE送信 ======
        if not opts["line"]:
            self.stdout.write(self.style.SUCCESS("generated (no LINE send)."))
            self.stdout.write(self.style.SUCCESS(f"[{_mode_label(ctx.mode)} @ {ctx.generated_at}] {ctx.ai_comment}"))
            return

        targets = self._resolve_line_targets(opts)
        if not targets:
            self.stdout.write(self.style.WARNING("LINE送信先が見つかりません。"))
            return

        self._send_line_flex(targets, ctx, opts)

    # ---------- 送信先解決 ----------
    def _resolve_line_targets(self, opts) -> List[str]:
        ids = [x.strip() for x in (opts.get("line_to") or "").split(",") if x.strip()]
        if ids:
            return ids
        if opts.get("line_all"):
            return list(LineContact.objects.values_list("user_id", flat=True))
        try:
            return [LineContact.objects.latest("created_at").user_id]
        except Exception:
            return []

    # ---------- トーンと配色（🔥/🌧/🌤 + 背景色） ----------
    def _tone_theme(self, regime: str) -> Dict[str, str]:
        """
        regime に応じてトーン絵文字と配色を返す。
        強気=淡オレンジ、慎重=淡ブルー、様子見=グレー。
        """
        rg = str(regime or "").upper()
        if "OFF" in rg:
            return dict(
                emoji="🌧",
                card="#E0F2FE",   # sky-100
                chip="#DBEAFE",   # sky-200
                primary="#2563EB",# blue-600
                heading="#111827",
                muted="#6B7280",
            )
        if "ON" in rg:
            return dict(
                emoji="🔥",
                card="#FFF7ED",   # orange-50
                chip="#FFEDE5",   # orange-100
                primary="#EA580C",# orange-600
                heading="#111827",
                muted="#6B7280",
            )
        return dict(
            emoji="🌤",
            card="#F3F4F6",     # gray-100
            chip="#E5E7EB",     # gray-200
            primary="#374151",  # gray-700
            heading="#111827",
            muted="#6B7280",
        )

    # ---------- コメント専用 Flex ----------
    def _build_flex(self, ctx: BriefContext) -> dict:
        theme = self._tone_theme(ctx.breadth_view.get("regime", "NEUTRAL"))
        mode_label = _mode_label(ctx.mode)
        comment_text = ctx.ai_comment or "—"

        body = {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "16px",
            "backgroundColor": theme["card"],     # ★ トーンに合わせたカード背景色
            "contents": [
                # ヘッダー（タイトル＋日付）— 左にトーン絵文字を表示
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {
                            "type": "text",
                            "text": f"{theme['emoji']}  AI デイリーコメント",
                            "weight": "bold",
                            "size": "lg",
                            "color": theme["primary"],
                            "flex": 8
                        },
                        {
                            "type": "text",
                            "text": ctx.asof,
                            "size": "xs",
                            "color": theme["muted"],
                            "align": "end",
                            "flex": 4
                        },
                    ]
                },

                # モード帯（寄り前/寄り直後…）
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": theme["chip"],
                    "cornerRadius": "10px",
                    "paddingAll": "10px",
                    "contents": [
                        {"type": "text", "text": f"{mode_label}（{ctx.generated_at} 時点）", "size": "xs", "color": theme["primary"]},
                        {"type": "text", "text": comment_text, "size": "md", "wrap": True, "color": theme["heading"]},
                    ]
                },
            ]
        }

        return {"type": "bubble", "size": "mega", "body": body}

    # ---------- LINE: Flex 送信 ----------
    def _send_line_flex(self, user_ids: List[str], ctx: BriefContext, opts) -> bool:
        flex = self._build_flex(ctx)
        alt = (opts.get("line_title") or f"AIデイリーコメント {ctx.asof}").strip()
        any_ok = False

        # 失敗時の最小バブル（構造 or 権限の切り分け用）
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
                    self.stdout.write(self.style.WARNING(f"LINE Flex to {uid}: {code} {getattr(r,'text','')}"))
                    rs = line_push_flex(uid, "Flex smoke test", smoke)
                    self.stdout.write(self.style.WARNING(f"  smoke test status={getattr(rs,'status_code',None)} body={getattr(rs,'text','')}"))
                else:
                    self.stdout.write(self.style.SUCCESS(f"LINE Flex to {uid}: {code}"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"LINE Flex exception (uid={uid}): {e}"))
        return any_ok