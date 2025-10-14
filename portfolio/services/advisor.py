# portfolio/services/advisor.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple, Optional
from hashlib import sha1
from math import exp
import json
from pathlib import Path
import time

from django.conf import settings
from django.core.cache import cache

# DB 永続化（任意）
from ..models_advisor import AdviceSession, AdviceItem, AdvicePolicy  # AdvicePolicyは未使用でもOK


# ====== 表示用構造 ======
@dataclass
class AdviceItemView:
    id: int                 # 永続化前は0固定。管理コマンドで保存時に実IDへ
    message: str
    score: float            # 優先度（0..1 大きいほど上位）
    taken: bool = False     # UI既定
    kind: str = "REBALANCE" # 提案タイプ（スコア補正に使用）


# ====== ユーティリティ ======
def _pct(v) -> float:
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0


def _hash_msg(msg: str) -> str:
    return sha1(msg.encode("utf-8")).hexdigest()


def _cooldown_pass(msg: str, days: int = 7) -> bool:
    """同一メッセージの連投抑制。days 以内に同じテキストを出したら抑制。"""
    key = f"advise:cd:{_hash_msg(msg)}"
    if cache.get(key):
        return False
    cache.set(key, 1, timeout=days * 24 * 60 * 60)
    return True


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


# ====== policy.json 自動読込（60秒キャッシュ） ======
# 期待フォーマット:
# {
#   "version": 1,
#   "updated_at": "2025-10-14T12:34:56Z",
#   "bias": 1.0,                         # 全体係数（省略可）
#   "kind_weight": {                     # タイプ別係数
#     "REBALANCE": 1.00,
#     "ADD_CASH": 1.10,
#     "TRIM_WINNERS": 1.05,
#     "CUT_LOSERS": 0.95,
#     "REDUCE_MARGIN": 1.20
#   }
# }
#
# 置き場所（先勝ち）:
#   1) settings.MEDIA_ROOT / "advisor/policy.json"
#   2) settings.BASE_DIR / "policy.json"
#
_POLICY_CACHE = {"mtime": None, "data": None}


def _policy_paths() -> List[Path]:
    paths: List[Path] = []
    try:
        if getattr(settings, "MEDIA_ROOT", None):
            paths.append(Path(settings.MEDIA_ROOT) / "advisor" / "policy.json")
    except Exception:
        pass
    try:
        if getattr(settings, "BASE_DIR", None):
            paths.append(Path(settings.BASE_DIR) / "policy.json")
    except Exception:
        pass
    # 重複除去
    uniq = []
    seen = set()
    for p in paths:
        if str(p) not in seen:
            uniq.append(p)
            seen.add(str(p))
    return uniq


def _load_policy_json() -> Dict:
    """policy.json を 60 秒キャッシュで読み込む。見つからなければ {} を返す。"""
    global _POLICY_CACHE
    try:
        # 最初に存在するパスを採用
        path = next((p for p in _policy_paths() if p.exists()), None)
        if not path:
            _POLICY_CACHE.update({"mtime": None, "data": None})
            return {}

        mtime = path.stat().st_mtime
        now = time.time()
        cached = _POLICY_CACHE["data"]
        cached_mtime = _POLICY_CACHE["mtime"]
        # 60秒以内でmtime変更なしならキャッシュ使用
        if cached is not None and cached_mtime == mtime and (now - getattr(_POLICY_CACHE, "ts", now)) <= 60:
            return cached or {}

        data = json.loads(path.read_text(encoding="utf-8"))
        _POLICY_CACHE.update({"mtime": mtime, "data": data, "ts": now})
        return data or {}
    except Exception:
        return {}


def _apply_policy_boost(kind: str, base_score: float) -> float:
    """policy.json に基づいてスコアを乗算補正"""
    pol = _load_policy_json()
    if not pol:
        return base_score
    bias = float(pol.get("bias", 1.0) or 1.0)
    kw = pol.get("kind_weight") or {}
    w = float(kw.get(kind, 1.0) or 1.0)
    return _clamp01(base_score * bias * w)


