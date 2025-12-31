# aiapp/services/home_today_plan.py
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.utils import timezone


logger = logging.getLogger(__name__)


# =========================
# helpers
# =========================
def _now_iso() -> str:
    return timezone.now().isoformat()


def _d(x) -> Decimal:
    try:
        if x is None:
            return Decimal("0")
        return Decimal(str(x))
    except Exception:
        return Decimal("0")


def _fmt_yen(x: Decimal) -> str:
    # 表示はテンプレ側で intcomma するので、ここは素の数を返す想定
    # ただしログ用途で使うこともあるので関数は残す
    try:
        return f"{int(x):,}"
    except Exception:
        return "0"


def _safe_get(dct: Any, path: List[str], default: Any = None) -> Any:
    cur = dct
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def _pick_top_sector(news_trends: Dict[str, Any]) -> Optional[str]:
    sectors = news_trends.get("sectors") if isinstance(news_trends, dict) else None
    if not isinstance(sectors, list) or not sectors:
        return None
    s0 = sectors[0]
    if isinstance(s0, dict):
        return (s0.get("sector") or "").strip() or None
    return None


def _extract_keywords_from_titles(items: List[Dict[str, Any]], limit: int = 10) -> List[str]:
    """
    ニュースタイトルから “それっぽい単語” を雑抽出（再現性のためルール固定）
    - 日本語: 2文字以上の連続
    - 英数字: 3文字以上の連続
    """
    text = " ".join([(it.get("title") or "") for it in (items[:limit] if items else [])])
    text = re.sub(r"\s+", " ", text)

    kws: List[str] = []

    # 日本語（漢字/ひらがな/カタカナ）2文字以上
    for m in re.finditer(r"[一-龥ぁ-んァ-ヴー]{2,}", text):
        w = m.group(0)
        if len(w) >= 2:
            kws.append(w)

    # 英数字 3文字以上
    for m in re.finditer(r"[A-Za-z0-9]{3,}", text):
        w = m.group(0)
        if len(w) >= 3:
            kws.append(w)

    # よくあるノイズを削る
    stop = {"NEWS", "Trends", "http", "https", "www", "com", "co", "jp"}
    out: List[str] = []
    seen = set()
    for w in kws:
        if w in stop:
            continue
        if w in seen:
            continue
        seen.add(w)
        out.append(w)

    return out[:8]


def _mode_from_goal(goal_year_total: Decimal, ytd_total: Decimal) -> Dict[str, str]:
    """
    今日のモード（再現性固定）
    - 目標0: "運用"
    - 残りがプラス: "回収"
    - 残りがマイナス/ゼロ: "守り"
    """
    if goal_year_total <= 0:
        return {"key": "operate", "label": "運用（改善）", "tone": "mid"}
    remain = goal_year_total - ytd_total
    if remain > 0:
        return {"key": "catchup", "label": "回収（ペース不足）", "tone": "high"}
    return {"key": "defend", "label": "守り（達成圏）", "tone": "low"}


