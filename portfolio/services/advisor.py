# -*- coding: utf-8 -*-
from __future__ import annotations
import json
import os
from dataclasses import dataclass, asdict
from hashlib import sha1
from math import exp
from typing import List, Dict, Tuple, Optional

from django.conf import settings
from django.core.cache import cache

from ..models_advisor import AdvicePolicy, AdviceSession, AdviceItem

# =========================
# 表示用構造
# =========================
@dataclass
class AdviceItemView:
    id: int                 # 永続化前は0（DB保存時に実IDへ）
    message: str
    score: float            # 0..1（大きいほど上位）
    taken: bool = False     # 既定チェック

# =========================
# ユーティリティ
# =========================
def _pct(v) -> float:
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0

def _hash_msg(msg: str) -> str:
    return sha1(msg.encode("utf-8")).hexdigest()

def _cooldown_pass(msg: str, days: int = 7) -> bool:
    """同一メッセージの連投抑制（days 以内は出さない）"""
    key = f"advise:cd:{_hash_msg(msg)}"
    if cache.get(key):
        return False
    cache.set(key, 1, timeout=days * 24 * 60 * 60)
    return True

# =========================
# policy.json（自動読み込み）
# =========================
_POLICY_CACHE_KEY = "advisor:policy:blob"
_POLICY_MTIME_KEY = "advisor:policy:mtime"
_DEFAULT_POLICY_REL = "media/advisor/policy.json"  # MEDIA_ROOT基準

def _policy_path() -> str:
    # MEDIA_ROOT/ media/advisor/policy.json（相対でも絶対でもOK）
    rel = getattr(settings, "ADVISOR_POLICY_PATH", _DEFAULT_POLICY_REL)
    if os.path.isabs(rel):
        return rel
    base = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
    return os.path.join(base, rel)

def _load_policy_from_disk() -> Optional[dict]:
    path = _policy_path()
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _get_policy() -> Optional[dict]:
    """
    - ファイルの mtime を見てキャッシュ更新
    - 失敗時は None（補正なし）
    """
    path = _policy_path()
    try:
        mtime = os.path.getmtime(path) if os.path.exists(path) else None
    except Exception:
        mtime = None

    cached_mtime = cache.get(_POLICY_MTIME_KEY)
    cached_policy = cache.get(_POLICY_CACHE_KEY)

    if mtime and (cached_policy is None or cached_mtime != mtime):
        policy = _load_policy_from_disk()
        cache.set(_POLICY_CACHE_KEY, policy, timeout=60 * 60)  # 1h
        cache.set(_POLICY_MTIME_KEY, mtime, timeout=60 * 60)
        return policy
    return cached_policy

# =========================
# 特徴量抽出（将来ML用）
# =========================
FEATURES = [
    "roi_gap_abs",
    "liquidity_rate_pct",
    "margin_ratio_pct",
    "realized_month_ratio",
    "top_sector_ratio",
    "uncat_sector_ratio",
    "win_ratio",
]

def _build_features(kpis: Dict, sectors: List[Dict]) -> Dict[str, float]:
    total_assets = max(1.0, _pct(kpis.get("total_assets")))
    realized_month_ratio = _pct(kpis.get("realized_month")) / total_assets

    top_ratio = 0.0
    uncat_ratio = 0.0
    if sectors:
        total_mv = sum(max(0.0, _pct(s.get("mv"))) for s in sectors) or 1.0
        top_ratio = _pct(sectors[0].get("mv")) / total_mv if sectors else 0.0
        uncat = next((s for s in sectors if s.get("sector") == "未分類"), None)
        if uncat:
            uncat_ratio = _pct(uncat.get("mv")) / total_mv

    feats = {
        "roi_gap_abs": _pct(kpis.get("roi_gap_abs")),
        "liquidity_rate_pct": _pct(kpis.get("liquidity_rate_pct")),
        "margin_ratio_pct": _pct(kpis.get("margin_ratio_pct")),
        "realized_month_ratio": realized_month_ratio,
        "top_sector_ratio": top_ratio * 100.0,
        "uncat_sector_ratio": uncat_ratio * 100.0,
        "win_ratio": _pct(kpis.get("win_ratio")),
    }
    # NaN 無効化
    return {k: (0.0 if v != v else float(v)) for k, v in feats.items()}

