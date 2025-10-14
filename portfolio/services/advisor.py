# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple, Optional
from hashlib import sha1
from math import log, sqrt
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from ..models_advisor import AdvicePolicy, AdviceItem as AdviceItemModel, AdviceSession

# ====== 型 ======
@dataclass
class AdviceItem:
    id: int                 # 永続化してないので0固定（保存時に差し替え）
    message: str
    score: float            # ルールベース素点（0-1想定、学習重みで補正）
    kind: Optional[str] = None
    taken: bool = False

# ====== ユーティリティ ======
def _pct(v) -> float:
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0

def _hash_msg(msg: str) -> str:
    return sha1(msg.encode("utf-8")).hexdigest()

def _cooldown_pass(msg: str, days: int = 7) -> bool:
    key = f"advise:cd:{_hash_msg(msg)}"
    if cache.get(key):
        return False
    cache.set(key, 1, timeout=days * 24 * 60 * 60)
    return True

# ====== ポリシー（UCB1） ======
def _ucb1(kind: str, c: float = 1.2) -> float:
    pol = AdvicePolicy.objects.filter(kind=kind).first()
    if not pol:
        return 0.8   # 未学習は少し高めで探索
    total_trials = max(1, sum(p.n for p in AdvicePolicy.objects.all()))
    if pol.n <= 0:
        return 0.8
    return max(0.0, min(1.5, pol.avg_reward + c * sqrt(log(total_trials) / pol.n)))

def _apply_policy_weight(items: List[AdviceItem]) -> None:
    for it in items:
        if not it.kind:
            continue
        w = _ucb1(it.kind)  # 0〜1.5程度
        it.score = max(0.0, min(1.5, it.score * w))

# ====== ヘッダー要約 ======
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

# ====== ルールベース ======
def _rules(kpis: Dict, sectors: List[Dict]) -> List[AdviceItem]:
    items: List[AdviceItem] = []

    # 1) ROI 乖離（評価 vs 現金化）
    gap = _pct(kpis.get("roi_gap_abs"))
    if gap >= 20:
        score = min(1.0, gap / 80.0)
        msg = f"評価ROIと現金ROIの乖離が {gap:.1f}pt。評価と実際の差が大きい。ポジション整理を検討。"
        items.append(AdviceItem(0, msg, score, kind="REBALANCE"))

    # 2) 流動性が低い
    liq = _pct(kpis.get("liquidity_rate_pct"))
    if liq and liq < 50:
        score = min(1.0, (50 - liq) / 30)
        msg = f"流動性 {liq:.1f}% と低め。現金化余地の確保を検討。"
        items.append(AdviceItem(0, msg, score, kind="ADD_CASH"))

    # 3) 信用比率が高すぎる
    mr = _pct(kpis.get("margin_ratio_pct"))
    if mr >= 60:
        score = min(1.0, (mr - 60) / 30)
        msg = f"信用比率が {mr:.1f}%。レバレッジと下落耐性を再確認。"
        items.append(AdviceItem(0, msg, score, kind="REDUCE_MARGIN"))

    # 4) セクター偏在
    if sectors:
        total_mv = sum(max(0.0, _pct(s.get("mv"))) for s in sectors) or 1.0
        top = sectors[0]
        top_ratio = _pct(top.get("mv")) / total_mv * 100.0
        if top_ratio >= 45:
            score = min(1.0, (top_ratio - 45) / 25)
            msg = f"セクター偏在（{top.get('sector','不明')} {top_ratio:.1f}%）。分散を検討。"
            items.append(AdviceItem(0, msg, score, kind="REBALANCE"))
        uncat = next((s for s in sectors if s.get("sector") == "未分類"), None)
        if uncat:
            un_ratio = _pct(uncat.get("mv")) / total_mv * 100.0
            if un_ratio >= 40:
                score = min(0.8, (un_ratio - 40) / 30)
                msg = f"未分類セクター比率 {un_ratio:.1f}%。銘柄の業種タグ整備を。"
                items.append(AdviceItem(0, msg, score, kind="FIX_METADATA"))

    # 5) 今月 実現益プラス → 利確提案
    rm = _pct(kpis.get("realized_month"))
    if rm > 0:
        score = min(0.6, rm / max(1.0, _pct(kpis.get("total_assets")) / 200))
        msg = "今月は実現益が出ています。含み益上位からの段階的利確を検討。"
        items.append(AdviceItem(0, msg, score, kind="TRIM_WINNERS"))

    # 6) 評価ROIマイナス → 守り
    re = kpis.get("roi_eval_pct")
    if re is not None and re < 0:
        score = min(0.9, abs(re) / 40)
        msg = f"評価ROIが {re:.2f}%。損失限定ルール（逆指値/縮小）を再設定。"
        items.append(AdviceItem(0, msg, score, kind="CUT_LOSERS"))

    return items

