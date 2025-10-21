# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import re
from typing import Dict, Any, List, Optional

# Django settings は任意（未インストール環境でも動くように try）
try:
    from django.conf import settings
except Exception:
    class _S:
        AI_COMMENT_MODEL = None
    settings = _S()  # type: ignore

# OpenAI SDK は任意依存
_OPENAI_AVAILABLE = False
try:
    from openai import OpenAI  # type: ignore
    _OPENAI_AVAILABLE = True
except Exception:
    try:
        import openai  # type: ignore
        _OPENAI_AVAILABLE = True
        OpenAI = None  # type: ignore
    except Exception:
        _OPENAI_AVAILABLE = False


# ----------------- ユーティリティ -----------------
def _shorten(text: str, limit: int = 230) -> str:
    """1段落・最大limit文字。空白整形＆末尾調整。"""
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text).strip()
    if len(t) <= limit:
        return t
    t = t[: limit - 1].rstrip()
    if not t.endswith(("。", "！", "!", "？", "?")):
        t += "…"
    return t


def _stance_from_score(score: float) -> str:
    """score(0～1想定)からざっくり需給スタンスを決める"""
    if score >= 0.6:
        return "買い寄り"
    if score <= 0.4:
        return "売り寄り"
    return "拮抗"


def _stars_from_score(score: float) -> str:
    """期待度（★0〜★★★）"""
    if score >= 0.7:
        return "★★★"
    if score >= 0.55:
        return "★★☆"
    if score >= 0.45:
        return "★☆☆"
    return "☆☆☆"


def _resolve_model_name(cli_or_kw: Optional[str] = None) -> str:
    """1) 引数 > 2) settings.AI_COMMENT_MODEL > 3) env AI_COMMENT_MODEL"""
    if cli_or_kw:
        return cli_or_kw
    model = getattr(settings, "AI_COMMENT_MODEL", None) or os.getenv("AI_COMMENT_MODEL")
    return model or "gpt-4-turbo"


# ----------------- リスクトーン補正 -----------------
def _humanize_regime(rg: str) -> str:
    rg = (rg or "").upper()
    if "ON" in rg:
        return "🔥買いが優勢（強気ムード）"
    elif "OFF" in rg:
        return "🌧売りが優勢（慎重ムード）"
    return "🌤方向感は拮抗（静かな地合い）"


# ----------------- ローカル生成（フォールバック） -----------------
def _fallback_sentence(
    *,
    regime: str,
    score: float,
    sectors: List[Dict[str, Any]],
    adopt_rate: float,
    prev_score: Optional[float],
    mode: str,
) -> str:
    tone = _humanize_regime(regime)

    top_secs = [str(s.get("sector", "")) for s in sectors if s.get("sector")]
    top_txt = "・".join(top_secs[:3]) if top_secs else "特筆なし"

    stance = _stance_from_score(float(score))
    heat = _stars_from_score(float(score))

    diff_part = ""
    if prev_score is not None:
        diff = round(float(score) - float(prev_score), 2)
        if diff > 0.05:
            diff_part = f"📈 前日比改善(+{diff:.2f}) "
        elif diff < -0.05:
            diff_part = f"📉 前日比悪化({diff:.2f}) "
        else:
            diff_part = "😐 前日比ほぼ横ばい "

    m = (mode or "").lower()
    if m == "preopen":
        tail = "寄り前は板の気配を見つつ、押し目は丁寧に拾う想定。"
    elif m == "postopen":
        tail = "寄り直後はプライスアクション優先、無理はせず優位だけ取る。"
    elif m == "noon":
        tail = "前場の流れを継続しやすい地合い、後場は出来高の伸びに注目。"
    elif m == "afternoon":
        tail = "後場は手仕舞いと押し目待ちが交錯、引けのトーンを見極めたい。"
    elif m == "outlook":
        tail = "引け後の手口は落ち着き、明日は同方向に素直に入れる場面を待ちたい。"
    else:
        tail = "全体は流れに素直、ルール通りで。"

    note = "✨ 精度は良好" if adopt_rate >= 0.55 else "🌀 シグナルはムラあり" if adopt_rate <= 0.45 else "🙂 平常運転"

    txt = (
        f"{diff_part}{tone}。温度感は「{stance}」（期待度{heat}）。"
        f" 注目は{top_txt}。{tail} {note}"
    )
    return _shorten(txt, 230)


# ----------------- 明日への展望：テンプレ整形 -----------------
def _outlook_template(
    *, regime: str, score: float, prev_score: Optional[float],
    sectors: List[Dict[str, Any]], adopt_rate: float
) -> str:
    tone = _humanize_regime(regime)
    stance = _stance_from_score(score)
    heat = _stars_from_score(score)
    top_secs = [str(s.get("sector", "")) for s in sectors if s.get("sector")][:3]
    top_txt = "・".join(top_secs) if top_secs else "特筆なし"

    if prev_score is not None:
        d = round(float(score) - float(prev_score), 2)
        if d > 0.05:
            drift = "きょうは上向きの流れで引け。"
        elif d < -0.05:
            drift = "きょうは弱含みで引け。"
        else:
            drift = "きょうは横ばい圏で引け。"
    else:
        drift = "きょうは落ち着いた引け。"

    note = "✨ 精度は良好" if adopt_rate >= 0.55 else "🌀 シグナルはムラあり" if adopt_rate <= 0.45 else "🙂 平常運転"

    text = (
        f"引け後の総括：{drift}{tone} 主役は{top_txt}。"
        f"明日の仮説：寄りの温度感は「{stance}」、期待度は{heat}。"
        f"寄り前は先物・為替・ニュースのギャップを確認し、基本は流れに素直で。{note}"
    )
    return _shorten(text, 230)


