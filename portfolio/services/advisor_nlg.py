# portfolio/services/advisor_nlg.py
def make_header_summary(kpis: dict) -> str:
    """AIヘッダー用の短い自然文"""
    total = kpis.get("total_assets", 0)
    unreal = kpis.get("unrealized_pnl", 0)
    roi_eval = kpis.get("roi_eval_pct")
    roi_liq = kpis.get("roi_liquid_pct")
    cash = kpis.get("cash_total", 0)

    sign = "+" if unreal >= 0 else ""
    return (
        f"総資産¥{total:,}、 含み損益{sign}¥{unreal:,}。"
        f"現金¥{cash:,}。 評価ROI{roi_eval:.2f}%／現金ROI{roi_liq:.2f}%。"
    )