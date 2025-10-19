# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import os
import json
import glob
import random

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
            out.append("".join(buf).rstrip())
            buf, size = [line], len(line)
        else:
            buf.append(line); size += len(line)
    if buf:
        out.append("".join(buf).rstrip())
    return out


# ---------- breadth 前日スコア推定（ローカル読込） ----------
def _market_dirs() -> List[str]:
    """
    breadth_YYYY-MM-DD.json を探す候補ディレクトリ:
    - <PROJECT_ROOT>/market
    - <MEDIA_ROOT>/market
    """
    dirs = []
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir:
        dirs.append(os.path.join(str(base_dir), "market"))
    media_root = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
    dirs.append(os.path.join(media_root, "market"))
    # 正規化 & 重複排除
    seen, out = set(), []
    for d in dirs:
        dd = os.path.abspath(d)
        if dd not in seen:
            seen.add(dd); out.append(dd)
    return out

def _scan_breadth_files() -> List[Tuple[date, str]]:
    """
    breadth_YYYY-MM-DD.json を見つけて (date, path) のリスト（昇順）を返す
    """
    items: List[Tuple[date, str]] = []
    for d in _market_dirs():
        try:
            for p in glob.glob(os.path.join(d, "breadth_*.json")):
                base = os.path.basename(p)
                try:
                    key = base.split("_", 1)[1].split(".json", 1)[0]
                    dt = datetime.fromisoformat(key).date()
                    items.append((dt, p))
                except Exception:
                    continue
        except Exception:
            continue
    items.sort(key=lambda x: x[0])  # 昇順
    return items

