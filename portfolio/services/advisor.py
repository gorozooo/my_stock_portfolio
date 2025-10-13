# portfolio/services/advisor.py
from __future__ import annotations
from typing import Tuple, List, Dict, Any

Number = int | float | None


def _pct(v: Number) -> str:
    if v is None:
        return "--"
    return f"{v:.1f}%" if abs(v) < 100 else f"{v:.0f}%"


def summarize(kpis: Dict[str, Any], sectors: List[Dict[str, Any]]) -> tuple[str, List[str]]:
    """
    入力: kpis (viewsで作った辞書), sectors([{sector, mv, rate}...])
    出力: (ai_note 一文, ai_actions [やること箇条書き])
    目的: 単なる数値列挙ではなく、「所見」と「具体提案」を返す
    """

    # ---- 取り出し ----
    roi_eval: Number   = kpis.get("roi_eval_pct")
    roi_liq: Number    = kpis.get("roi_liquid_pct")
    roi_gap: Number    = kpis.get("roi_gap_abs")
    liq_rate: Number   = kpis.get("liquidity_rate_pct", 0.0)    # 即時現金化/総資産
    margin_ratio: Number = kpis.get("margin_ratio_pct", 0.0)    # 信用比率
    unreal: int        = int(kpis.get("unrealized_pnl", 0))
    realized_m: int    = int(kpis.get("realized_month", 0))
    realized_c: int    = int(kpis.get("realized_cum", 0))
    cash_total: int    = int(kpis.get("cash_total", 0))
    margin_unreal: int = int(kpis.get("margin_unrealized", 0))

    # セクター集中度
    total_mv = sum(int(s.get("mv", 0)) for s in sectors) or 1
    top_sector = sectors[0]["sector"] if sectors else "未分類"
    top_share = (sectors[0]["mv"] / total_mv * 100.0) if sectors else 0.0

    # ---- 所見（ヘッドライン） ----
    stance = []
    # リスクサマリ
    if roi_gap is not None and roi_gap >= 20:
        stance.append("評価と実勢の乖離が大きい")
    if liq_rate is not None and liq_rate < 50:
        stance.append("流動性が低め")
    if margin_ratio is not None and margin_ratio >= 60:
        stance.append("信用比率高め")
    if not stance:
        stance.append("バランス良好")

    # 方向感
    if unreal >= 0 and realized_m >= 0:
        tone = "含み益を維持しつつ収益確保中"
    elif unreal >= 0 and realized_m < 0:
        tone = "含み益はあるが直近の実現はマイナス"
    elif unreal < 0 and realized_m >= 0:
        tone = "含み損だが実現で巻き返し中"
    else:
        tone = "含み・実現とも逆風"

    # ヘッドライン文章
    note = (
        f"{'・'.join(stance)}。{tone}。"
        f" 現金余力¥{cash_total:,}、トップ構成は「{top_sector}」{top_share:.0f}%。"
        f" 評価ROI{_pct(roi_eval)}／現金ROI{_pct(roi_liq)}。"
    )

    # ---- アクション（最大4件） ----
    actions: List[str] = []

    # 1) 乖離が大きい
    if roi_gap is not None and roi_gap >= 20:
        actions.append(f"評価と現金ROIの乖離が {roi_gap:.1f}pt。利益の重い銘柄・含み損の信用を優先的に整理。")

    # 2) 流動性
    if liq_rate is not None and liq_rate < 50:
        actions.append("流動性<50%。一部利確 or 現引き/返済で現金比率を引き上げ。")

    # 3) 信用比率
    if margin_ratio is not None and margin_ratio >= 60:
        if margin_unreal < 0:
            actions.append("信用含み損あり。リスクの高い建玉から段階的に縮小。")
        else:
            actions.append("信用比率60%以上。イベント前は玉を軽くしてボラ対応。")

    # 4) セクター集中
    if top_share >= 45:
        actions.append(f"セクター集中（{top_sector} {top_share:.0f}%）。相関の低いセクターに分散。")

    # 5) 含み益/損の定石
    if unreal > 0 and realized_m >= 0:
        actions.append("含み益上位から部分利確→押し目の強い銘柄へ乗り換え。")
    elif unreal < 0 and realized_m <= 0:
        actions.append("含み損は“理由が薄い”銘柄から損切りを検討。")

    # 6) 実現の補足
    if realized_c > 0 and realized_m > 0:
        actions.append("今月も実現益が出ているため、勝ちパターンを継続。損小利大を徹底。")

    # ユニーク化 & 上位4件
    seen = set()
    uniq_actions = []
    for a in actions:
        if a not in seen:
            uniq_actions.append(a)
            seen.add(a)
        if len(uniq_actions) >= 4:
            break

    # 空の場合のフォールバック
    if not uniq_actions:
        uniq_actions = ["現状は様子見でOK。イベント前後のみポジションサイズを調整。"]

    return note, uniq_actions