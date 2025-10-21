# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import re
from typing import Dict, Any, List, Optional

# Django settings は任意（未インストールでも動作）
try:
    from django.conf import settings
except Exception:
    class _S:
        AI_COMMENT_MODEL = None
    settings = _S()  # type: ignore

# OpenAI SDK（新旧両対応）
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


# ----------------- 文字列ユーティリティ -----------------
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


def _resolve_model_name(cli_or_kw: Optional[str] = None) -> str:
    """1) 引数 > 2) settings.AI_COMMENT_MODEL > 3) env AI_COMMENT_MODEL（既定 gpt-4-turbo）"""
    if cli_or_kw:
        return cli_or_kw
    model = getattr(settings, "AI_COMMENT_MODEL", None) or os.getenv("AI_COMMENT_MODEL")
    return model or "gpt-4-turbo"


# ----------------- トーン＆絵文字 -----------------
def _tone_emoji_from_regime(regime: str) -> str:
    """regime文字列からトーン絵文字を推定"""
    rg = (regime or "").upper()
    if "ON" in rg:
        return "🔥"  # 強気
    if "OFF" in rg:
        return "🌧"  # 慎重
    return "🌤"      # 様子見


def _ensure_emoji(text: str, regime: str) -> str:
    """
    GPTが絵文字を出さなかった時の最低限の補完。
    先頭にトーン絵文字を1個だけ付与（重複は避ける）。
    """
    if not text:
        return text
    if any(e in text for e in ("🔥", "🌧", "🌤", "📈", "📉", "✨", "🙂", "🤔", "👀")):
        return text
    return f"{_tone_emoji_from_regime(regime)} {text}"


def _post_fix_terms(text: str) -> str:
    """
    リスクオン/オフ→人間語に置換。ニュートラル→拮抗。
    """
    return (
        text.replace("リスクオン", "買いが優勢（強気）")
            .replace("リスクオフ", "売りが優勢（慎重）")
            .replace("ニュートラル", "拮抗（様子見）")
    )


# ----------------- 最小フォールバック -----------------
def _fallback_sentence(*, regime: str, score: float, sectors: List[Dict[str, Any]], mode: str) -> str:
    """
    API不通時だけ使う超簡易文（短い・1段落）。GPT版が基本運用。
    """
    emoji = _tone_emoji_from_regime(regime)
    if "ON" in (regime or "").upper():
        tone = "買いが優勢"
    elif "OFF" in (regime or "").upper():
        tone = "売りが優勢"
    else:
        tone = "拮抗"
    secs = [s.get("sector") for s in sectors if s.get("sector")]
    sec_txt = "・".join(secs[:3]) if secs else "特筆なし"
    base = f"{emoji} いまの地合いは「{tone}」。注目は{sec_txt}。"
    m = (mode or "").lower()
    tail = {
        "preopen": "寄り前は先物と板気配を確認して丁寧に入るイメージ。",
        "postopen": "寄り直後はプライスアクション優先で無理はしない。",
        "noon": "後場は出来高の伸びに乗る/待つの見極めが鍵。",
        "afternoon": "引けにかけては手仕舞いと押し目待ちが交錯、焦らず。",
        "outlook": "明日は寄りのトーンを再確認しつつ、流れに素直に。"
    }.get(m, "")
    return _shorten(f"{base} {tail}", 230)


# ----------------- System Prompt（全モードGPT生成） -----------------
def _system_prompt_for(mode: str, persona: str) -> str:
    """
    明日への展望を含む全モードをGPTに書かせる設定。
    人格: 億トレーダー兼経済評論家（短く核心/フレンドリー/少量の絵文字）。
    """
    base = (
        "あなたは日本の『億トレーダー兼経済評論家』。"
        "プロ視点で核心を短く、フレンドリーに伝える。"
        "用語は噛み砕き、“買いが優勢/売りが優勢/拮抗”で需給を表現。"
        "出力は日本語・1段落・最大2文・過度な断定や免責の羅列は禁止。"
        "絵文字は1〜3個、テンポよく自然に入れる。"
    )

    m = (mode or "").lower()
    focus = {
        "preopen":
            "寄り付き前の温度感。『今日は買い寄り/売り寄り/様子見』が一目で分かる表現に。"
            "先物・為替・ボラの影響を含意し、短い作戦を一言。",
        "postopen":
            "寄り直後の地合い。初動の強弱と、続伸/反転どちら寄りかを一言で。",
        "noon":
            "前場の総括＋後場に向けた温度感。『続伸狙い/押し目待ち/様子見』のどれかを示す。",
        "afternoon":
            "後場の温度感。引けに向けた手仕舞い/追随/見送りの判断軸を一言。",
        "outlook":
            "引け後の総括＋翌営業日の展望を2文で。"
            "1文目=『きょうの総括（上向き/弱含み/横ばい）＋主役セクター』、"
            "2文目=『明日の仮説（買い/売り/拮抗＋★期待度）＋寄り前の注意点』を含める。"
    }.get(m, "全体の温度感を要約し、短い作戦を一言。")

    must = (
        "必ず含める: 需給スタンス（買いが優勢/売りが優勢/拮抗）、"
        "期待度（★〜★★★を1回だけ）、注目セクター1〜3個。"
        "禁止: 箇条書き・改行・専門用語の羅列。"
    )
    return f"{base} {focus} {must}"


# ----------------- GPTコメント生成 -----------------
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
    """
    全モードをGPTで生成（preopen/postopen/noon/afternoon/outlook すべて）。
    - regime/score/sectors/adopt_rate/prev_score だけを“事実”として渡し、2文の短文を要求
    - 返答にリスクオン/オフ等が混じったら人間語に置換
    - 絵文字が薄いときはトーン絵文字を補完
    """
    model = _resolve_model_name(engine)
    use_api = _OPENAI_AVAILABLE and bool(os.getenv("OPENAI_API_KEY"))

    # 事実テーブル（コンパクト）
    top_secs = [str(s.get("sector", "")) for s in sectors if s.get("sector")][:3]
    facts = (
        f"Regime={regime}, Score={score:.3f}, AdoptRate={adopt_rate:.3f}, "
        f"PrevScore={'' if prev_score is None else f'{prev_score:.3f}'}, "
        f"TopSectors={', '.join(top_secs) if top_secs else 'なし'}"
    )

    if not use_api:
        # フォールバック（最小限）
        return _fallback_sentence(regime=regime, score=score, sectors=sectors, mode=mode)

    system_msg = _system_prompt_for(mode, persona)
    user_msg = (
        "次の事実をもとに、1段落・最大2文で相場の温度感と短い作戦を出してください。"
        "『買いが優勢/売りが優勢/拮抗』の語を必ず使い、期待度は★で一度だけ示す。\n"
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

        text = _post_fix_terms(text)             # リスクオン/オフ→人間語
        text = _ensure_emoji(text, regime)       # 絵文字が薄い場合に補完
        return _shorten(text, 230)

    except Exception:
        # 失敗時フォールバック
        return _fallback_sentence(regime=regime, score=score, sectors=sectors, mode=mode)