# =========================
# ルール（フォールバック提案）
# =========================
_CATEGORY_PATTERNS = [
    ("GAP",     ["乖離", "評価ROIと現金ROI"]),
    ("LIQ",     ["流動性", "現金化余地"]),
    ("MARGIN",  ["信用比率", "レバレッジ"]),
    ("SECTOR",  ["セクター偏在"]),
    ("UNCAT",   ["未分類セクター", "業種タグ"]),
    ("REALIZE", ["実現益", "利確", "段階的利確"]),
    ("NEGROI",  ["評価ROIが", "損失限定"]),
]

def _category_of(msg: str) -> str:
    m = msg or ""
    for cat, keys in _CATEGORY_PATTERNS:
        if any(k in m for k in keys):
            return cat
    return "OTHER"

def _header_note(kpis: Dict, sectors: List[Dict]) -> str:
    ta = kpis.get("total_assets", 0)
    un = kpis.get("unrealized_pnl", 0)
    ca = kpis.get("cash_total", 0)
    re = kpis.get("roi_eval_pct", None)
    rl = kpis.get("roi_liquid_pct", None)

    parts = [
        f"総資産¥{ta:,}",
        f"含み損益{'+' if un >= 0 else ''}¥{un:,}",
        f"現金¥{ca:,}",
    ]
    if re is not None:
        parts.append(f"評価ROI{re:.2f}%")
    if rl is not None:
        parts.append(f"現金ROI{rl:.2f}%")
    return "、".join(parts) + "。"

def _rules(kpis: Dict, sectors: List[Dict]) -> List[AdviceItemView]:
    items: List[AdviceItemView] = []

    gap = _pct(kpis.get("roi_gap_abs"))
    if gap >= 20:
        score = min(1.0, gap / 80.0)
        msg = f"評価ROIと現金ROIの乖離が {gap:.1f}pt。評価と実際の差が大きい。ポジション整理を検討。"
        items.append(AdviceItemView(0, msg, score))

    liq = _pct(kpis.get("liquidity_rate_pct"))
    if liq and liq < 50:
        score = min(1.0, (50 - liq) / 30)
        msg = f"流動性 {liq:.1f}% と低め。現金化余地の確保を検討。"
        items.append(AdviceItemView(0, msg, score))

    mr = _pct(kpis.get("margin_ratio_pct"))
    if mr >= 60:
        score = min(1.0, (mr - 60) / 30)
        msg = f"信用比率が {mr:.1f}%。レバレッジと下落耐性を再確認。"
        items.append(AdviceItemView(0, msg, score))

    if sectors:
        total_mv = sum(max(0.0, _pct(s.get("mv"))) for s in sectors) or 1.0
        top = sectors[0]
        top_ratio = _pct(top.get("mv")) / total_mv * 100.0
        if top_ratio >= 45:
            score = min(1.0, (top_ratio - 45) / 25)
            msg = f"セクター偏在（{top.get('sector','不明')} {top_ratio:.1f}%）。分散を検討。"
            items.append(AdviceItemView(0, msg, score))
        uncat = next((s for s in sectors if s.get("sector") == "未分類"), None)
        if uncat:
            un_ratio = _pct(uncat.get("mv")) / total_mv * 100.0
            if un_ratio >= 40:
                score = min(0.8, (un_ratio - 40) / 30)
                msg = f"未分類セクター比率 {un_ratio:.1f}%。銘柄の業種タグ整備を。"
                items.append(AdviceItemView(0, msg, score))

    rm = _pct(kpis.get("realized_month"))
    if rm > 0:
        score = 0.5
        msg = "今月は実現益が出ています。含み益上位からの段階的利確を検討。"
        items.append(AdviceItemView(0, msg, score))

    re = kpis.get("roi_eval_pct")
    if re is not None and re < 0:
        score = min(0.9, abs(re) / 40)
        msg = f"評価ROIが {re:.2f}%。損失限定ルール（逆指値/縮小）を再設定。"
        items.append(AdviceItemView(0, msg, score))

    return items

