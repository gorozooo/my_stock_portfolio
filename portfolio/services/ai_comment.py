# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any, List, Optional
import os, re, random

# Django settings は任意（未インストール環境でも動くように try）
try:
    from django.conf import settings
except Exception:
    class _S:  # fallback
        AI_COMMENT_MODEL = None
    settings = _S()  # type: ignore

# OpenAI SDK（任意依存・無ければローカルで生成）
_OPENAI_AVAILABLE = False
try:
    from openai import OpenAI  # type: ignore
    _OPENAI_AVAILABLE = True
except Exception:
    try:
        import openai  # type: ignore
        OpenAI = None  # type: ignore
        _OPENAI_AVAILABLE = True
    except Exception:
        _OPENAI_AVAILABLE = False


def _shorten(text: str, limit: int = 230) -> str:
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
    if cli_or_kw:
        return cli_or_kw
    return getattr(settings, "AI_COMMENT_MODEL", None) or os.getenv("AI_COMMENT_MODEL") or "gpt-4-turbo"


def _local_fallback_comment(
    *,
    persona: str,
    mode: str,
    regime: str,
    score: float,
    sectors: List[Dict[str, Any]],
    adopt_rate: float,
    prev_score: Optional[float],
    snapshot: Optional[Dict[str, Any]],
    seed: str = "",
) -> str:
    """APIが無い時のローカル生成（砕けたトーン＋絵文字＋前日比＋スナップショット要約）。"""
    rg = (regime or "NEUTRAL").upper()
    rnd = random.Random(f"{seed}|{mode}|{rg}|{score:.3f}|{adopt_rate:.3f}")
    top_secs = [str(s.get("sector", "")) for s in (sectors or []) if s.get("sector")]
    top_txt = "・".join(top_secs[:3]) if top_secs else "（目立つセクターなし）"

    # スナップショット軽要約
    def sx(s: Optional[Dict[str, Any]]) -> str:
        if not s:
            return ""
        fx = s.get("fx", {}).get("USDJPY", {}).get("spot")
        vix = s.get("vol", {}).get("VIX", {}).get("last")
        nk  = s.get("futures", {}).get("NK225", {}).get("pct_d")
        spx = s.get("futures", {}).get("SPX", {}).get("pct_d")
        out = []
        if nk is not None:  out.append(f"先物{nk:+.2f}%")
        if spx is not None: out.append(f"米先{spx:+.2f}%")
        if vix is not None: out.append(f"VIX{vix:.1f}")
        if fx is not None:  out.append(f"ドル円{fx:.2f}")
        return " / ".join(out)

    # トーン
    opens_on  = ["📈 いい風！", "🚀 上方向の気配。", "💪 強めスタート。"]
    opens_off = ["🌧 向かい風。", "🧊 弱含み。", "🪫 リスクオフ寄り。"]
    opens_neu = ["😐 中立寄り。", "⚖️ 様子見優勢。", "🤔 方向感まだ。"]
    tips_str  = ["🟢 流れに順行で。", "📊 押し目拾いも。", "✨ 強い所に素直に。"]
    tips_mid  = ["🧩 小ロットで。", "😌 ポジ控えめ。", "🌤 焦り禁物。"]
    tips_weak = ["🛡 守り重視。", "💤 現金厚め。", "🥶 逆張り抑制。"]

    if "OFF" in rg:
        op = rnd.choice(opens_off); tip = rnd.choice(tips_weak); stance = "弱気寄り"
    elif "ON" in rg:
        op = rnd.choice(opens_on); tip = rnd.choice(tips_str if score >= 0.6 else tips_mid); stance = "強気～やや強気"
    else:
        op = rnd.choice(opens_neu); tip = rnd.choice(tips_mid); stance = "中立"

    diff = ""
    if prev_score is not None:
        d = round(score - prev_score, 2)
        if d > 0.05:  diff = f"📈 昨日より改善(+{d:.2f}) "
        elif d < -0.05: diff = f"📉 昨日より悪化({d:.2f}) "
        else: diff = "😐 前日比は横ばい "

    sig = "✨ シグナルまずまず" if adopt_rate >= 0.55 else \
          "🌀 ノイズ気味" if adopt_rate <= 0.45 else "🙂 平常運転"

    snap = sx(snapshot)
    # モード名を軽く
    mode_tag = dict(
        preopen="寄り前",
        postopen="寄り直後",
        noon="前場総括",
        afternoon="後場",
        outlook="明日展望",
    ).get(mode, "市況")

    txt = f"{op} {diff}{f'[{snap}] ' if snap else ''}注目👉 {top_txt}。{tip}（{stance}・Score {score:.2f}）{sig}"
    return _shorten(txt, 230)


