# portfolio/ml/feature_builder.py
def build_features(kpis: dict, sectors: list[dict]) -> dict:
    """KPI＋セクター情報を特徴量化"""
    feat = {
        "roi_gap_abs": float(kpis.get("roi_gap_abs") or 0),
        "margin_ratio_pct": float(kpis.get("margin_ratio_pct") or 0),
        "liquidity_rate_pct": float(kpis.get("liquidity_rate_pct") or 0),
        "unrealized_pnl": float(kpis.get("unrealized_pnl") or 0),
        "realized_month": float(kpis.get("realized_month") or 0),
        "win_ratio": float(kpis.get("win_ratio") or 0),
        "total_assets": float(kpis.get("total_assets") or 0),
    }

    if sectors:
        total_mv = sum(s["mv"] for s in sectors) or 1
        top_share = sectors[0]["mv"] / total_mv
    else:
        top_share = 0.0

    feat["sector_top_share"] = top_share
    return feat


def label_from_outcome(item) -> int:
    """提案採用後のROI改善で成果をラベル化"""
    y = item.outcome or {}
    return 1 if y.get("liquid_roi_delta", 0) >= 5.0 and item.taken else 0