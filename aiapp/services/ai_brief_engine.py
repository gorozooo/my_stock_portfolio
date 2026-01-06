from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from django.utils import timezone


def _as_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _as_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(float(x))
    except Exception:
        return default


def _s(x: Any) -> str:
    try:
        if x is None:
            return ""
        return str(x)
    except Exception:
        return ""


def _yen(n: float | int) -> str:
    try:
        v = int(round(float(n)))
    except Exception:
        v = 0
    return f"¥{v:,}"


def _hash_pick(seed: str, items: List[str]) -> str:
    if not items:
        return ""
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    idx = int(h[:8], 16) % len(items)
    return items[idx]


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _group_defs() -> Dict[str, Dict[str, Any]]:
    # “楽天” と “SBI+松井” に固定（あなたの要望）
    return {
        "rakuten": {"label": "楽天", "brokers": ["RAKUTEN"]},
        "sbi_matsui": {"label": "SBI+松井", "brokers": ["SBI", "MATSUI"]},
    }


def _get_group_equity_and_risk(ctx: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    brief_context.py が ctx.user_state.risk_groups に入れてくれた値を優先して使う。
    無ければ最低限のフォールバックを作る。
    """
    us = ctx.get("user_state") or {}
    rg = us.get("risk_groups") or {}

    out: Dict[str, Dict[str, Any]] = {}
    defs = _group_defs()

    # ctx から取れるならそれを採用
    if isinstance(rg, dict) and rg:
        for k, d in defs.items():
            got = rg.get(k)
            if isinstance(got, dict):
                out[k] = {
                    "label": _s(got.get("label") or d["label"]).strip(),
                    "brokers": got.get("brokers") or d["brokers"],
                    "equity_yen": _as_int(got.get("equity_yen"), 0),
                    "risk_yen": got.get("risk_yen", None),
                }

    # フォールバック（旧：全体account_equityから）
    if not out:
        risk_pct = _as_float(us.get("risk_pct"), 0.0)
        equity = _as_int(us.get("equity"), 0)
        risk_yen = us.get("risk_yen", None)
        out = {
            "rakuten": {"label": "楽天", "brokers": ["RAKUTEN"], "equity_yen": equity, "risk_yen": risk_yen},
            "sbi_matsui": {"label": "SBI+松井", "brokers": ["SBI", "MATSUI"], "equity_yen": 0, "risk_yen": None},
        }

    return out


def _sum_ytd_by_group(ctx: Dict[str, Any]) -> Dict[str, float]:
    """
    portfolio_state.brokers の ytd をグループ別に合算する。
    """
    ps = ctx.get("portfolio_state") or {}
    rows = ps.get("brokers") or []
    defs = _group_defs()

    ytd_map_by_broker: Dict[str, float] = {}
    if isinstance(rows, list):
        for r in rows:
            if not isinstance(r, dict):
                continue
            b = _s(r.get("broker")).strip().upper()
            y = _as_float(r.get("ytd"), 0.0)
            if b:
                ytd_map_by_broker[b] = float(y)

    out: Dict[str, float] = {}
    for gk, gd in defs.items():
        s = 0.0
        for b in gd["brokers"]:
            s += float(ytd_map_by_broker.get(str(b).upper(), 0.0))
        out[gk] = float(s)

    return out


def _allocate_goal_to_groups(goal_total: int, group_equity: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    """
    年目標（全体）しか無いので、グループ残高の比率で “自然に分配” する。
    - 楽天の体力が多いなら、楽天の目標も多め
    - 体力がゼロなら、半々に逃がす
    """
    if goal_total <= 0:
        return {"rakuten": 0, "sbi_matsui": 0}

    r_eq = _as_int((group_equity.get("rakuten") or {}).get("equity_yen"), 0)
    s_eq = _as_int((group_equity.get("sbi_matsui") or {}).get("equity_yen"), 0)
    tot = r_eq + s_eq

    if tot <= 0:
        half = int(round(goal_total / 2))
        return {"rakuten": half, "sbi_matsui": goal_total - half}

    r_goal = int(round(goal_total * (float(r_eq) / float(tot))))
    s_goal = int(goal_total - r_goal)
    return {"rakuten": r_goal, "sbi_matsui": s_goal}


@dataclass
class BriefState:
    # “思考結果”の核（ここが人格）
    topic: str = ""
    conflict: str = ""     # 迷い/対立（例：攻めたい vs ルール優先）
    comparison: str = ""   # 比較（例：ニュース主導 vs 形主導）
    policy: str = ""       # 今日の方針（例：型を1つ、サイズ半分）
    confidence: float = 0.55  # 0..1
    tone: str = "calm"     # calm / coach / sharp


def _infer_state(ctx: Dict[str, Any]) -> BriefState:
    """
    素材(ctx)から “今日の主語/迷い/比較/方針” を推論する。
    文章生成より先に「判断」を作るのがコツ。
    """
    ps = ctx.get("portfolio_state") or {}
    bs = ctx.get("behavior_state") or {}
    ms = ctx.get("market_state") or {}

    goal_total = _as_int(ps.get("goal_year_total"), 0)
    ytd_total = _as_float(ps.get("realized_ytd"), 0.0)

    trades7 = _as_int(((bs.get("last_7d") or {}).get("trades")), 0)
    pnl7 = _as_float(((bs.get("last_7d") or {}).get("pnl_sum")), 0.0)

    group_eq = _get_group_equity_and_risk(ctx)

    themes = ms.get("themes") or []
    news_top = ms.get("news_top") or []

    # topic: まずはテーマ上位
    topic = ""
    if isinstance(themes, list):
        # “その他”は弱いので後ろへ
        cleaned = []
        for t in themes:
            if not isinstance(t, dict):
                continue
            name = _s(t.get("name")).strip()
            if not name:
                continue
            cleaned.append((name, _as_float(t.get("strength"), 0.0), _as_int(t.get("count"), 0)))
        cleaned.sort(key=lambda x: (x[1], x[2]), reverse=True)
        for name, _, _ in cleaned[:3]:
            if name and name != "その他":
                topic = name
                break

    if not topic and isinstance(news_top, list) and news_top:
        # newsのtopic推定があれば拾う
        for it in news_top[:6]:
            if isinstance(it, dict):
                tp = _s(it.get("topic")).strip()
                if tp:
                    topic = tp
                    break

    # 進捗感：目標に対してytdが極端に低いなら“焦り”寄り
    progress_ratio = 0.0
    if goal_total > 0:
        progress_ratio = _clamp01(float(ytd_total) / float(goal_total))

    # confidence（材料の濃さ）
    conf = 0.45
    if goal_total > 0:
        conf += 0.10
    if topic:
        conf += 0.10
    if trades7 > 0:
        conf += 0.15
    if abs(pnl7) > 0:
        conf += 0.05
    conf = _clamp01(conf)

    # tone
    tone = "calm"
    if conf >= 0.75:
        tone = "coach"
    if progress_ratio < 0.10 and goal_total > 0:
        tone = "sharp"

    conflict = ""
    comparison = ""
    policy = ""

    if goal_total > 0 and progress_ratio < 0.10:
        conflict = "取りに行きたい気持ちが出る。でも今は“負け方”を止めるのが先。"
        comparison = "ニュースを追うより、型（条件）に落として待つ方が勝率が上がる。"
    elif goal_total <= 0:
        conflict = "利益を追うとブレる。今日は“再現性”だけを積む日に寄せる。"
        comparison = "相場を読みに行くより、ルールを先に置いた方がミスが減る。"
    else:
        conflict = "攻めるなら攻めるで、条件を固定しないと雑になる。"
        comparison = "テーマを見る → 監視条件に変換 → 入る/入らないを機械化、の順が強い。"

    if trades7 == 0:
        policy = "今日は“仕込みと監視”の日。入る条件を2つ作って、入らない条件も1つ決める。"
    else:
        if pnl7 < 0:
            policy = "今日はサイズ半分＋回数上限。勝ちに行く前に、負けの連鎖を切る。"
        else:
            policy = "勝ってる時ほどルールを守る。型を1つに絞って、同じ手順で回す。"

    # リスク設定が薄いなら補強（グループ値を優先）
    us = ctx.get("user_state") or {}
    risk_pct = _as_float(us.get("risk_pct"), 0.0)
    r_risk = (group_eq.get("rakuten") or {}).get("risk_yen", None)
    s_risk = (group_eq.get("sbi_matsui") or {}).get("risk_yen", None)
    if risk_pct <= 0 or (not r_risk and not s_risk):
        policy += " まず1トレードの損失上限（%と円）を固定してから動く。"

    return BriefState(
        topic=topic,
        conflict=conflict,
        comparison=comparison,
        policy=policy,
        confidence=conf,
        tone=tone,
    )


def _compose_summary(seed: str, st: BriefState) -> str:
    """
    100〜160文字の人格核：
    「迷い」「比較」「今日の方針」を必ず入れる（ただし定型を避け、組み立てる）
    """
    openers = {
        "sharp": [
            "今日、余計なことはしない。",
            "焦りが出る日ほど、手数を減らす。",
            "勝ちに行く前に、負けを止める。",
        ],
        "coach": [
            "今日のテーマは“再現性”。",
            "大事なのは判断の一貫性。",
            "形を揃えるほど、結果は安定する。",
        ],
        "calm": [
            "今日は落ち着いて整える。",
            "ノイズを切って、焦点を1つに。",
            "読むより、条件に落とす。",
        ],
    }

    bridges = [
        "ただ、{conflict}",
        "でも、{conflict}",
        "とはいえ、{conflict}",
    ]
    compare_phrases = [
        "比較すると、{comparison}",
        "結局、{comparison}",
        "見方を変えると、{comparison}",
    ]
    actions = [
        "結論：{policy}",
        "今日の方針はこれ。{policy}",
        "やることはシンプル。{policy}",
    ]

    o = _hash_pick(seed + ":o", openers.get(st.tone, openers["calm"]))
    b = _hash_pick(seed + ":b", bridges).format(conflict=st.conflict)
    c = _hash_pick(seed + ":c", compare_phrases).format(comparison=st.comparison)
    a = _hash_pick(seed + ":a", actions).format(policy=st.policy)

    if st.topic:
        mid = f"主語は「{st.topic}」。"
        parts = [o, mid, b, c, a]
    else:
        parts = [o, b, c, a]

    s = " ".join([p for p in parts if p]).strip()

    if len(s) > 170:
        s = s.replace("比較すると、", "").replace("見方を変えると、", "")
        s = s.replace("ただ、", "").replace("とはいえ、", "")
    return s


def _build_reasons(ctx: Dict[str, Any], st: BriefState) -> List[str]:
    ps = ctx.get("portfolio_state") or {}
    us = ctx.get("user_state") or {}
    bs = ctx.get("behavior_state") or {}
    ms = ctx.get("market_state") or {}

    goal_total = _as_int(ps.get("goal_year_total"), 0)
    ytd_total = _as_float(ps.get("realized_ytd"), 0.0)
    mtd_total = _as_float(ps.get("realized_mtd"), 0.0)

    trades7 = _as_int(((bs.get("last_7d") or {}).get("trades")), 0)
    pnl7 = _as_float(((bs.get("last_7d") or {}).get("pnl_sum")), 0.0)

    risk_pct = _as_float(us.get("risk_pct"), 0.0)

    group_eq = _get_group_equity_and_risk(ctx)
    group_ytd = _sum_ytd_by_group(ctx)
    group_goal = _allocate_goal_to_groups(goal_total, group_eq)

    reasons: List[str] = []

    # --- 年目標：グループ別に表示（あなたの要望） ---
    if goal_total > 0:
        r_goal = group_goal.get("rakuten", 0)
        s_goal = group_goal.get("sbi_matsui", 0)
        r_ytd = group_ytd.get("rakuten", 0.0)
        s_ytd = group_ytd.get("sbi_matsui", 0.0)

        reasons.append(
            f"年目標は分けて見る：楽天 {_yen(r_goal)} / YTD {_yen(r_ytd)}、SBI+松井 {_yen(s_goal)} / YTD {_yen(s_ytd)}。"
        )
        reasons.append(f"全体では YTD {_yen(ytd_total)} / MTD {_yen(mtd_total)}。数字は“現実固定”に使う。")
    else:
        reasons.append("目標が未設定だと判断基準がブレやすい。まず“守るルール”を優先する。")

    # --- 許容損失：グループ別に表示（あなたの要望） ---
    if risk_pct > 0:
        r_risk = (group_eq.get("rakuten") or {}).get("risk_yen", None)
        s_risk = (group_eq.get("sbi_matsui") or {}).get("risk_yen", None)

        parts = []
        if r_risk is not None:
            parts.append(f"楽天 {risk_pct:.1f}%（目安 {_yen(r_risk)}）")
        if s_risk is not None:
            parts.append(f"SBI+松井 {risk_pct:.1f}%（目安 {_yen(s_risk)}）")

        if parts:
            reasons.append("1トレードの許容損失は " + " / ".join(parts) + "。ここを超える判断は全部“事故”。")
        else:
            reasons.append(f"リスク%は {risk_pct:.1f}%。口座別（現金+評価額×%）の円換算も合わせて固定すると迷いが減る。")
    else:
        reasons.append("リスク%が未設定。サイズ計算が崩れるので、先にここを決める。")

    # --- 行動状態 ---
    if trades7 == 0:
        reasons.append("直近7日で取引が無い＝材料が薄い。今日は条件作りと記録を厚くする日。")
    else:
        if pnl7 < 0:
            reasons.append("直近がマイナスの時は取り返しが最大の敵。サイズと回数を縛るほど生存率が上がる。")
        else:
            reasons.append("直近がプラスの時こそ雑になりやすい。手順固定が次のドローダウンを防ぐ。")

    # --- market ---
    themes = ms.get("themes") or []
    if st.topic:
        reasons.append(f"ニュース側の主語は「{st.topic}」。読むだけ禁止で、監視条件に変換する。")
    elif isinstance(themes, list) and themes:
        top = []
        for t in themes[:3]:
            if isinstance(t, dict):
                nm = _s(t.get("name")).strip()
                cnt = _as_int(t.get("count"), 0)
                if nm:
                    top.append(f"{nm}×{cnt}")
        if top:
            reasons.append("注目セクターは " + " / ".join(top) + "。テーマの熱を“条件化”して使う。")

    return reasons[:5]


def _build_concerns(ctx: Dict[str, Any], st: BriefState) -> List[str]:
    us = ctx.get("user_state") or {}
    bs = ctx.get("behavior_state") or {}
    ms = ctx.get("market_state") or {}

    risk_pct = _as_float(us.get("risk_pct"), 0.0)
    trades7 = _as_int(((bs.get("last_7d") or {}).get("trades")), 0)

    concerns: List[str] = []

    if risk_pct <= 0:
        concerns.append("リスク設定が未固定だと、どの判断も“後出し”になって崩れやすい。")
    if trades7 == 0:
        concerns.append("材料（取引ログ）が薄い日は、AIも薄くなる。今日は“記録を増やす”が最短。")

    themes = ms.get("themes") or []
    if isinstance(themes, list) and themes:
        for t in themes[:2]:
            if isinstance(t, dict) and _s(t.get("name")).strip() == "その他" and _as_float(t.get("strength"), 0.0) >= 0.8:
                concerns.append("ニュースの主語が散ってる（その他が強い）。追いかけるほど迷いが増える日。")
                break

    if not concerns:
        concerns.append("迷いが出たら“比較”をやめて、条件とサイズで機械化する。")

    return concerns[:2]


def _build_escape(ctx: Dict[str, Any], st: BriefState) -> str:
    group_eq = _get_group_equity_and_risk(ctx)

    # まず楽天、なければSBI+松井で “逃げ道” を作る（1行に収める）
    r = (group_eq.get("rakuten") or {}).get("risk_yen", None)
    s = (group_eq.get("sbi_matsui") or {}).get("risk_yen", None)

    if r:
        return f"逃げ道：楽天の逆指値が {_yen(r)} の範囲に収まらないなら、その時点で見送り。"
    if s:
        return f"逃げ道：SBI+松井の逆指値が {_yen(s)} の範囲に収まらないなら、その時点で見送り。"
    return "逃げ道：逆指値を置けないなら見送り。迷いが出たらサイズ半分。"


def build_ai_brief_from_ctx(
    *,
    ctx: Dict[str, Any],
    user_id: int,
) -> Dict[str, Any]:
    """
    出力は home.html が受け取れる形に合わせる。
    """
    now_iso = timezone.now().isoformat()
    d = _s(ctx.get("date"))

    seed = f"{user_id}:{d}:{_s((ctx.get('user_state') or {}).get('mode_period'))}:{_s((ctx.get('user_state') or {}).get('mode_aggr'))}"

    st = _infer_state(ctx)

    summary = _compose_summary(seed, st)
    reasons = _build_reasons(ctx, st)
    concerns = _build_concerns(ctx, st)
    escape = _build_escape(ctx, st)

    reference_news = None
    try:
        ms = ctx.get("market_state") or {}
        top = (ms.get("news_top") or [])
        if isinstance(top, list) and top and isinstance(top[0], dict):
            it = top[0]
            reference_news = {
                "title": _s(it.get("title")).strip(),
                "source": _s(it.get("source")).strip() or "NEWS",
                "host": _s(it.get("host")).strip(),
                "link": _s(it.get("url")).strip(),
                "hint": "主語に変換して監視条件へ",
            }
    except Exception:
        reference_news = None

    return {
        "title": "AI BRIEF",
        "status": "ok",
        "as_of": now_iso,
        "summary": summary,
        "reasons": reasons,
        "concerns": concerns,
        "escape": escape,
        "reference_news": reference_news,
        "confidence": st.confidence,
        "tone": st.tone,
        "topic": st.topic,
    }