# ====== 特徴量抽出 ======
FEATURES = [
    "roi_gap_abs",
    "liquidity_rate_pct",
    "margin_ratio_pct",
    "realized_month_ratio",   # realized_month / total_assets
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
    # NaN対策
    return {k: (0.0 if v != v else float(v)) for k, v in feats.items()}


# ====== 種別推定（文面→Kind） ======
def _guess_kind(msg: str) -> str:
    m = msg or ""
    if "信用比率" in m or "レバレッジ" in m:
        return "REDUCE_MARGIN"
    if "流動性" in m or "現金化" in m or "現金比率" in m:
        return "ADD_CASH"
    if "実現益" in m or "利確" in m or "含み益上位" in m:
        return "TRIM_WINNERS"
    if "評価ROIが" in m and "-" in m:
        return "CUT_LOSERS"
    # 分散 / 乖離 / セクター偏在 / 未分類タグ整備などは広く REBALANCE 扱い
    return "REBALANCE"


# ====== ルールベース（フォールバック） ======
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

    # 1) ROI 乖離（評価 vs 現金化）
    gap = _pct(kpis.get("roi_gap_abs"))
    if gap >= 20:
        score = min(1.0, gap / 80.0)  # 乖離80ptで満点
        msg = f"評価ROIと現金ROIの乖離が {gap:.1f}pt。評価と実際の差が大きい。ポジション整理を検討。"
        items.append(AdviceItemView(0, msg, score, kind="REBALANCE"))

    # 2) 流動性が低い
    liq = _pct(kpis.get("liquidity_rate_pct"))
    if liq and liq < 50:
        score = min(1.0, (50 - liq) / 30)
        msg = f"流動性 {liq:.1f}% と低め。現金化余地の確保を検討。"
        items.append(AdviceItemView(0, msg, score, kind="ADD_CASH"))

    # 3) 信用比率が高すぎる
    mr = _pct(kpis.get("margin_ratio_pct"))
    if mr >= 60:
        score = min(1.0, (mr - 60) / 30)
        msg = f"信用比率が {mr:.1f}%。レバレッジと下落耐性を再確認。"
        items.append(AdviceItemView(0, msg, score, kind="REDUCE_MARGIN"))

    # 4) セクター偏在
    if sectors:
        total_mv = sum(max(0.0, _pct(s.get("mv"))) for s in sectors) or 1.0
        top = sectors[0]
        top_ratio = _pct(top.get("mv")) / total_mv * 100.0
        if top_ratio >= 45:
            score = min(1.0, (top_ratio - 45) / 25)
            msg = f"セクター偏在（{top.get('sector','不明')} {top_ratio:.1f}%）。分散を検討。"
            items.append(AdviceItemView(0, msg, score, kind="REBALANCE"))
        uncat = next((s for s in sectors if s.get("sector") == "未分類"), None)
        if uncat:
            un_ratio = _pct(uncat.get("mv")) / total_mv * 100.0
            if un_ratio >= 40:
                score = min(0.8, (un_ratio - 40) / 30)
                msg = f"未分類セクター比率 {un_ratio:.1f}%。銘柄の業種タグ整備を。"
                items.append(AdviceItemView(0, msg, score, kind="REBALANCE"))

    # 5) 今月の実現がプラス
    rm = _pct(kpis.get("realized_month"))
    if rm > 0:
        score = 0.5
        msg = "今月は実現益が出ています。含み益上位からの段階的利確を検討。"
        items.append(AdviceItemView(0, msg, score, kind="TRIM_WINNERS"))

    # 6) 評価ROIがマイナス（守り）
    re = kpis.get("roi_eval_pct")
    if re is not None and re < 0:
        score = min(0.9, abs(re) / 40)
        msg = f"評価ROIが {re:.2f}%。損失限定ルール（逆指値/縮小）を再設定。"
        items.append(AdviceItemView(0, msg, score, kind="CUT_LOSERS"))

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
    uniq.sort(key=lambda x: x.score, reverse=True)
    # 上位3件は既定でチェックON
    for i, it in enumerate(uniq[:3]):
        it.taken = True
    return uniq


# ====== 週次/次の一手 素案 ======
def weekly_report(kpis: Dict, sectors: List[Dict]) -> str:
    head = _header_note(kpis, sectors)
    sect = ", ".join([f"{s['sector']} {s['rate']}%" for s in sectors[:5]]) or "—"
    return f"{head} セクター概況: {sect}。勝率データは今後追加予定。"


def next_move(kpis: Dict, sectors: List[Dict]) -> str:
    items = _post_process(_rules(kpis, sectors))
    bullets = " / ".join([it.message for it in items[:3]]) or "様子見。"
    return f"次の一手: {bullets}"


# ====== エントリポイント ======
def summarize(kpis: Dict, sectors: List[Dict]) -> Tuple[str, List[Dict], str, str, str]:
    """
    戻り値:
      ai_note: ヘッダー要約（自然文）
      ai_items: 提案リスト（id/message/score/taken/kind）
      session_id: 表示世代ID（キャッシュキーなどに利用可）
      weekly: 週次レポート素案
      nextmove: 次の一手素案
    """
    ai_note = _header_note(kpis, sectors)

    # ルール候補を作成
    base_items = _rules(kpis, sectors)

    # === 自己学習結果（policy.json）で補正 ===
    boosted: List[AdviceItemView] = []
    for it in base_items:
        # 種別は優先：明示 → 推定
        kind = it.kind or _guess_kind(it.message)
        new_score = _apply_policy_boost(kind, it.score)
        boosted.append(AdviceItemView(
            id=it.id, message=it.message, score=new_score, taken=it.taken, kind=kind
        ))

    # ソート + 上位チェックON
    items = _post_process(boosted)
    ai_items = [asdict(it) for it in items]

    # セッション表示ID（軽量）
    session_id = _hash_msg(ai_note)[:8]

    # レポート素案
    weekly = weekly_report(kpis, sectors)
    nextmove = next_move(kpis, sectors)

    return ai_note, ai_items, session_id, weekly, nextmove


# ====== 外部公開ユーティリティ ======
def extract_features_for_learning(kpis: Dict, sectors: List[Dict]) -> Dict[str, float]:
    """学習用の特徴量を外部（管理コマンド等）に提供"""
    return _build_features(kpis, sectors)


def ensure_session_persisted(ai_note: str, ai_items: list, kpis: dict):
    """
    AI提案をDBに保存して永続化する。
    - 最新セッションを AdviceSession として保存
    - items を AdviceItem として紐付け
    """
    try:
        session = AdviceSession.objects.create(
            context_json=kpis,
            note=ai_note[:200]
        )
        for item in ai_items:
            AdviceItem.objects.create(
                session=session,
                kind=item.get("kind", "REBALANCE"),
                message=item.get("message", ""),
                score=float(item.get("score", 0.0) or 0.0),
                taken=bool(item.get("taken", False)),
                reasons=item.get("reasons", []),
            )
    except Exception:
        # 保存失敗しても表示は継続
        pass
    return ai_items