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
        # 互換レイヤ（旧版）
        import openai  # type: ignore
        _OPENAI_AVAILABLE = True
        OpenAI = None  # type: ignore
    except Exception:
        _OPENAI_AVAILABLE = False


def _shorten(text: str, limit: int = 220) -> str:
    """行を1〜2行・短文に整える。過剰な空白を畳み、末尾を整える。"""
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text).strip()
    if len(t) <= limit:
        return t
    t = t[: limit - 1].rstrip()
    if not t.endswith(("。", "！", "!", "？", "?")):
        t += "…"
    return t


def _local_fallback_comment(
    *,
    regime: str,
    score: float,
    sectors: List[Dict[str, Any]],
    adopt_rate: float,
    prev_score: Optional[float],
    seed: str = "",
) -> str:
    """APIが無い時のローカル生成（砕けたトーン＋絵文字＋前日比）。"""
    rg = (regime or "").upper()
    top_secs = [str(s.get("sector", "")) for s in sectors if s.get("sector")]
    top_txt = "・".join(top_secs[:3]) if top_secs else "（特に目立つセクターなし）"

    rnd = random.Random(f"{seed}|{rg}|{score:.3f}|{adopt_rate:.3f}")

    opens_on  = ["📈 地合いまずまず！", "🌞 いい風きてる！", "💪 強めのトーン！", "🚀 ノッてきた！"]
    opens_off = ["🌧 ちょい向かい風…", "🧊 冷え気味。慎重に。", "😴 元気薄め。", "🪫 静かな始まり。"]
    opens_neu = ["😐 方向感はフラット。", "⚖️ 判断は落ち着いて。", "🤔 様子見優勢。", "😶 まだ静観ムード。"]

    tips_str  = ["📊 押し目拾いもアリ！", "🟢 勝ち筋に素直に！", "🔥 トレンド順行で！"]
    tips_mid  = ["🧩 小ロットで様子見。", "🌤 早焦りは禁物。", "😌 分散寄りで。"]
    tips_weak = ["🛡 守り重視で。", "💤 現金厚めもOK。", "🥶 逆張りは控えめに。"]

    if "OFF" in rg:
        op = rnd.choice(opens_off); tip = rnd.choice(tips_weak); stance = "弱気寄り"
    elif "ON" in rg:
        op = rnd.choice(opens_on)
        tip = rnd.choice(tips_str if score >= 0.6 else tips_mid)
        stance = "強気" if score >= 0.6 else "やや強気"
    else:
        op = rnd.choice(opens_neu); tip = rnd.choice(tips_mid); stance = "中立"

    # 前日差コメント
    diff_part = ""
    if prev_score is not None:
        diff = round(score - prev_score, 2)
        if diff > 0.05:
            diff_part = f"📈 昨日より改善(+{diff:.2f}) "
        elif diff < -0.05:
            diff_part = f"📉 昨日より悪化({diff:.2f}) "
        else:
            diff_part = "😐 前日とほぼ横ばい "

    sig_part = "✨ シグナルはまずまず" if adopt_rate >= 0.55 else \
               "🌀 ノイズ気味。慎重に" if adopt_rate <= 0.45 else "🙂 平常運転"

    out = f"{op} {diff_part}注目👉 {top_txt}。{tip}（{stance}・Score {score:.2f}）{sig_part}"
    return _shorten(out, 230)


def _resolve_model_name(cli_or_kw: Optional[str] = None) -> str:
    """
    1) 引数、2) settings.AI_COMMENT_MODEL、3) env AI_COMMENT_MODEL の優先順位。
    既定は gpt-4-turbo。gpt-5 に切替可。
    """
    if cli_or_kw:
        return cli_or_kw
    model = getattr(settings, "AI_COMMENT_MODEL", None) or os.getenv("AI_COMMENT_MODEL")
    if model:
        return model
    return "gpt-4-turbo"  # 既定


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
) -> str:
    """
    “今日のひとこと” を返す。OpenAIが使えなければローカルで生成。
    engine: "gpt-4-turbo" (既定) / "gpt-5"
    """
    # OpenAIを使える条件
    use_api = _OPENAI_AVAILABLE and bool(os.getenv("OPENAI_API_KEY"))
    model = _resolve_model_name(engine)

    # APIなし → ローカル生成
    if not use_api:
        return _local_fallback_comment(
            regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score, seed=seed,
        )

    # --------- OpenAIで生成 ----------
    # 構造化された事実を渡す
    top_secs = [str(s.get("sector", "")) for s in sectors if s.get("sector")][:5]
    facts = (
        f"Regime={regime}, Score={score:.3f}, "
        f"AdoptRate={adopt_rate:.3f}, PrevScore={'' if prev_score is None else f'{prev_score:.3f}'}, "
        f"TopSectors={', '.join(top_secs) if top_secs else 'なし'}"
    )

    sys = (
        "あなたは日本語の投資アシスタント。"
        "砕けた口調で、短く（2文以内・最大230文字）、絵文字を適度に使って、"
        "前日比コメント（あれば）と注目セクターを織り交ぜ、過度な断定や助言は避け、"
        "読みやすい一段落にまとめてください。"
    )
    user = (
        f"以下の状況を要約して『今日のひとこと』を書いてください。\n"
        f"- 事実: {facts}\n"
        f"- 必須: 砕けた/人間っぽい/短く/適度な絵文字/煽らない/具体語を少し\n"
        f"- 出力は一段落のみ（箇条書きや改行なし）"
    )

    try:
        if OpenAI:
            client = OpenAI()
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": sys},
                          {"role": "user", "content": user}],
                temperature=float(temperature),
                max_tokens=int(max_tokens),
            )
            text = resp.choices[0].message.content.strip()
        else:
            # 旧 openai ライブラリ互換
            import openai  # type: ignore
            openai.api_key = os.getenv("OPENAI_API_KEY")
            resp = openai.ChatCompletion.create(  # type: ignore
                model=model,
                messages=[{"role": "system", "content": sys},
                          {"role": "user", "content": user}],
                temperature=float(temperature),
                max_tokens=int(max_tokens),
            )
            text = resp["choices"][0]["message"]["content"].strip()  # type: ignore
        return _shorten(text, 230)
    except Exception:
        # 失敗時はローカルにフォールバック
        return _local_fallback_comment(
            regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score, seed=seed,
        )