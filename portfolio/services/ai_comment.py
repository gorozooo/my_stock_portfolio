# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import json
import random
import re
from typing import Dict, Any, List, Optional

# Django settings は任意（未インストール環境でも動くように try）
try:
    from django.conf import settings
except Exception:
    class _S:
        AI_COMMENT_MODEL = None
        MEDIA_ROOT = ""
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


# ---------------------------
# 共通ユーティリティ
# ---------------------------
def _shorten(text: str, limit: int = 230) -> str:
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


def _media_root() -> str:
    return getattr(settings, "MEDIA_ROOT", "") or os.getcwd()


# ---------------------------
# パーソナ（ユーザー別スタイル）
# ---------------------------
_DEFAULT_PERSONA: Dict[str, Any] = {
    # 口調・絵文字・長さの嗜好（なければデフォルトで砕けたトーン）
    "tone": "casual",               # "casual" / "neutral"
    "emoji_level": "medium",        # "low" / "medium" / "high"
    "risk_aversion": "balanced",    # "cautious" / "balanced"
    "signature": "",                # 文末に軽い口癖を付けたいときなど
    # 禁則（過度な断定を避ける等）は常に有効
}

def load_persona(user_id: Optional[str]) -> Dict[str, Any]:
    """MEDIA_ROOT/advisor/persona/<user_id>.json を読み、無ければデフォルト。"""
    if not user_id:
        return dict(_DEFAULT_PERSONA)
    base = os.path.join(_media_root(), "advisor", "persona")
    path = os.path.join(base, f"{user_id}.json")
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                p = dict(_DEFAULT_PERSONA)
                p.update({k: v for k, v in data.items() if v is not None})
                return p
    except Exception:
        pass
    return dict(_DEFAULT_PERSONA)


# ---------------------------
# ローカル生成（フォールバック）
# ---------------------------
def _local_fallback_comment(
    *,
    regime: str,
    score: float,
    sectors: List[Dict[str, Any]],
    adopt_rate: float,
    prev_score: Optional[float],
    seed: str = "",
    persona: Optional[Dict[str, Any]] = None,
) -> str:
    """APIが無い/失敗時の砕けた“今日のひとこと”（パーソナ反映軽量）。"""
    persona = persona or _DEFAULT_PERSONA
    rg = (regime or "").upper()
    top_secs = [str(s.get("sector", "")) for s in sectors if s.get("sector")]
    top_txt = "・".join(top_secs[:3]) if top_secs else "（特に目立つセクターなし）"

    rnd = random.Random(f"{seed}|{rg}|{score:.3f}|{adopt_rate:.3f}|{persona.get('tone')}")

    opens_on  = ["📈 地合いまずまず！", "🌞 いい風きてる！", "💪 強めトーン！", "🚀 ノッてきた！"]
    opens_off = ["🌧 ちょい向かい風…", "🧊 冷え気味、慎重に。", "😴 元気薄め。", "🪫 静かな始まり。"]
    opens_neu = ["😐 方向感フラット。", "⚖️ 焦らず様子見。", "🤔 見極めどき。", "😶 静観ムード。"]

    tips_str  = ["📊 押し目拾いもアリ！", "🟢 勝ち筋に素直に！", "🔥 順行でOK！"]
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
        diff = round(score - (prev_score or 0.0), 2)
        if diff > 0.05:
            diff_part = f"📈 昨日より改善(+{diff:.2f}) "
        elif diff < -0.05:
            diff_part = f"📉 昨日より悪化({diff:.2f}) "
        else:
            diff_part = "😐 前日とほぼ横ばい "

    # 採用率でシグナルの一言
    sig_part = "✨ シグナルはまずまず" if adopt_rate >= 0.55 else \
               "🌀 ノイズ気味、慎重に" if adopt_rate <= 0.45 else "🙂 平常運転"

    # 絵文字密度を軽く調整
    if persona.get("emoji_level") == "low":
        op = re.sub(r"[^\w\sぁ-んァ-ヶ一-龠。、！!？?]+", "", op).strip()

    signature = (" " + persona["signature"]) if persona.get("signature") else ""
    out = f"{op} {diff_part}注目👉 {top_txt}。{tip}（{stance}・Score {score:.2f}）{sig_part}{signature}"
    return _shorten(out, 230)


# ---------------------------
# モデル名の決定
# ---------------------------
def _resolve_model_name(cli_or_kw: Optional[str] = None) -> str:
    """
    優先順: 1) 引数 engine, 2) settings.AI_COMMENT_MODEL, 3) env AI_COMMENT_MODEL, 既定 gpt-4-turbo
    """
    if cli_or_kw:
        return cli_or_kw
    model = getattr(settings, "AI_COMMENT_MODEL", None) or os.getenv("AI_COMMENT_MODEL")
    if model:
        return model
    return "gpt-4-turbo"


# ---------------------------
# 公開API
# ---------------------------
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
    user_id: Optional[str] = None,   # ★ユーザー別パーソナ
) -> str:
    """
    “今日のひとこと” を返す。OpenAIが使えなければローカルで生成。
    engine: "gpt-4-turbo"（既定）/ "gpt-4o-mini" / "gpt-5" など
    """
    persona = load_persona(user_id)
    # OpenAIを使える条件
    use_api = _OPENAI_AVAILABLE and bool(os.getenv("OPENAI_API_KEY"))
    model = _resolve_model_name(engine)

    # APIなし → ローカル生成
    if not use_api:
        return _local_fallback_comment(
            regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score,
            seed=seed, persona=persona,
        )

    # --------- OpenAIで生成 ----------
    # 構造化された事実を渡す
    top_secs = [str(s.get("sector", "")) for s in sectors if s.get("sector")][:5]
    facts = (
        f"Regime={regime}, Score={score:.3f}, "
        f"AdoptRate={adopt_rate:.3f}, PrevScore={'' if prev_score is None else f'{prev_score:.3f}'}, "
        f"TopSectors={', '.join(top_secs) if top_secs else 'なし'}"
    )

    # パーソナをプロンプトへ反映
    tone = persona.get("tone", "casual")
    emoji = persona.get("emoji_level", "medium")
    signature = persona.get("signature", "")

    sys = (
        "あなたは日本語の投資アシスタント。\n"
        "- 砕けた口調（ただし煽らない）で、短く（2文以内・最大230文字）。\n"
        "- 絵文字は persona に合わせて使う（low/medium/high）。\n"
        "- 前日比コメント（あれば）と注目セクターを織り交ぜる。\n"
        "- 過度な断定は避け、読みやすい一段落にまとめる。\n"
        "- 文末に signature があれば自然に添える（任意）。"
    )
    user = (
        f"[facts]\n{facts}\n\n"
        f"[persona]\n"
        f"- tone={tone}\n- emoji={emoji}\n- signature={signature}\n\n"
        f"[rules]\n- 箇条書き禁止・改行禁止・一段落のみ\n- 語尾は自然体\n"
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
            text = (resp.choices[0].message.content or "").strip()
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
            text = (resp["choices"][0]["message"]["content"] or "").strip()  # type: ignore
        return _shorten(text, 230)
    except Exception:
        # 失敗時はローカルにフォールバック
        return _local_fallback_comment(
            regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score,
            seed=seed, persona=persona,
        )