# ----------------- モード別 System Prompt -----------------
def _system_prompt_for(mode: str, persona: str) -> str:
    base_persona = (
        "あなたは日本の『億トレーダー兼経済評論家』。"
        "プロ視点で短く本質だけを示し、需給スタンス（買い/売り/拮抗）と期待度をはっきり伝える。"
        "断定は一部OKだが煽らない。専門用語の羅列は禁止。"
        "出力は日本語、2文以内・一段落・適度な絵文字。"
    )

    focus_dict = {
        "preopen":  "寄り付き前の温度感。今日は買い寄り/売り寄り/拮抗が一目で分かるように。",
        "postopen": "寄り直後の地合い。初動の強弱と継続/反転の可能性を簡潔に。",
        "noon":     "前場の総括と後場への期待を一言で。押し目待ち・続伸・様子見のいずれかを含めて。",
        "afternoon":"後場のムードと引けの雰囲気を端的に。手仕舞い/追随/見送りの温度感を示す。",
        "outlook":  "引け後の総括と翌営業日の展望を2文で。1文目は『引け後の総括（上向き/弱含み/横ばい）＋主役セクター』、2文目は『明日の仮説（買い/売り/拮抗＋期待度★）＋寄り前の注意点』を必ず含める。",
    }

    focus = focus_dict.get((mode or "").lower(), "全体の地合いと需給バランスを短く。")
    style_rules = (
        "必ず含める: 需給スタンス（買い/売り/拮抗）・期待度（★）・注目セクター1〜3個。"
        "禁止: 箇条書き・改行・冗長な免責。"
    )
    return f"{base_persona} {focus} {style_rules}"


# ----------------- メイン：AIコメント生成 -----------------
def make_ai_comment(
    *,
    regime: str,
    score: float,
    sectors: List[Dict[str, Any]],
    adopt_rate: float,
    prev_score: Optional[float] = None,
    seed: str = "",
    engine: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 180,
    mode: str = "preopen",
    persona: str = "dealer",
) -> str:
    use_api = _OPENAI_AVAILABLE and bool(os.getenv("OPENAI_API_KEY"))
    model = _resolve_model_name(engine)

    # --- 「明日への展望」はテンプレで固定（LLMに任せず確実に明日視点へ） ---
    if (mode or "").lower() == "outlook":
        return _outlook_template(
            regime=regime, score=score, prev_score=prev_score,
            sectors=sectors, adopt_rate=adopt_rate
        )

    # --- OpenAI API 不使用時 ---
    if not use_api:
        return _fallback_sentence(
            regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score, mode=mode,
        )

    # --- API使用時 ---
    top_secs = [str(s.get("sector", "")) for s in sectors if s.get("sector")][:3]
    facts = (
        f"Regime={regime}, Score={score:.3f}, AdoptRate={adopt_rate:.3f}, "
        f"PrevScore={'' if prev_score is None else f'{prev_score:.3f}'}, "
        f"TopSectors={', '.join(top_secs) if top_secs else 'なし'}"
    )

    system_msg = _system_prompt_for(mode, persona)
    user_msg = (
        "次の事実をもとに、2文以内で地合いの温度感を伝えてください。"
        "リスクオン/オフなどの専門用語は禁止、代わりに『買いが優勢』『売りが優勢』『拮抗』のいずれかを必ず使ってください。\n"
        f"- 事実: {facts}"
    )

    try:
        if OpenAI:
            client = OpenAI()
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system_msg},
                          {"role": "user", "content": user_msg}],
                temperature=float(temperature),
                max_tokens=int(max_tokens),
                seed=hash(seed) % (2**31 - 1) if seed else None,
            )
            text = resp.choices[0].message.content.strip()
        else:
            import openai  # type: ignore
            openai.api_key = os.getenv("OPENAI_API_KEY")
            resp = openai.ChatCompletion.create(  # type: ignore
                model=model,
                messages=[{"role": "system", "content": system_msg},
                          {"role": "user", "content": user_msg}],
                temperature=float(temperature),
                max_tokens=int(max_tokens),
            )
            text = resp["choices"][0]["message"]["content"].strip()  # type: ignore

        # 専門語が出た場合の補正
        text = (
            text.replace("リスクオン", "🔥買いが優勢（強気ムード）")
                .replace("リスクオフ", "🌧売りが優勢（慎重ムード）")
                .replace("ニュートラル", "🌤方向感は拮抗（静かな地合い）")
        )
        return _shorten(text, 230)

    except Exception:
        # 失敗時フォールバック
        return _fallback_sentence(
            regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score, mode=mode,
        )