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


def _shorten(text: str, limit: int = 230) -> str:
    """1段落・最大limit文字程度に整形。末尾調整。"""
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
    """
    優先順位: 1) 引数 engine, 2) settings.AI_COMMENT_MODEL, 3) env AI_COMMENT_MODEL
    既定は gpt-4-turbo（gpt-5 などにも切替可）
    """
    if cli_or_kw:
        return cli_or_kw
    model = getattr(settings, "AI_COMMENT_MODEL", None) or os.getenv("AI_COMMENT_MODEL")
    if model:
        return model
    return "gpt-4-turbo"


def _local_fallback_comment(
    *,
    regime: str,
    score: float,
    sectors: List[Dict[str, Any]],
    adopt_rate: float,
    prev_score: Optional[float],
    phase: Optional[str],
    seed: str = "",
) -> str:
    """APIが無い時のローカル生成（砕けたトーン＋絵文字＋前日比＋時間帯味付け）。"""
    rg = (regime or "").upper()
    top_secs = [str(s.get("sector", "")) for s in sectors if s.get("sector")]
    top_txt = "・".join(top_secs[:3]) if top_secs else "（特に目立つセクターなし）"

    rnd = random.Random(f"{seed}|{rg}|{score:.3f}|{adopt_rate:.3f}|{phase or ''}")

    # 時間帯ごとのニュアンス
    prefix = {
        "preopen":   "⏰寄り前の空気感は、",
        "postopen":  "🛎️寄り付き直後、",
        "noon":      "🍱前場の総括：",
        "afternoon": "⛳後場は、",
        "outlook":   "🔭明日への見立て：",
    }.get((phase or "").lower(), "")

    opens_on  = ["📈 雰囲気は悪くない！", "🌞 追い風が吹いてる！", "💪 リスクセンチメントは強め！", "🚀 上目線に傾きつつある！"]
    opens_off = ["🌧 逆風寄り…", "🧊 リスク回避が優勢。", "😴 トーンは弱め。", "🪫 慎重姿勢が無難。"]
    opens_neu = ["😐 様子見ムード。", "⚖️ 方向感はまだフラット。", "🤔 判断は急がず。", "😶 静かな立ち上がり。"]

    tips_str  = ["📊 押し目拾いも検討。", "🟢 強い所に素直に。", "🔥 トレンド順行で。"]
    tips_mid  = ["🧩 小ロットで様子見。", "🌤 慎重にポジ調整。", "😌 分散と時間分散で。"]
    tips_weak = ["🛡 守り重視で。", "💤 キャッシュ厚めもあり。", "🥶 逆張りは控えめ。"]

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
            diff_part = "😐 ほぼ横ばい "

    sig_part = "✨ シグナルは無難" if adopt_rate >= 0.55 else \
               "🌀 ノイズ気味で慎重に" if adopt_rate <= 0.45 else "🙂 平常運転"

    pieces = [
        prefix or "",
        op,
        diff_part,
        f"注目👉 {top_txt}。",
        f"{tip}（{stance}・Score {score:.2f}）",
        sig_part
    ]
    out = " ".join([p for p in pieces if p]).strip()
    return _shorten(out, 230)


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
    phase: Optional[str] = None,
    snapshot: Optional[Dict[str, float]] = None,
) -> str:
    """
    “今日のひとこと” を返す。OpenAIが使えなければローカルで生成。
    engine: "gpt-4-turbo" (既定) / "gpt-5" / その他環境指定モデル
    phase: preopen/postopen/noon/afternoon/outlook で口調の目的を明確化
    snapshot: 先物/VIX/為替など（将来的な拡張口）
    """
    # OpenAIを使える条件
    use_api = _OPENAI_AVAILABLE and bool(os.getenv("OPENAI_API_KEY"))
    model = _resolve_model_name(engine)

    # APIなし → ローカル生成
    if not use_api:
        return _local_fallback_comment(
            regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score,
            phase=phase, seed=seed,
        )

    # --------- OpenAIで生成 ----------
    # 構造化された事実
    top_secs = [str(s.get("sector", "")) for s in sectors if s.get("sector")][:5]
    phase_title = {
        "preopen":   "寄付き前の温度感（7:20現在）",
        "postopen":  "寄付き直後の温度感（9:50現在）",
        "noon":      "前場の総括と後場の温度感（12:00現在）",
        "afternoon": "引け前の温度感（14:55現在）",
        "outlook":   "明日への展望（17:00現在）",
    }.get((phase or "").lower(), "マーケットの温度感")

    snap_txt = ""
    if snapshot:
        parts = []
        if "nikkei_fut" in snapshot: parts.append(f"日経先物 {snapshot['nikkei_fut']:+.2f}%")
        if "spx_fut"   in snapshot: parts.append(f"米先物 {snapshot['spx_fut']:+.2f}%")
        if "vix"       in snapshot: parts.append(f"VIX {snapshot['vix']:.1f}")
        if "usd_jpy"   in snapshot: parts.append(f"ドル円 {snapshot['usd_jpy']:+.2f}%")
        if "gold"      in snapshot: parts.append(f"金 {snapshot['gold']:+.2f}%")
        if parts:
            snap_txt = " | 指標: " + ", ".join(parts)

    sys = (
        "あなたは日本語の投資アシスタント。"
        "日本の個人投資家が“相場全体の温度感”を素早く掴めるように、"
        "砕けた口調で、短く（2文以内・最大230字）、絵文字を適度に使い、"
        "前日比（あれば）と注目セクターを織り交ぜ、断定や過度な助言は避け、"
        "出力は一段落のみにしてください。"
        "禁止: 箇条書き、改行の多用、専門用語の羅列。"
    )

    user = (
        f"時間帯: {phase_title}\n"
        f"状況: Regime={regime}, Score={score:.3f}, AdoptRate={adopt_rate:.3f}, "
        f"PrevScore={'' if prev_score is None else f'{prev_score:.3f}'}, "
        f"TopSectors={', '.join(top_secs) if top_secs else 'なし'}{snap_txt}\n"
        f"条件: 一段落・2文以内・最大230字・砕けた口調・適度な絵文字・煽らない"
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
            adopt_rate=adopt_rate, prev_score=prev_score,
            phase=phase, seed=seed,
        )