# portfolio/services/advisor.py
from __future__ import annotations
from typing import List, Dict, Tuple

def summarize(kpis: Dict, sectors: List[Dict]) -> Tuple[str, List[str]]:
    total = float(kpis.get("total_assets", 0) or 0)
    pnl = float(kpis.get("unrealized_pnl", 0) or 0)
    wr = float(kpis.get("win_ratio", 0) or 0)
    cash = float(kpis.get("cash_balance", 0) or 0)
    realized = float(kpis.get("realized_month", 0) or 0)

    # 上位/ワーストの簡易抽出（欠損安全）
    safe_secs = [s for s in (sectors or []) if isinstance(s, dict)]
    top = sorted(safe_secs, key=lambda x: x.get("rate", 0), reverse=True)[:3]
    worst = sorted(safe_secs, key=lambda x: x.get("rate", 0))[:1]

    # ★ここがエラー箇所：フォーマット指定子のスペースを排除
    #   OK: {total:,.0f} / {pnl:+,.0f} / {wr:.1f}
    msg = (
        f"総資産{total:,.0f}円、含み損益{pnl:+,.0f}円（勝率{wr:.1f}%）。"
        f"現金{cash:,.0f}円。"
    )

    if top:
        msg += f" 今週強いセクターは「{', '.join(str(s.get('sector','')) for s in top)}」。"
    if worst:
        msg += f" 伸び悩みは「{worst[0].get('sector','')}」。"
    if realized != 0:
        msg += f" 当月実現損益は{realized:+,.0f}円。"

    actions: List[str] = []
    if wr >= 60 and pnl > 0:
        actions.append("含み益上位から一部利確を検討（勝率60%超）")
    # 0除算ガード
    total_nonzero = total if total > 0 else 1.0
    if (cash / total_nonzero) < 0.10:
        actions.append("現金比率が10%未満。調整余地あり")
    if top and float(top[0].get("rate", 0)) > 3:
        actions.append(f"好調セクター「{top[0].get('sector','')}」で押し目待ち")

    if not actions:
        actions.append("分散維持・ルール順守で様子見")

    return msg, actions