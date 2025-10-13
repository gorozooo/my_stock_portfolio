# portfolio/services/advisor.py
from __future__ import annotations
from typing import Any, Dict, List, Tuple

Number = float | int | None


# ===== Helpers =====
def _to_i(v: Number) -> int:
    try:
        return int(v or 0)
    except Exception:
        return 0


def _to_f(v: Number) -> float:
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0


def _fmt_yen(v: Number) -> str:
    return f"¥{_to_i(v):,}"


def _posneg(v: Number) -> str:
    x = _to_f(v)
    if x > 0:
        return "＋"
    if x < 0:
        return "－"
    return "±"


def _pick_top_sectors(sectors: List[Dict[str, Any]], top_n: int = 2) -> Tuple[List[str], List[str]]:
    """rateの高低で上位/下位セクター名を返す（'未分類'は除外）"""
    clean = [s for s in (sectors or []) if (s.get("sector") not in ("未分類", "UNCLASSIFIED"))]
    # mv が 0 のカードも除外（ノイズ）
    clean = [s for s in clean if _to_i(s.get("mv")) > 0]
    if not clean:
        return [], []
    sorted_by_rate = sorted(clean, key=lambda x: _to_f(x.get("rate")), reverse=True)
    up = [s["sector"] for s in sorted_by_rate[:top_n]]
    down = [s["sector"] for s in sorted_by_rate[-top_n:]]
    return up, down


# ===== Core =====
def summarize(kpis: Dict[str, Any], sectors: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """
    入力:
      - kpis: viewで作った辞書
      - sectors: [{sector, mv, rate}, ...]  ※'未分類'はこの中に混ざっていてもOK

    出力:
      - ai_note: 1〜3行の要約
      - ai_actions: 箇条書き提案
    """
    # ========== サマリー ==========
    total = _fmt_yen(kpis.get("total_assets"))
    unr   = _fmt_yen(kpis.get("unrealized_pnl"))
    unr_s = _posneg(kpis.get("unrealized_pnl"))
    cash  = _fmt_yen(kpis.get("cash_total"))
    win   = kpis.get("win_ratio")
    win_s = f"（勝率{win:.1f}%）" if isinstance(win, (int, float)) else ""

    up_secs, down_secs = _pick_top_sectors(sectors, top_n=2)
    up_label   = "、".join(up_secs) if up_secs else "（分析中）"
    down_label = "、".join(down_secs) if down_secs else "（分析中）"

    # ROI（2段式）差分
    roi_eval   = kpis.get("roi_eval_pct")
    roi_liquid = kpis.get("roi_liquid_pct")
    roi_gap    = kpis.get("roi_gap_abs")

    # サマリー文（未分類しか無くても必ず出す）
    note_parts: List[str] = []
    note_parts.append(f"総資産{total}、含み損益{unr_s}{unr}{win_s}。現金{cash}。")
    if roi_eval is not None and roi_liquid is not None:
        note_parts.append(f"評価ROI{roi_eval:.2f}%／現金ROI{roi_liquid:.2f}%。")
    # セクターが拾えたら付与
    if up_secs:
        note_parts.append(f"直近強いセクターは「{up_label}」。")
    if down_secs:
        note_parts.append(f"伸び悩みは「{down_label}」。")

    ai_note = " ".join(note_parts).strip()

    # ========== 提案（フォールバックつき） ==========
    acts: List[str] = []

    # 1) ROI 乖離
    if isinstance(roi_gap, (int, float)) and roi_gap >= 20.0:
        acts.append(f"評価ROIと現金ROIの乖離が{roi_gap:.1f}pt。評価と実際の差が大きい。ポジション整理を検討。")

    # 2) 含み益上位の一部利確（勝率しきい値）
    if isinstance(win, (int, float)) and win >= 60.0 and _to_i(kpis.get("unrealized_pnl")) > 0:
        acts.append("含み益上位から一部利確を検討（勝率60%超）。")

    # 3) 現金・流動性チェック
    liq = kpis.get("liquidity_rate_pct")
    if isinstance(liq, (int, float)) and liq < 50.0:
        acts.append(f"流動性が{liq:.1f}%と低め。現金化余地の確保を検討。")

    # 4) 信用比率
    margin_ratio = kpis.get("margin_ratio_pct")
    if isinstance(margin_ratio, (int, float)) and margin_ratio >= 60.0:
        acts.append(f"信用比率が{margin_ratio:.1f}%（60%超）。余力とボラに注意。")

    # 5) 強いセクター押し目待ち
    if up_secs:
        acts.append(f"好調セクター「{up_label}」で押し目狙い。")

    # フォールバック（何も出なければ最低1つは返す）
    if not ai_note:
        ai_note = "市場状況を解析中です。直近データが揃い次第、要約と提案を更新します。"
    if not acts:
        acts = ["直近のデータが少ないため具体提案は控えます。流動性と信用比率を定期確認。"]

    return ai_note, acts