def _post_process(items: List[AdviceItemView]) -> List[AdviceItemView]:
    seen = set()
    uniq: List[AdviceItemView] = []
    for it in items:
        key = it.message.strip()
        if key in seen:
            continue
        seen.add(key)
        if _cooldown_pass(key):
            uniq.append(it)
    # 上位3件は既定チェックON（UIの初期状態）
    uniq.sort(key=lambda x: x.score, reverse=True)
    for i, it in enumerate(uniq[:3]):
        it.taken = True
    return uniq

# =========================
# policy.json によるスコア補正
# =========================
def _bonus_from_policy(msg: str, policy: dict) -> float:
    """
    policy.json からメッセージ/カテゴリの改善度を見て 0..1 のボーナスへ。
    - avg_improve: -1..1 を 0..1 へ線形変換
    - confidence:  0..1 の重み
    返り値: 0..1（None相当は0）
    """
    if not policy:
        return 0.0

    cat = _category_of(msg)
    m_tbl = (policy.get("message") or {})
    c_tbl = (policy.get("category") or {})

    # 1) メッセージ優先
    m = m_tbl.get((msg or "").strip())
    if m:
        base = (float(m.get("avg_improve", 0.0)) + 1.0) / 2.0  # -1..1 -> 0..1
        conf = float(m.get("confidence", 0.0))
        return max(0.0, min(1.0, base * conf))

    # 2) カテゴリで代替
    c = c_tbl.get(cat)
    if c:
        base = (float(c.get("avg_improve", 0.0)) + 1.0) / 2.0
        conf = float(c.get("confidence", 0.0))
        return max(0.0, min(1.0, base * conf))

    return 0.0

def _apply_policy(items: List[AdviceItemView], kpis: Dict, sectors: List[Dict]) -> List[AdviceItemView]:
    """
    base_score(0..1) をポリシーボーナス(0..1)と合成。
    合成式（シンプル）： score' = clip( 0.6*base + 0.4*bonus )
    """
    policy = _get_policy()
    if not policy:
        return items

    boosted: List[AdviceItemView] = []
    for it in items:
        base = float(it.score)
        bonus = _bonus_from_policy(it.message, policy)
        score = max(0.0, min(1.0, 0.6 * base + 0.4 * bonus))
        boosted.append(AdviceItemView(id=it.id, message=it.message, score=score, taken=it.taken))

    boosted.sort(key=lambda x: x.score, reverse=True)
    # 既定チェックは並び替え後に再設定
    for i, it in enumerate(boosted):
        it.taken = (i < 3)
    return boosted

# =========================
# 週次/次の一手
# =========================
def weekly_report(kpis: Dict, sectors: List[Dict]) -> str:
    head = _header_note(kpis, sectors)
    sect = ", ".join([
        f"{s.get('sector', '-')}"
        f" {s.get('rate', s.get('share_pct', 0))}%"
        for s in sectors[:5]
    ]) or "--"
    return f"{head} セクター概況: {sect}。勝率データは今後追加予定。"

def next_move(kpis: Dict, sectors: List[Dict]) -> str:
    items = _post_process(_rules(kpis, sectors))
    bullets = " / ".join([it.message for it in items[:3]]) or "様子見。"
    return f"次の一手: {bullets}"

# =========================
# エントリポイント
# =========================
def summarize(kpis: Dict, sectors: List[Dict], variant: str = "A") -> Tuple[str, List[Dict], str, str, str]:
    """
    variant:
      'A' -> ルールのみ
      'B' -> policy.json 補正を使ってスコアをブースト
    """
    ai_note = _header_note(kpis, sectors)

    # まずルールで候補
    base_items = _rules(kpis, sectors)
    feats = _build_features(kpis, sectors)

    use_policy = (variant == "B")
    scored: List[AdviceItemView] = []
    pol_score = _score_with_policy(feats) if use_policy else None
    policy_ok = (pol_score is not None)

    for it in base_items:
        score = it.score
        if use_policy and policy_ok:
            # ルール 0.6 + policy 0.4
            score = max(0.0, min(1.0, 0.6 * float(it.score) + 0.4 * float(pol_score)))
        scored.append(AdviceItemView(id=it.id, message=it.message, score=score, taken=it.taken))

    items = _post_process(scored if (use_policy and policy_ok) else base_items)
    ai_items = [asdict(it) for it in items]
    session_id = sha1(ai_note.encode("utf-8")).hexdigest()[:8]
    weekly = weekly_report(kpis, sectors)
    nextmove = next_move(kpis, sectors)
    return ai_note, ai_items, session_id, weekly, nextmove

