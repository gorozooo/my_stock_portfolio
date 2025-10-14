# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple
from hashlib import sha1
from django.core.cache import cache

# ====== 型 ======
@dataclass
class AdviceItem:
    id: int                 # 永続化してないので0固定。API実装後に置換可
    message: str
    score: float            # 優先度（大きいほど上位）
    taken: bool = False     # UIのチェック既定

# ====== ユーティリティ ======
def _pct(v) -> float:
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0

def _hash_msg(msg: str) -> str:
    return sha1(msg.encode("utf-8")).hexdigest()

def _cooldown_pass(msg: str, days: int = 7) -> bool:
    """
    同一メッセージの連投抑制。days 以内に同じテキストを出したら抑制。
    """
    key = f"advise:cd:{_hash_msg(msg)}"
    if cache.get(key):
        return False
    cache.set(key, 1, timeout=days * 24 * 60 * 60)
    return True

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

# ====== ルールベース生成 ======
def _rules(kpis: Dict, sectors: List[Dict]) -> List[AdviceItem]:
    items: List[AdviceItem] = []

    # 1) ROI 乖離（評価 vs 現金化）
    gap = _pct(kpis.get("roi_gap_abs"))
    if gap >= 20:
        score = min(1.0, gap / 80.0)  # 乖離80ptで満点
        msg = f"評価ROIと現金ROIの乖離が {gap:.1f}pt。評価と実際の差が大きい。ポジション整理を検討。"
        items.append(AdviceItem(0, msg, score))

    # 2) 流動性が低い
    liq = _pct(kpis.get("liquidity_rate_pct"))
    if liq and liq < 50:
        score = min(1.0, (50 - liq) / 30)  # 20%台で強め
        msg = f"流動性 {liq:.1f}% と低め。現金化余地の確保を検討。"
        items.append(AdviceItem(0, msg, score))

    # 3) 信用比率が高すぎる
    mr = _pct(kpis.get("margin_ratio_pct"))
    if mr >= 60:
        score = min(1.0, (mr - 60) / 30)
        msg = f"信用比率が {mr:.1f}%。レバレッジと下落耐性を再確認。"
        items.append(AdviceItem(0, msg, score))

    # 4) セクター偏在（上位1セクターの比率）
    if sectors:
        total_mv = sum(max(0.0, _pct(s.get("mv"))) for s in sectors) or 1.0
        top = sectors[0]
        top_ratio = _pct(top.get("mv")) / total_mv * 100.0
        if top_ratio >= 45:
            score = min(1.0, (top_ratio - 45) / 25)
            msg = f"セクター偏在（{top.get('sector','不明')} {top_ratio:.1f}%）。分散を検討。"
            items.append(AdviceItem(0, msg, score))
        # 未分類が多すぎる
        uncat = next((s for s in sectors if s.get("sector") == "未分類"), None)
        if uncat:
            un_ratio = _pct(uncat.get("mv")) / total_mv * 100.0
            if un_ratio >= 40:
                score = min(0.8, (un_ratio - 40) / 30)
                msg = f"未分類セクター比率 {un_ratio:.1f}%。銘柄の業種タグ整備を。"
                items.append(AdviceItem(0, msg, score))

    # 5) 今月の実現が大きくプラス（利確提案）
    rm = _pct(kpis.get("realized_month"))
    if rm > 0:
        score = min(0.6, rm / max(1.0, _pct(kpis.get("total_assets")) / 200))  # 粗い強度
        msg = "今月は実現益が出ています。含み益上位からの段階的利確を検討。"
        items.append(AdviceItem(0, msg, score))

    # 6) 評価ROIがマイナス（守り）
    re = kpis.get("roi_eval_pct")
    if re is not None and re < 0:
        score = min(0.9, abs(re) / 40)
        msg = f"評価ROIが {re:.2f}%。損失限定ルール（逆指値/縮小）を再設定。"
        items.append(AdviceItem(0, msg, score))

    return items

# ====== 重複排除 & クールダウン & ソート ======
def _post_process(items: List[AdviceItem]) -> List[AdviceItem]:
    # テキスト重複を排除
    seen = set()
    uniq: List[AdviceItem] = []
    for it in items:
        key = it.message.strip()
        if key in seen:
            continue
        seen.add(key)
        # クールダウン適用（直近N日同一文面は抑制）
        if _cooldown_pass(key):
            uniq.append(it)

    # スコア降順
    uniq.sort(key=lambda x: x.score, reverse=True)
    # 上位3件だけ強調（チェック既定ON）
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

# ====== エントリポイント ======
def summarize(kpis: Dict, sectors: List[Dict]) -> Tuple[str, List[Dict], str, str, str]:
    """
    戻り値:
      ai_note: ヘッダー要約（自然文）
      ai_items: 提案リスト（id/message/score/taken）
      session_id: 表示世代ID（キャッシュキーに利用可）
      weekly: 週次レポート素案
      nextmove: 次の一手素案
    """
    ai_note = _header_note(kpis, sectors)
    items = _post_process(_rules(kpis, sectors))
    ai_items = [asdict(it) for it in items]
    session_id = _hash_msg(ai_note)[:8]
    weekly = weekly_report(kpis, sectors)
    nextmove = next_move(kpis, sectors)
    return ai_note, ai_items, session_id, weekly, nextmove