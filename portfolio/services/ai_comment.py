# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import re
from typing import Dict, Any, List, Optional

# Django設定
try:
    from django.conf import settings
except Exception:
    class _S: AI_COMMENT_MODEL = None
    settings = _S()  # type: ignore

# OpenAI SDK
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


# ----------------- 共通ユーティリティ -----------------
def _shorten(text: str, limit: int = 230) -> str:
    """1段落・最大limit文字。空白整形＆末尾調整。"""
    if not text: return ""
    t = re.sub(r"\s+", " ", text).strip()
    if len(t) <= limit: return t
    t = t[: limit - 1].rstrip()
    if not t.endswith(("。", "！", "!", "？", "?")):
        t += "…"
    return t


def _stance_from_score(score: float) -> str:
    if score >= 0.6: return "買い寄り"
    if score <= 0.4: return "売り寄り"
    return "拮抗"


def _stars_from_score(score: float) -> str:
    if score >= 0.7: return "★★★"
    if score >= 0.55: return "★★☆"
    if score >= 0.45: return "★☆☆"
    return "☆☆☆"


def _resolve_model_name(cli_or_kw: Optional[str] = None) -> str:
    if cli_or_kw: return cli_or_kw
    model = getattr(settings, "AI_COMMENT_MODEL", None) or os.getenv("AI_COMMENT_MODEL")
    return model or "gpt-4-turbo"


def _humanize_regime(rg: str) -> str:
    rg = (rg or "").upper()
    if "ON" in rg:
        return "🔥買いが優勢（強気ムード）"
    elif "OFF" in rg:
        return "🌧売りが優勢（慎重ムード）"
    return "🌤方向感は拮抗（静かな地合い）"


# ----------------- 人格プロンプト -----------------
def _persona_text(persona: str) -> str:
    p = (persona or "gorozooo").lower()
    if p == "gorozooo":
        return (
            "あなたは『gorozooo』という、アナリスト×ディーラー×評論家のハイブリッド人格。"
            "数字は冷静に読むが、相場の“温度感”を大事にする。"
            "専門用語よりも、肌感・リズム・直感で伝える。"
            "文体は短く・鋭く・人間味があり、自然な絵文字を交えて温度を表現する。"
            "分析は理性的、語り口はフレンドリーで余韻がある。"
        )
    if p == "analyst":
        return "客観と数値に強いアナリスト。要因を整理して、冷静に短くまとめる。"
    if p == "dealer":
        return "板と流れを読むディーラー。感覚的・スピーディーに結論を出す。"
    if p == "critic":
        return "背景と文脈を読む評論家。少し距離を置きながら核心を突く。"
    return "アナリスト×ディーラー×評論家のハイブリッドとして振る舞う。"


# ----------------- GPTプロンプト -----------------
def _system_prompt_for(mode: str, persona: str) -> str:
    persona_block = _persona_text(persona)

    base_rules = (
        "出力は日本語、2文以内・一段落。"
        "適度に絵文字(1〜3個)を入れて温度を伝える。"
        "『リスクオン/オフ』は禁止、代わりに『買いが優勢』『売りが優勢』『拮抗』を使用。"
        "必ず含める: 需給スタンス（買い/売り/拮抗）・期待度（★）・注目セクター1〜3個。"
        "禁止: 箇条書き・改行・免責文。"
    )

    focus_map = {
        "preopen":  "寄り付き前の温度感。今日の初手が一目で分かるように。",
        "postopen": "寄り直後の地合い。勢いと反動の可能性を端的に。",
        "noon":     "前場の総括と後場への見立て。次の流れを暗示するように。",
        "afternoon":"後場の雰囲気と引けのトーン。余韻や静けさも表現してよい。",
        "outlook":  "引け後の総括＋翌営業日の展望。市場の呼吸や期待感を自然に表現。",
    }
    focus = focus_map.get((mode or "").lower(), "全体の地合いを短くまとめる。")

    return f"{persona_block} {base_rules} {focus}"


# ----------------- フォールバック -----------------
def _fallback_sentence(*, regime, score, sectors, adopt_rate, prev_score, mode) -> str:
    tone = _humanize_regime(regime)
    stance = _stance_from_score(score)
    heat = _stars_from_score(score)
    top_secs = [s.get("sector") for s in sectors if s.get("sector")][:3]
    top_txt = "・".join(top_secs) if top_secs else "特筆なし"
    return _shorten(f"{tone}。温度感は{stance}（期待度{heat}）。注目は{top_txt}。")


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
    persona: str = "gorozooo",
) -> str:

    use_api = _OPENAI_AVAILABLE and bool(os.getenv("OPENAI_API_KEY"))
    model = _resolve_model_name(engine)

    if not use_api:
        return _fallback_sentence(
            regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score, mode=mode,
        )

    top_secs = [s.get("sector") for s in sectors if s.get("sector")][:3]
    facts = (
        f"Regime={regime}, Score={score:.3f}, AdoptRate={adopt_rate:.3f}, "
        f"PrevScore={'' if prev_score is None else f'{prev_score:.3f}'}, "
        f"TopSectors={', '.join(top_secs) if top_secs else 'なし'}"
    )

    system_msg = _system_prompt_for(mode, persona)
    user_msg = (
        "次の事実をもとに、今日の地合いを短く人間らしく要約してください。"
        "テンションや雰囲気も含め、相場を肌感覚で伝えるように。\n"
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

        text = (
            text.replace("リスクオン", "🔥買いが優勢（強気ムード）")
                .replace("リスクオフ", "🌧売りが優勢（慎重ムード）")
                .replace("ニュートラル", "🌤方向感は拮抗（静かな地合い）")
        )
        return _shorten(text, 230)

    except Exception:
        return _fallback_sentence(
            regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score, mode=mode,
        )