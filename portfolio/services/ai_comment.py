# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import random
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
    # 新クライアント（openai>=1.x）
    from openai import OpenAI  # type: ignore
    _OPENAI_AVAILABLE = True
except Exception:
    try:
        # 旧SDK互換
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
    既定は gpt-4-turbo。
    """
    if cli_or_kw:
        return cli_or_kw
    model = getattr(settings, "AI_COMMENT_MODEL", None) or os.getenv("AI_COMMENT_MODEL")
    return model or "gpt-4-turbo"


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
    rg = (regime or "").upper()
    top_secs = [str(s.get("sector", "")) for s in sectors if s.get("sector")]
    top_txt = "・".join(top_secs[:3]) if top_secs else "特筆なし"

    stance = _stance_from_score(float(score))
    heat = _stars_from_score(float(score))
    # 前日差
    diff_part = ""
    if prev_score is not None:
        diff = round(float(score) - float(prev_score), 2)
        if diff > 0.05:
            diff_part = f"📈 前日比改善(+{diff:.2f}) "
        elif diff < -0.05:
            diff_part = f"📉 前日比悪化({diff:.2f}) "
        else:
            diff_part = "😐 前日比ほぼ横ばい "

    # モード別の語尾・文脈
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
    # リスクトーン（RISK_ON/OFF を軽く表現）
    tone = "リスクオン気味" if "ON" in rg else "リスクオフ気味" if "OFF" in rg else "ニュートラル"

    txt = (
        f"{diff_part}{tone}。温度感は「{stance}」（期待度{heat}）。"
        f" 注目は{top_txt}。{tail} {note}"
    )
    return _shorten(txt, 230)


# ----------------- モード別 System Prompt -----------------
def _system_prompt_for(mode: str, persona: str) -> str:
    """
    億トレーダー兼経済評論家の人格で、時間帯に応じた観点を強調。
    """
    base_persona = (
        "あなたは日本の『億トレーダー兼経済評論家』。"
        "プロ視点で短く本質だけを示し、需給スタンス（買い/売り/拮抗）と期待度をはっきり伝える。"
        "断定は一部OKだが煽らない。専門用語の羅列は禁止。"
        "出力は日本語、2文以内・一段落・適度な絵文字。"
    )

    m = (mode or "").lower()
    if m == "preopen":
        focus = (
            "寄り付き前の温度感を要約。先物/為替/ボラの影響を含意しつつ、"
            "『今日は買い寄り/売り寄り/拮抗』が一目で分かる表現に。"
            "強すぎる煽りは避け、短い方針に触れる。"
        )
    elif m == "postopen":
        focus = (
            "寄り直後の地合い。寄り成りの手口や初動の強弱を短く評価。"
            "継続/反転の可能性を1フレーズで示す。"
        )
    elif m == "noon":
        focus = (
            "前場の総括と、後場に向けた温度感。前場の勝ち筋/負け筋を一言、"
            "後場は『続伸狙い/押し目待ち/様子見』などの方針提示を短く。"
        )
    elif m == "afternoon":
        focus = (
            "後場のムードと引けの手口の匂いを要約。手仕舞い/追随/見送りの温度感を示す。"
        )
    elif m == "outlook":
        focus = (
            "引け後の総括と、翌営業日に向けた展望。『明日は買い寄り/売り寄り/拮抗』の仮説を一言で。"
            "過度に長期の断定は避け、短い期待/警戒ポイントを添える。"
        )
    else:
        focus = (
            "市場の温度感を要約。需給スタンスと期待度を一言で伝え、短い運用方針を示す。"
        )

    style_rules = (
        "必ず含める: 需給スタンス（買い/売り/拮抗）・期待度（★で簡潔に）、"
        "注目セクターを1〜3個。"
        "禁止: 箇条書き・改行・長文・冗長な免責。"
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
    """
    “今日のひとこと” を返す（モード別）。OpenAI不可ならローカル生成。
    - mode: preopen / postopen / noon / afternoon / outlook
    - persona は今後拡張用（現状は固定で億トレ×評論家トーン）
    """
    use_api = _OPENAI_AVAILABLE and bool(os.getenv("OPENAI_API_KEY"))
    model = _resolve_model_name(engine)

    # OpenAI不可 → ローカル生成
    if not use_api:
        return _fallback_sentence(
            regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score, mode=mode,
        )

    # 事実テーブル（コンパクト）
    top_secs = [str(s.get("sector", "")) for s in sectors if s.get("sector")][:3]
    facts = (
        f"Regime={regime}, Score={score:.3f}, "
        f"AdoptRate={adopt_rate:.3f}, PrevScore={'' if prev_score is None else f'{prev_score:.3f}'}, "
        f"TopSectors={', '.join(top_secs) if top_secs else 'なし'}"
    )

    system_msg = _system_prompt_for(mode, persona)
    user_msg = (
        "次の事実を基に、2文以内で“一段落のみ”の短いコメントを作ってください。"
        "需給スタンス（買い/売り/拮抗）と期待度（★で表現）を必ず明記し、"
        "注目セクターも1〜3個触れてください。絵文字は控えめに1〜3個。\n"
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
                seed=hash(seed) % (2**31 - 1) if seed else None,  # 再現性の軽確保（OpenAIオプション）
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
        return _shorten(text, 230)
    except Exception:
        # 失敗時はローカルにフォールバック
        return _fallback_sentence(
            regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score, mode=mode,
        )