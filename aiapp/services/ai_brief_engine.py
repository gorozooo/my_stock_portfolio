# aiapp/services/ai_brief_engine.py
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
    us = ctx.get("user_state") or {}
    ps = ctx.get("portfolio_state") or {}
    bs = ctx.get("behavior_state") or {}
    ms = ctx.get("market_state") or {}

    goal = _as_int(ps.get("goal_year_total"), 0)
    ytd = _as_float(ps.get("realized_ytd"), 0.0)
    mtd = _as_float(ps.get("realized_mtd"), 0.0)

    trades7 = _as_int(((bs.get("last_7d") or {}).get("trades")), 0)
    pnl7 = _as_float(((bs.get("last_7d") or {}).get("pnl_sum")), 0.0)

    risk_pct = _as_float(us.get("risk_pct"), 0.0)
    risk_yen = us.get("risk_yen", None)

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
    # （ここは将来、月/週のペース計算を入れる）
    progress_ratio = 0.0
    if goal > 0:
        progress_ratio = _clamp01(float(ytd) / float(goal))

    # confidence（材料の濃さ）
    # 取引があり、テーマがあり、目標が入っていれば上がる
    conf = 0.45
    if goal > 0:
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
    if progress_ratio < 0.10 and goal > 0:
        tone = "sharp"  # 進捗が薄いのに目標がある＝引き締め

    # conflict（迷い）とcomparison（比較）とpolicy（方針）
    conflict = ""
    comparison = ""
    policy = ""

    if goal > 0 and progress_ratio < 0.10:
        conflict = "取りに行きたい気持ちが出る。でも今は“負け方”を止めるのが先。"
        comparison = "ニュースを追うより、型（条件）に落として待つ方が勝率が上がる。"
    elif goal <= 0:
        conflict = "利益を追うとブレる。今日は“再現性”だけを積む日に寄せる。"
        comparison = "相場を読みに行くより、ルールを先に置いた方がミスが減る。"
    else:
        conflict = "攻めるなら攻めるで、条件を固定しないと雑になる。"
        comparison = "テーマを見る → 監視条件に変換 → 入る/入らないを機械化、の順が強い。"

    # 直近取引ゼロなら “準備の日” に寄せる
    if trades7 == 0:
        policy = "今日は“仕込みと監視”の日。入る条件を2つ作って、入らない条件も1つ決める。"
    else:
        # 直近で負けてるなら守り、勝ってるなら規律維持
        if pnl7 < 0:
            policy = "今日はサイズ半分＋回数上限。勝ちに行く前に、負けの連鎖を切る。"
        else:
            policy = "勝ってる時ほどルールを守る。型を1つに絞って、同じ手順で回す。"

    # risk設定が薄いなら補強
    if risk_pct <= 0 or not risk_yen:
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
    # 言い回しの“部品”を複数持って、seedで選ぶ（テンプレ臭を消す）
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

    # topicがあれば自然に混ぜる
    if st.topic:
        # うるさくしない（1回だけ出す）
        mid = f"主語は「{st.topic}」。"
        parts = [o, mid, b, c, a]
    else:
        parts = [o, b, c, a]

    s = " ".join([p for p in parts if p]).strip()

    # 長すぎたら軽く削る（雑でOK、まず動かす）
    if len(s) > 170:
        s = s.replace("比較すると、", "").replace("見方を変えると、", "")
        s = s.replace("ただ、", "").replace("とはいえ、", "")
    return s


