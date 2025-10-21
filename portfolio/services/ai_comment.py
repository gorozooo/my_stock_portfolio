# -*- coding: utf-8 -*-
from __future__ import annotations
import os, re
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


# ---------- 共通ユーティリティ ----------
def _shorten(text: str, limit: int = 230) -> str:
    if not text: return ""
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


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
    return getattr(settings, "AI_COMMENT_MODEL", None) or os.getenv("AI_COMMENT_MODEL") or "gpt-4-turbo"


def _humanize_regime(rg: str) -> tuple[str, str]:
    """トーン絵文字＋背景色"""
    rg = (rg or "").upper()
    if "ON" in rg:
        return "🔥", "#fff3e0"  # 強気:淡いオレンジ
    elif "OFF" in rg:
        return "🌧", "#e0f2fe"  # 慎重:淡いブルー
    return "🌤", "#f3f4f6"      # 様子見:グレー


# ---------- gorozooo人格 ----------
def _persona_text(persona: str) -> str:
    p = (persona or "gorozooo").lower()
    if p == "gorozooo":
        return (
            "あなたは『gorozooo』という、アナリスト×ディーラー×評論家のハイブリッド人格。"
            "数字に基づく精度と、ディーラーの瞬発的な判断、評論家の洞察を兼ね備える。"
            "相場の“温度感”をフレンドリーに、肌感覚で語る。"
            "絵文字で温度を伝え、短く鋭く余韻のある文体を好む。"
        )
    return "アナリスト×ディーラー×評論家のハイブリッドとして自然に話す。"


# ---------- System Prompt ----------
def _system_prompt_for(mode: str, persona: str) -> str:
    persona_block = _persona_text(persona)
    base_rules = (
        "出力は日本語、2文以内・一段落。"
        "絵文字(1〜3個)で温度感を伝える。"
        "『リスクオン/オフ』は禁止。"
        "代わりに『買いが優勢』『売りが優勢』『拮抗』を使う。"
        "必ず含める: 需給スタンス（買い/売り/拮抗）・期待度（★）・注目セクター1〜3個。"
    )
    focus_map = {
        "preopen":  "寄り付き前の温度感。初手の雰囲気を端的に。",
        "postopen": "寄り直後の地合い。勢いと押し引きを簡潔に。",
        "noon":     "前場の総括＋後場のムードを自然に。",
        "afternoon":"後場の雰囲気と引けの温度を短く。",
        "outlook":  "引け後の総括＋翌営業日の展望を自然に2文でまとめる。",
    }
    focus = focus_map.get((mode or "").lower(), "全体の地合いを短くまとめる。")
    return f"{persona_block} {base_rules} {focus}"


# ---------- フォールバック ----------
def _fallback_sentence(*, regime, score, sectors, mode) -> str:
    tone, _ = _humanize_regime(regime)
    stance = _stance_from_score(score)
    heat = _stars_from_score(score)
    top_secs = [s.get("sector") for s in sectors if s.get("sector")][:3]
    top_txt = "・".join(top_secs) if top_secs else "特筆なし"
    return _shorten(f"{tone}全体は{stance}（期待度{heat}）。注目は{top_txt}。")


# ---------- メイン ----------
def make_ai_comment(
    *,
    regime: str,
    score: float,
    sectors: List[Dict[str, Any]],
    adopt_rate: float,
    prev_score: Optional[float] = None,
    seed: str = "",
    engine: Optional[str] = None,
    temperature: float = 0.8,
    max_tokens: int = 180,
    mode: str = "preopen",
    persona: str = "gorozooo",
) -> str:
    """gorozoooハイブリッド人格で生成"""
    use_api = _OPENAI_AVAILABLE and bool(os.getenv("OPENAI_API_KEY"))
    model = _resolve_model_name(engine)

    if not use_api:
        return _fallback_sentence(regime=regime, score=score, sectors=sectors, mode=mode)

    top_secs = [s.get("sector") for s in sectors if s.get("sector")][:3]
    facts = (
        f"Regime={regime}, Score={score:.3f}, AdoptRate={adopt_rate:.3f}, "
        f"PrevScore={'' if prev_score is None else f'{prev_score:.3f}'}, "
        f"TopSectors={', '.join(top_secs) if top_secs else 'なし'}"
    )

    system_msg = _system_prompt_for(mode, persona)
    user_msg = f"次の事実をもとに、市場の温度感と雰囲気を短く伝えてください。\n- 事実: {facts}"

    try:
        if OpenAI:
            client = OpenAI()
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
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
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
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
        return _fallback_sentence(regime=regime, score=score, sectors=sectors, mode=mode)