def make_ai_comment(
    *,
    mode: str,                    # "preopen" / "postopen" / "noon" / "afternoon" / "outlook"
    persona: str,                 # ディーラー視点など
    regime: str,
    score: float,
    sectors: List[Dict[str, Any]],
    adopt_rate: float,
    prev_score: Optional[float] = None,
    snapshot: Optional[Dict[str, Any]] = None,  # 直近スナップショットJSON
    seed: str = "",
    engine: Optional[str] = None,
    temperature: float = 0.6,
    max_tokens: int = 180,
) -> str:
    """
    “今日のひとこと” を返す。
    snapshot が渡されると、寄り前/引け前などの温度感に反映。
    engine: "gpt-4-turbo"(既定)/"gpt-5"/"gpt-4o-mini" 等
    """
    # API利用可否
    use_api = _OPENAI_AVAILABLE and bool(os.getenv("OPENAI_API_KEY"))
    model = _resolve_model_name(engine)

    if not use_api:
        return _local_fallback_comment(
            persona=persona, mode=mode, regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score, snapshot=snapshot, seed=seed
        )

    # --- OpenAIで生成 ---
    top_secs = [str(s.get("sector", "")) for s in (sectors or []) if s.get("sector")][:5]
    facts = {
        "mode": mode,
        "regime": regime, "score": round(float(score), 3),
        "adopt_rate": round(float(adopt_rate), 3),
        "prev_score": None if prev_score is None else round(float(prev_score), 3),
        "top_sectors": top_secs or [],
        "snapshot": snapshot or {},
    }

    # モード別の強調点
    mode_hint = {
        "preopen":   "先物・VIX・為替（USDJPY）を主に、寄り気配・ギャップ方向の温度感を短文で。",
        "postopen":  "寄り付き後の主導セクター継続/交代感、寄りの偏り。具体名は極力控えめで短文。",
        "noon":      "前場の総括と後場への地合い見通しを一言で。過度な断定禁止。",
        "afternoon": "後場のフロー/需給の偏り匂いを短文で。数字の羅列は避ける。",
        "outlook":   "翌日への含みを一言で。イベントやドル円/VIXの位置感を軽く示唆。",
    }.get(mode, "短く温度感のみ。")

    system = (
        f"あなたは日本のベテラン株式ディーラー。{persona} "
        "出力は日本語・砕けた口調・人間味・最大2文・絵文字適度。"
        "前日比コメント（あれば）と、注目セクター/リスクオンオフを短く示す。"
        "断定/煽り/助言は禁止。箇条書き・改行は使わず一段落で。"
    )
    user = (
        "次の事実を基に『寄付き/場中/引け後』いずれかのタイミングでの温度感コメントを1段落で返してください。\n"
        f"- モード要件: {mode_hint}\n"
        f"- 事実JSON: {facts}\n"
        "- 必須: 砕けた/短い/絵文字適度/断定回避/数値羅列しない/寄りの温度感を一言で"
    )

    try:
        if OpenAI:
            client = OpenAI()
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=float(temperature),
                max_tokens=int(max_tokens),
            )
            text = resp.choices[0].message.content.strip()
        else:
            import openai  # type: ignore
            openai.api_key = os.getenv("OPENAI_API_KEY")
            resp = openai.ChatCompletion.create(  # type: ignore
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=float(temperature),
                max_tokens=int(max_tokens),
            )
            text = resp["choices"][0]["message"]["content"].strip()  # type: ignore
        return _shorten(text, 230)
    except Exception:
        return _local_fallback_comment(
            persona=persona, mode=mode, regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score, snapshot=snapshot, seed=seed
        )