def _guess_prev_breadth_score(asof_str: str) -> Optional[float]:
    """
    asof より前の breadth_* の中から最も最近のものの score を返す。無ければ None。
    """
    try:
        asof = datetime.fromisoformat(asof_str).date()
    except Exception:
        asof = date.today()
    items = _scan_breadth_files()
    prevs = [p for (d, p) in items if d < asof]
    if not prevs:
        # どうしても見つからない場合、最後から2番目を保険で使う
        if len(items) >= 2:
            prevs = [items[-2][1]]
        else:
            return None
    path = prevs[-1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return float(obj.get("score", 0.0))
    except Exception:
        return None


# ---------- 「今日のひとこと」 ----------
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


def _make_ai_comment(
    regime: str,
    score: float,
    sectors: List[Dict[str, Any]],
    adopt_rate: float,
    prev_score: Optional[float] = None,
    seed: str = "",
) -> str:
    """
    もっと人間味のある “今日のひとこと”
    - 砕けたトーン / 口語
    - 前日比を↗︎/↘︎/→で直感表示
    - 上位セクターを自然文で
    - シグナルの当たり具合を体感っぽく
    """
    rg = (regime or "").upper()
    top_secs = [s.get("sector", "") for s in (sectors or []) if s.get("sector")]
    tops = "・".join(top_secs[:3]) if top_secs else "とくに目立たず"

    rnd = random.Random((seed or "") + rg + f"{score:.3f}{adopt_rate:.3f}")

    # --- 前日比の言い回し
    delta_icon, delta_phrase = "→", "昨日と大きくは変わらず"
    if prev_score is not None:
        diff = round(score - float(prev_score), 2)
        if diff > 0.05:
            delta_icon, delta_phrase = "↗︎", rnd.choice(["昨日よりトーン上がってきた", "じわっと改善中", "雰囲気ひとつ明るめ"])
        elif diff < -0.05:
            delta_icon, delta_phrase = "↘︎", rnd.choice(["やや失速ぎみ", "少しトーンダウン", "警戒感がのってきた"])

    # --- 地合いオープナー
    open_on = [
        "今日は気持ちよく上を見られそう🙌",
        "全体の空気は悪くないね😎",
        "雰囲気は前向き、波に乗れそう🚀",
    ]
    open_off = [
        "無理せずいきたい空気感😪",
        "リスクは少し抑えめでいこう🛡️",
        "今日は肩の力を抜いて様子見でも👌",
    ]
    open_neu = [
        "方向感が出にくい日かも🤔",
        "上下に振れやすいので落ち着いて⚖️",
        "どちらにも行けるので慎重に🧭",
    ]

    if "ON" in rg:
        opener = rnd.choice(open_on)
        stance = rnd.choice(["強気寄り", "やや強気"])
        action = rnd.choice(["押し目拾いはアリ", "素直にトレンド追随でOK", "伸びるところに便乗で"])
        mood_emoji = "🟢"
    elif "OFF" in rg:
        opener = rnd.choice(open_off)
        stance = rnd.choice(["守り寄り", "弱気寄り"])
        action = rnd.choice(["サイズ小さめで", "現金多めで", "ディフェンシブ寄せで"])
        mood_emoji = "🔴"
    else:
        opener = rnd.choice(open_neu)
        stance = "中立"
        action = rnd.choice(["軽めに刻んで", "シナリオは複数用意で", "無理にポジらないで"])
        mood_emoji = "⚪️"

    # --- シグナルの当たり具合（採用率）
    if adopt_rate >= 0.60:
        hit = rnd.choice(["当たり感はけっこう良さげ👌", "シグナルの精度は高め👍", "今日は頼りになりそう✨"])
    elif adopt_rate >= 0.50:
        hit = rnd.choice(["まずまずの手応え🙂", "平常運転って感じ😌", "可もなく不可もなく🕊"])
    else:
        hit = rnd.choice(["ノイズ多めなので慎重に⚠️", "だましに注意👀", "深追いは禁物🙅‍♂️"])

    # --- 上位セクターを自然文で
    if top_secs:
        sec_line = f"今日は『{tops}』あたりが元気そう💡"
    else:
        sec_line = "セクターは横並びで決め手薄そう💤"

    # --- 文章を組み立て（3〜4行）
    lines = [
        f"{mood_emoji} {opener} {delta_icon} {delta_phrase}（Score {score:.2f}）",
        sec_line,
        f"{action}。スタンスは{stance}で。{hit}",
    ]
    return "\n".join(lines)


# =========================
# コマンド本体（LINE専用）
# =========================
class Command(BaseCommand):
    help = "AIデイリーブリーフを生成し、LINEに配信（メールは廃止）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--date", type=str, default="", help="対象日(YYYY-MM-DD)。未指定は今日")
        parser.add_argument("--days", type=int, default=90, help="週次サマリのlookback（日数）")
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
            _ = datetime.fromisoformat(asof_str).date()
        except Exception:
            return self.stdout.write(self.style.ERROR(f"invalid --date: {asof_str}"))

        # ---- 市況
        b = latest_breadth() or {}
        regime_view = breadth_regime(b)  # dict（regime/score等）

        # ---- 前日スコア（ローカル breadth_* から推定）
        prev_day_score = _guess_prev_breadth_score(asof_str)

        # ---- 指数
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

        # ---- ひとこと
        ai_comment = _make_ai_comment(
            regime=regime_view.get("regime", "NEUTRAL"),
            score=float(regime_view.get("score", 0.0)),
            sectors=sectors_view,
            adopt_rate=float(week_stats.get("rate", 0.0)),
            prev_score=prev_day_score,
            seed=asof_str,
        )

        ctx = BriefContext(
            asof=asof_str,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            breadth=b,
            breadth_view=regime_view,
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

    # ---------- テキスト描画（手動指定時のみ） ----------
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

        # ---- Theme by Regime -------------------------------------------------
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

        # ---- helpers ---------------------------------------------------------
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

        # ---- sector list -----------------------------------------------------
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

        # ---- body ------------------------------------------------------------
        body = {
          "type": "box",
          "layout": "vertical",
          "spacing": "lg",
          "backgroundColor": T["card"],
          "paddingAll": "16px",
          "contents": [
            # ヘッダー
            {"type": "box", "layout": "horizontal", "contents": [
                {"type": "text", "text": f"{T['icon']}  AI デイリーブリーフ",
                 "weight": "bold", "size": "lg", "color": T["primary"], "flex": 9},
                {"type": "text", "text": ctx.asof, "size": "xs", "color": T["muted"], "align": "end", "flex": 3}
            ]},

            # 今日のひとこと（Regimeに応じた薄色帯）
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

            # 地合い
            {"type": "text", "text": "地合い（Breadth）", "weight": "bold", "size": "md", "color": T["heading"]},
            row("Regime", b.get("regime","NEUTRAL"), color=T["primary"]),
            row("Score", f"{score:.2f}", signed_color(score)),
            row("A/D", f"{ad:.3f}", signed_color(ad-1.0)),
            row("VOL", f"{vol:.2f}", signed_color(vol-1.0)),
            row("H-L", f"{hl:.1f}", signed_color(hl)),

            {"type": "separator", "margin": "md"},

            # セクター
            {"type": "text", "text": "セクターRS（上位8）", "weight": "bold", "size": "md", "color": T["heading"]},
            {"type": "box", "layout": "vertical", "spacing": "sm", "contents": sector_lines},

            {"type": "separator", "margin": "md"},

            # サマリー
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

        # スモーク最小バブル（構造 or 権限の切り分け用）
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
                    self.stdout.write(self.style.WARNING(
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