def _worst_broker(by_broker_rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    broker別YTDの “弱い順” を1つ（最小）
    """
    if not by_broker_rows:
        return None
    rows = []
    for r in by_broker_rows:
        if not isinstance(r, dict):
            continue
        ytd = r.get("ytd", 0)
        try:
            ytd_f = float(ytd)
        except Exception:
            ytd_f = 0.0
        rows.append((ytd_f, r))
    if not rows:
        return None
    rows.sort(key=lambda x: x[0])  # 小さい順
    return rows[0][1]


# =========================
# public
# =========================
def build_today_plan_snapshot(
    user,
    assets: Dict[str, Any],
    news_trends: Dict[str, Any],
) -> Dict[str, Any]:
    """
    TODAY PLAN（Home用）
    - ASSETS（実現損益/目標/ペース）と NEWS & TRENDS から
      毎日3アクションを “ルールで” 固定生成する（再現性ファースト）
    """
    try:
        # --- inputs ---
        ytd_total = _d(_safe_get(assets, ["realized", "ytd", "total"], 0))
        goal_year_total = _d(_safe_get(assets, ["goals", "year_total"], 0))

        by_broker_rows = _safe_get(assets, ["pace", "by_broker_rows"], []) or []
        if not isinstance(by_broker_rows, list):
            by_broker_rows = []

        top_sector = _pick_top_sector(news_trends)
        news_items = news_trends.get("items") if isinstance(news_trends, dict) else None
        if not isinstance(news_items, list):
            news_items = []
        trends_items = news_trends.get("trends") if isinstance(news_trends, dict) else None
        if not isinstance(trends_items, list):
            trends_items = []

        kws = _extract_keywords_from_titles(news_items, limit=10)
        kws2 = _extract_keywords_from_titles(trends_items, limit=10)
        keywords = (kws + [k for k in kws2 if k not in kws])[:8]

        mode = _mode_from_goal(goal_year_total, ytd_total)

        weak = _worst_broker(by_broker_rows)
        weak_label = (weak.get("label") if isinstance(weak, dict) else None) or "（不明）"
        try:
            weak_ytd = Decimal(str(weak.get("ytd", 0))) if isinstance(weak, dict) else Decimal("0")
        except Exception:
            weak_ytd = Decimal("0")

        # --- action templates (fixed) ---
        theme = top_sector or "全体（マクロ）"
        kw_txt = " / ".join(keywords[:4]) if keywords else "材料整理"

        # Action #1: 目標・ペース
        if goal_year_total <= 0:
            a1_title = "今日の“勝ち方の型”を1つ固定する"
            a1_why = [
                "年間目標が未設定なので、まずは“再現性のある型”を先に固める",
                "Homeの運用を「意思決定 → 実行 → ログ」の流れに寄せる",
                f"ニュース材料（参考）: {kw_txt}",
            ]
            a1_do = [
                "本日ルールを1つ決める（例: 逆指値の幅/利確R/取引回数上限）",
                "決めたルールをメモ（policy_key / strategy_label）に残す",
            ]
            a1_watch = [
                "ルール逸脱をしそうな瞬間が来たら「理由」を1行で残す",
            ]
            a1_level = "mid"
            a1_tag = "運用"
        else:
            remain = goal_year_total - ytd_total
            a1_title = f"年目標の残りを“月ペース”で可視化して動く"
            a1_why = [
                f"年目標: {int(goal_year_total):,} / YTD: {int(ytd_total):,} / 残り: {int(remain):,}",
                f"今日のモードは「{mode['label']}」",
                f"テーマ（ニュース起点）: {theme}",
            ]
            a1_do = [
                "ASSETSの「必要ペース（月/週）」を見て、今日の稼働量を決める",
                "売買するなら「狙う形」を1つに絞る（押し目/ブレイク等）",
            ]
            a1_watch = [
                "今日のテーマに関するニュースが追加で出たら“条件”を更新",
            ]
            a1_level = "high" if mode["tone"] == "high" else ("low" if mode["tone"] == "low" else "mid")
            a1_tag = "目標/ペース"

        # Action #2: 弱い証券会社を刺す
        a2_title = f"{weak_label}の“負け方”を止める（YTD最弱を改善）"
        a2_why = [
            f"証券会社別YTDで一番弱いのが {weak_label}（YTD {int(weak_ytd):,}）",
            "弱点を1つ潰すだけで、月のブレが一気に小さくなる",
            "改善は“新戦略”ではなく“禁止事項”から入るのが速い",
        ]
        a2_do = [
            f"{weak_label}は今日「やらないこと」を1つ決める（例: 逆張り禁止/持ち越し禁止）",
            "取引するなら“同じ型だけ”に限定してログを厚くする",
        ]
        a2_watch = [
            "同じミスが出たら、次の1回はサイズ半分に落とす（自動ルール）",
        ]
        a2_level = "mid"
        a2_tag = "証券会社別"

        # Action #3: ニュース→監視条件（ウォッチ）
        a3_title = f"今日のテーマ「{theme}」を“条件ウォッチ化”する"
        a3_why = [
            "ニュースは読むだけだと流れる。条件に変換すると武器になる",
            f"材料候補: {kw_txt}",
            "Homeのトップテロップ（📰）と連動して“監視→行動”に繋げる",
        ]
        a3_do = [
            "気になった見出しを1つ選び、監視条件（上抜け/下抜け/イベント日）に落とす",
            "条件を満たしたら「OrderMemo」へ下書き（将来のワンタップ発注へ）",
        ]
        a3_watch = [
            "同テーマの見出しが増えるほど“過熱”として扱う（追いかけ禁止にする等）",
        ]
        a3_level = "low"
        a3_tag = "ニュース→条件"

        actions = [
            {
                "rank": 1,
                "title": a1_title,
                "tag": a1_tag,
                "why": a1_why,
                "do": a1_do,
                "watch": a1_watch,
                "level": a1_level,
            },
            {
                "rank": 2,
                "title": a2_title,
                "tag": a2_tag,
                "why": a2_why,
                "do": a2_do,
                "watch": a2_watch,
                "level": a2_level,
            },
            {
                "rank": 3,
                "title": a3_title,
                "tag": a3_tag,
                "why": a3_why,
                "do": a3_do,
                "watch": a3_watch,
                "level": a3_level,
            },
        ]

        notes = [
            "※これは“ルール生成”なので、同じ入力（ASSETS/NEWS）なら同じTODAY PLANになります。",
            "※次の段階で Watch / Policy / Holding と繋ぐと、条件が具体的に自動化されます。",
        ]

        return {
            "title": "TODAY PLAN",
            "status": "ok",
            "as_of": _now_iso(),
            "mode": mode,         # {"key","label","tone"}
            "theme": theme,
            "keywords": keywords,
            "actions": actions,
            "notes": notes,
        }

    except Exception as e:
        logger.exception("build_today_plan_snapshot failed: %s", e)
        return {
            "title": "TODAY PLAN",
            "status": "error",
            "as_of": _now_iso(),
            "error": str(e),
            "actions": [],
            "notes": ["TODAY PLANの生成に失敗しました。ログを確認してください。"],
        }