# ====== 重複排除 & CD & ポリシー適用 & ソート ======
def _post_process(items: List[AdviceItem]) -> List[AdviceItem]:
    seen = set()
    uniq: List[AdviceItem] = []
    for it in items:
        key = it.message.strip()
        if key in seen:
            continue
        seen.add(key)
        if _cooldown_pass(key):
            uniq.append(it)

    _apply_policy_weight(uniq)

    uniq.sort(key=lambda x: x.score, reverse=True)
    for i, it in enumerate(uniq[:3]):
        it.taken = True
    return uniq

# ====== 週次レポート / 次の一手 ======
def weekly_report(kpis: Dict, sectors: List[Dict]) -> str:
    head = _header_note(kpis, sectors)
    sect = ", ".join([f"{s['sector']} {s['rate']}%" for s in sectors[:5]]) or "—"
    return f"{head} セクター概況: {sect}。勝率データは今後追加予定。"

def next_move(kpis: Dict, sectors: List[Dict]) -> str:
    items = _post_process(_rules(kpis, sectors))
    bullets = " / ".join([it.message for it in items[:3]]) or "様子見。"
    return f"次の一手: {bullets}"

# ====== エントリポイント（生成） ======
def summarize(kpis: Dict, sectors: List[Dict]) -> Tuple[str, List[Dict], str, str, str]:
    ai_note = _header_note(kpis, sectors)
    items = _post_process(_rules(kpis, sectors))
    ai_items = [asdict(it) for it in items]
    session_id = _hash_msg(ai_note)[:8]
    weekly = weekly_report(kpis, sectors)
    nextmove = next_move(kpis, sectors)
    return ai_note, ai_items, session_id, weekly, nextmove

# ====== 永続化（ホーム描画時に1回/数時間） ======
def ensure_session_persisted(ai_note: str, ai_items: List[Dict], kpis: Dict) -> List[Dict]:
    """
    同じ ai_note のセッション連打を防ぎつつ、
    AdviceSession / AdviceItem を保存。保存済みなら既存IDを返す。
    戻り値: ai_items（id 置換後）
    """
    note_hash = _hash_msg(ai_note)[:12]
    cache_key = f"advisor:persist:{note_hash}"
    if cache.get(cache_key):
        # 直近保存済み → 既存最新セッションから id を引き当て（なければそのまま）
        sess = AdviceSession.objects.order_by("-created_at").first()
        if not sess:
            return ai_items
        db_items = list(AdviceItemModel.objects.filter(session=sess).order_by("-score", "-id"))
        # message で対応付け（簡易）
        out = []
        for it in ai_items:
            db = next((x for x in db_items if x.message == it["message"]), None)
            out.append({**it, "id": db.id if db else 0})
        return out

    with transaction.atomic():
        sess = AdviceSession.objects.create(
            context_json=kpis,
            note=ai_note[:200],
        )
        db_items = []
        for it in ai_items:
            db = AdviceItemModel.objects.create(
                session=sess,
                kind=it.get("kind") or "REBALANCE",
                message=it["message"],
                score=float(it.get("score") or 0.0),
                reasons=[],
                taken=bool(it.get("taken") or False),
            )
            db_items.append(db)

    cache.set(cache_key, 1, timeout=3 * 60 * 60)  # 3時間ガード

    # 生成した id を反映
    out = []
    for it in ai_items:
        db = next((x for x in db_items if x.message == it["message"]), None)
        out.append({**it, "id": db.id if db else 0})
    return out