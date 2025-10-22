# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import re
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
load_dotenv("/home/gorozooo/my_stock_portfolio/.env")

# Django settings は任意（未インストール環境でも動くように try）
try:
    from django.conf import settings
except Exception:
    class _S:
        AI_COMMENT_MODEL = None
    settings = _S()  # type: ignore

# OpenAI SDK は任意依存（>=1系優先、なければ旧SDK）
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
    """
    1) 引数 > 2) settings.AI_COMMENT_MODEL > 3) env AI_COMMENT_MODEL。
    既定は **gpt-4-turbo**（固定）。
    """
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


# ----------------- 人格プロンプト -----------------
def _persona_text(persona: str) -> str:
    p = (persona or "gorozooo").lower()
    if p == "gorozooo":
        return (
            "あなたは『gorozooo』という、アナリスト×ディーラー×評論家のハイブリッド人格。"
            "数字に基づく精度と、ディーラーの瞬発的な判断、評論家の洞察を兼ね備える。"
            "専門用語よりも肌感・リズム・直感で伝える。"
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


# ----------------- GPT用 System Prompt -----------------
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
    """
    “今日のひとこと” を返す（モード別）。
    原則：OPENAI_API_KEY があれば **gpt-4-turbo** 等を使う。無ければローカル文生成。
    """
    # ✅ APIキーがあれば自動でGPT、無ければローカル
    use_api = _OPENAI_AVAILABLE and bool(os.getenv("OPENAI_API_KEY"))
    model = _resolve_model_name(engine)

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
        "次の事実をもとに、今日の地合いを短く人間らしく要約してください。"
        "テンションや雰囲気も含め、相場を肌感覚で伝えるように。\n"
        f"- 事実: {facts}"
    )

    try:
        if OpenAI:
            client = OpenAI()
            resp = client.chat.completions.create(
                model=model,  # 既定は gpt-4-turbo
                messages=[{"role": "system", "content": system_msg},
                          {"role": "user", "content": user_msg}],
                temperature=float(temperature),
                max_tokens=int(max_tokens),
                seed=hash(seed) % (2**31 - 1) if seed else None,  # 再現性の軽確保（任意）
            )
            text = resp.choices[0].message.content.strip()
        else:
            import openai  # type: ignore
            openai.api_key = os.getenv("OPENAI_API_KEY")
            resp = openai.ChatCompletion.create(  # type: ignore
                model=model,  # 既定は gpt-4-turbo
                messages=[{"role": "system", "content": system_msg},
                          {"role": "user", "content": user_msg}],
                temperature=float(temperature),
                max_tokens=int(max_tokens),
            )
            text = resp["choices"][0]["message"]["content"].strip()  # type: ignore

        # 専門語が出た場合の補正（視覚的に直感化）
        text = (
            text.replace("リスクオン", "🔥買いが優勢（強気ムード）")
                .replace("リスクオフ", "🌧売りが優勢（慎重ムード）")
                .replace("ニュートラル", "🌤方向感は拮抗（静かな地合い）")
        )
        return _shorten(text, 230)

    except Exception:
        # 失敗時はローカルにフォールバック
        return _fallback_sentence(
            regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score, mode=mode,
        )