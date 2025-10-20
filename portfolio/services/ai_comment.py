# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, random, re
from typing import Dict, Any, List, Optional
from datetime import datetime

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


# ========= 履歴ストア（JSONL / 擬似学習） =========
def _media_root() -> str:
    return getattr(settings, "MEDIA_ROOT", "") or os.getcwd()

def _history_path(persona_id: str = "default") -> str:
    base = os.path.join(_media_root(), "advisor")
    os.makedirs(base, exist_ok=True)
    # personごとに分ける（将来マルチユーザー対応が簡単）
    return os.path.join(base, f"comment_history_{persona_id}.jsonl")

def _append_history(persona_id: str, record: Dict[str, Any]) -> None:
    path = _history_path(persona_id)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _load_recent_history(persona_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    path = _history_path(persona_id)
    if not os.path.exists(path):
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out[-limit:]

_EMOJI_SET = set("😀😃😄😁😆😅😂🙂😊🥲😍😘😗😙😚🤗🤩🤔🤨🫠😐😑😶🙄😏😣😥😮‍💨😮😯😪😫🥱😴😌😛😜🤪😝🤤😒😓😔😕🙃🫤🫥😲☹️🙁😖😞😟😤😢😭😦😧😨😩🤯😬😰😱🥵🥶😳🤒🤕🤢🤮🤧😇🥳🤝👍👎🙏💪🔥✨💡🚀📈📉📊🎯🧠🛡🪫🌞🌧⚖️💤🧊🌀😐😶🤔🙂")
def _emoji_density(s: str) -> float:
    if not s:
        return 0.0
    emo = sum(1 for ch in s if ch in _EMOJI_SET)
    return emo / max(len(s), 1)

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
    preferred_emoji: str = "medium",
) -> str:
    """APIが無い時のローカル生成（砕けたトーン＋絵文字＋前日比）。"""
    rg = (regime or "").upper()
    top_secs = [str(s.get("sector", "")) for s in sectors if s.get("sector")]
    top_txt = "・".join(top_secs[:3]) if top_secs else "（特に目立つセクターなし）"

    rnd = random.Random(f"{seed}|{rg}|{score:.3f}|{adopt_rate:.3f}")

    # 絵文字密度の微調整
    emo = {"low":"", "medium":"✨", "high":"🔥"}[preferred_emoji]

    opens_on  = [f"📈 地合いまずまず{emo}", f"🌞 いい風きてる{emo}", f"💪 強めのトーン{emo}", f"🚀 ノッてきた{emo}"]
    opens_off = [f"🌧 ちょい向かい風…{emo}", f"🧊 冷え気味。慎重に{emo}", f"😴 元気薄め{emo}", f"🪫 静かな始まり{emo}"]
    opens_neu = [f"😐 方向感はフラット{emo}", f"⚖️ 判断は落ち着いて{emo}", f"🤔 様子見優勢{emo}", f"😶 まだ静観ムード{emo}"]

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

def _derive_style_from_history(hist: List[Dict[str, Any]]) -> Dict[str, Any]:
    """履歴から好みを推定（超軽量）。"""
    if not hist:
        return {"emoji_pref": "medium", "target_len": 120}

    # 文章長の中央値近似 & 絵文字密度
    texts = [h.get("text","") for h in hist if isinstance(h.get("text",""), str)]
    if not texts:
        return {"emoji_pref": "medium", "target_len": 120}
    lens = [len(t) for t in texts]
    avg_len = sum(lens)/len(lens)
    avg_emo = sum(_emoji_density(t) for t in texts)/len(texts)

    # ざっくりルール
    if avg_emo >= 0.02:
        emoji_pref = "high"
    elif avg_emo <= 0.005:
        emoji_pref = "low"
    else:
        emoji_pref = "medium"

    # 長さは100〜180にクリップ
    target_len = int(max(100, min(180, avg_len)))
    return {"emoji_pref": emoji_pref, "target_len": target_len}

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
    persona_id: str = "default",
) -> str:
    """
    “今日のひとこと” を返す。OpenAIが使えなければローカルで生成。
    engine: "gpt-4-turbo" (既定) / "gpt-5"
    persona_id: 履歴ファイルの分離キー（LINE user_id 等）
    """
    # --- 履歴から好みを抽出 ---
    history = _load_recent_history(persona_id, limit=50)
    style = _derive_style_from_history(history)
    preferred_emoji = style["emoji_pref"]
    target_len = style["target_len"]

    # OpenAIを使える条件
    use_api = _OPENAI_AVAILABLE and bool(os.getenv("OPENAI_API_KEY"))
    model = _resolve_model_name(engine)

    # APIなし → ローカル生成
    if not use_api:
        text = _local_fallback_comment(
            regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score, seed=seed,
            preferred_emoji=preferred_emoji,
        )
        _append_history(persona_id, {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "engine": "local",
            "regime": regime, "score": score, "prev_score": prev_score,
            "adopt_rate": adopt_rate, "sectors_top": [s.get("sector") for s in sectors[:5]],
            "text": text
        })
        return text

    # --------- OpenAIで生成 ----------
    top_secs = [str(s.get("sector", "")) for s in sectors if s.get("sector")][:5]
    facts = (
        f"Regime={regime}, Score={score:.3f}, "
        f"AdoptRate={adopt_rate:.3f}, PrevScore={'' if prev_score is None else f'{prev_score:.3f}'}, "
        f"TopSectors={', '.join(top_secs) if top_secs else 'なし'}"
    )

    # 履歴由来のスタイル指示を追加
    style_hint = {
        "emoji_preference": preferred_emoji,            # low / medium / high
        "target_length_chars": target_len,              # 目安
        "voice": "casual, friendly, human-like",
    }

    sys = (
        "あなたは日本トップクラスの証券ディーラー。"
        "砕けた口調で、短く（2文以内）、絵文字を適度に使って、"
        "前日比コメント（あれば）と注目セクターを織り交ぜ、過度な断定や助言は避け、"
        "読みやすい一段落にまとめてください。"
        "禁止: 箇条書き、改行過多、専門用語の羅列。"
    )
    user = (
        f"状況を要約して『今日のひとこと』を書いてください。\n"
        f"- 事実: {facts}\n"
        f"- スタイル: {json.dumps(style_hint, ensure_ascii=False)}\n"
        f"- 出力は一段落のみ（改行なし）"
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

        text = _shorten(text, max(100, min(230, target_len + 20)))
        _append_history(persona_id, {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "engine": model,
            "regime": regime, "score": score, "prev_score": prev_score,
            "adopt_rate": adopt_rate, "sectors_top": [s.get("sector") for s in sectors[:5]],
            "style_hint": style_hint,
            "text": text
        })
        return text
    except Exception:
        # 失敗時はローカル
        text = _local_fallback_comment(
            regime=regime, score=score, sectors=sectors,
            adopt_rate=adopt_rate, prev_score=prev_score, seed=seed,
            preferred_emoji=preferred_emoji,
        )
        _append_history(persona_id, {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "engine": "local-fallback",
            "regime": regime, "score": score, "prev_score": prev_score,
            "adopt_rate": adopt_rate, "sectors_top": [s.get("sector") for s in sectors[:5]],
            "text": text
        })
        return text