def _build_reasons(ctx: Dict[str, Any], st: BriefState) -> List[str]:
    ps = ctx.get("portfolio_state") or {}
    us = ctx.get("user_state") or {}
    bs = ctx.get("behavior_state") or {}
    ms = ctx.get("market_state") or {}

    goal = _as_int(ps.get("goal_year_total"), 0)
    ytd = _as_float(ps.get("realized_ytd"), 0.0)
    mtd = _as_float(ps.get("realized_mtd"), 0.0)
    trades7 = _as_int(((bs.get("last_7d") or {}).get("trades")), 0)
    pnl7 = _as_float(((bs.get("last_7d") or {}).get("pnl_sum")), 0.0)

    risk_pct = _as_float(us.get("risk_pct"), 0.0)
    risk_yen = us.get("risk_yen", None)

    reasons: List[str] = []

    if goal > 0:
        reasons.append(f"年目標 {_yen(goal)} に対して、YTD {_yen(ytd)} / MTD {_yen(mtd)}。数字が“今の現実”を固定する。")
    else:
        reasons.append("目標が未設定だと判断基準がブレやすい。まず“守るルール”を優先する。")

    if risk_pct > 0:
        if risk_yen:
            reasons.append(f"1トレードの許容損失は {risk_pct:.1f}%（目安 {_yen(risk_yen)}）。ここを超える判断は全部“事故”。")
        else:
            reasons.append(f"リスク%は {risk_pct:.1f}%。円換算（口座残高×%）も合わせて固定すると迷いが減る。")
    else:
        reasons.append("リスク%が未設定。サイズ計算が崩れるので、先にここを決める。")

    if trades7 == 0:
        reasons.append("直近7日で取引が無い＝“勝ちパターン/負けパターンの材料”が不足。今日は条件作りと記録を厚くする日。")
    else:
        if pnl7 < 0:
            reasons.append("直近がマイナスの時は、取り返しが最大の敵。サイズと回数を縛るほど生存率が上がる。")
        else:
            reasons.append("直近がプラスの時こそ雑になりやすい。手順固定が次のドローダウンを防ぐ。")

    # market
    themes = ms.get("themes") or []
    if st.topic:
        reasons.append(f"ニュース側の主語は「{st.topic}」。読むだけ禁止で、監視条件に変換する。")
    elif isinstance(themes, list) and themes:
        # 上位をさらっと
        top = []
        for t in themes[:3]:
            if isinstance(t, dict):
                nm = _s(t.get("name")).strip()
                cnt = _as_int(t.get("count"), 0)
                if nm:
                    top.append(f"{nm}×{cnt}")
        if top:
            reasons.append("注目セクターは " + " / ".join(top) + "。テーマの熱を“条件化”して使う。")

    # brokers
    brokers = ps.get("brokers") or []
    if isinstance(brokers, list) and brokers:
        # ytdが動いてるブローカーがあれば拾う
        movers = sorted(
            [b for b in brokers if isinstance(b, dict)],
            key=lambda x: abs(_as_float(x.get("ytd"), 0.0)),
            reverse=True,
        )
        if movers:
            b0 = movers[0]
            label = _s(b0.get("label")).strip()
            y = _as_float(b0.get("ytd"), 0.0)
            if label:
                reasons.append(f"ブローカー別の中心は {label}（YTD {_yen(y)}）。主戦場を決めると迷いが減る。")

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

    # ニュースが雑多なら注意
    themes = ms.get("themes") or []
    if isinstance(themes, list) and themes:
        # “その他”が強い = 主語が散ってる
        for t in themes[:2]:
            if isinstance(t, dict) and _s(t.get("name")).strip() == "その他" and _as_float(t.get("strength"), 0.0) >= 0.8:
                concerns.append("ニュースの主語が散ってる（その他が強い）。追いかけるほど迷いが増える日。")
                break

    # topicあれば別の懸念も作れるが、まずは2つまで
    if not concerns:
        concerns.append("迷いが出たら“比較”をやめて、条件とサイズで機械化する。")

    return concerns[:2]


def _build_escape(ctx: Dict[str, Any], st: BriefState) -> str:
    us = ctx.get("user_state") or {}
    risk_yen = us.get("risk_yen", None)

    if risk_yen:
        return f"逃げ道：逆指値が {_yen(risk_yen)} の範囲に収まらないなら、その時点で見送り。"
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

    # seed：日付×ユーザー×（主語）で微差を出す
    seed = f"{user_id}:{d}:{_s((ctx.get('user_state') or {}).get('mode_period'))}:{_s((ctx.get('user_state') or {}).get('mode_aggr'))}"

    st = _infer_state(ctx)

    summary = _compose_summary(seed, st)
    reasons = _build_reasons(ctx, st)
    concerns = _build_concerns(ctx, st)
    escape = _build_escape(ctx, st)

    # 外部参照（news_top の先頭だけ、隔離枠として）
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
        "summary": summary,          # ← 人格核（長さは“目安”、まず動かす）
        "reasons": reasons,          # ← 内部データ由来
        "concerns": concerns,        # ← 自己批判/注意点
        "escape": escape,            # ← 逃げ道（1行）
        "reference_news": reference_news,  # ← 外部は隔離
        "confidence": st.confidence,
        "tone": st.tone,
        "topic": st.topic,
    }