# =========================
# セッション永続化（ビューから呼ぶ）
# =========================
def ensure_session_persisted(ai_note: str, ai_items: list, kpis: dict, variant: str = "A"):
    """
    セッション永続化: AdviceSession/AdviceItem を保存
    - context_json にも ab_variant を残す
    - AdviceSession.variant にも保存
    """
    ctx = dict(kpis or {})
    ctx.setdefault("ab_variant", variant)

    session = AdviceSession.objects.create(
        context_json=ctx,
        note=ai_note[:200],
        variant=variant,
    )
    for item in ai_items:
        AdviceItem.objects.create(
            session=session,
            kind=item.get("kind", "REBALANCE"),
            message=item.get("message", ""),
            score=item.get("score", 0.0),
            taken=item.get("taken", False),
            reasons=item.get("reasons", []),
        )
    # DBの実IDが付いた訳ではあるが、呼び元の描画はそのままでOK
    return ai_items
    
# ==============================
# policy.json を使ったスコア補正
# ==============================
import os, json

def _score_with_policy(feats: dict) -> float:
    """
    policy.json の重みを読み込み、特徴量 dict からスコア（0〜1）を返す。
    存在しない・読み込めない場合は None を返す。
    """
    try:
        # --- ファイルパス解決 ---
        base_dir = os.path.dirname(__file__)
        policy_path = os.path.join(base_dir, "policy.json")

        if not os.path.exists(policy_path):
            return None

        # --- JSON読込 ---
        with open(policy_path, "r", encoding="utf-8") as f:
            policy = json.load(f)

        weights = policy.get("weights", {})
        bias = float(policy.get("bias", 0.0))

        # --- スコア計算 ---
        score = bias
        for k, v in feats.items():
            if k in weights:
                score += float(weights[k]) * float(v)

        # sigmoid風正規化（-1〜+1 → 0〜1）
        score = 1 / (1 + pow(2.71828, -score))
        return round(float(score), 4)

    except Exception as e:
        print(f"[advisor] policy load failed: {e}")
        return None
        
        
def extract_features_for_learning(kpis: dict, sectors: list) -> dict:
    """
    KPI・セクター情報から機械学習に使える特徴量を抽出する。
    - policy_snapshot / advisor_train の共通で利用可能。
    - 特徴量は最初はシンプルにKPIベース。
    """
    try:
        feat = {}

        # KPI数値をそのままコピー（Noneは0に）
        for k, v in kpis.items():
            if isinstance(v, (int, float)):
                feat[k] = float(v)
            elif isinstance(v, (str, bool)):
                feat[k] = float(v) if isinstance(v, bool) else 0.0
            else:
                feat[k] = 0.0

        # セクターごとのトップ3シェアを特徴量として追加
        top3 = sorted(sectors, key=lambda s: s.get("mv", 0), reverse=True)[:3]
        for i, sec in enumerate(top3):
            feat[f"sector{i+1}_rate"] = sec.get("rate", 0.0)
            feat[f"sector{i+1}_share"] = sec.get("share_pct", 0.0)

        # 特殊派生特徴量（例：リスク関連）
        roi_eval = kpis.get("roi_eval_pct") or 0
        roi_liquid = kpis.get("roi_liquid_pct") or 0
        feat["roi_gap_abs"] = abs(roi_eval - roi_liquid)
        feat["liquidity_margin_ratio"] = (
            (kpis.get("liquidity_rate_pct") or 0) - (kpis.get("margin_ratio_pct") or 0)
        )

        return feat

    except Exception as e:
        print(f"[extract_features_for_learning] failed: {e}")
        return {}