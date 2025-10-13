# portfolio/services/advisor_rules.py
def score_rules(kpis: dict, sectors: list[dict]) -> list[dict]:
    """数値指標からルールベースの提案を生成"""
    items = []

    # ROI乖離
    gap = float(kpis.get("roi_gap_abs") or 0)
    if gap >= 20:
        items.append({
            "kind": "REBALANCE",
            "message": f"評価ROIと現金ROIの乖離が {gap:.1f}pt。ポジション整理を検討。",
            "reasons": [{"metric": "roi_gap_abs", "value": gap, "th": 20}],
            "score": min(1.0, gap / 100.0 + 0.2)
        })

    # 信用比率
    mr = float(kpis.get("margin_ratio_pct") or 0)
    if mr >= 60:
        items.append({
            "kind": "REDUCE_MARGIN",
            "message": f"信用比率 {mr:.1f}%（60%超）。信用圧縮でボラリスク低減。",
            "reasons": [{"metric": "margin_ratio_pct", "value": mr, "th": 60}],
            "score": min(1.0, 0.5 + (mr - 60) / 80)
        })

    # 流動性
    liq = float(kpis.get("liquidity_rate_pct") or 0)
    if liq < 50:
        items.append({
            "kind": "ADD_CASH",
            "message": f"流動性 {liq:.1f}% と低め。現金化余地を検討。",
            "reasons": [{"metric": "liquidity_rate_pct", "value": liq, "th": 50}],
            "score": min(0.9, (50 - liq) / 100 + 0.3)
        })

    # セクター偏在
    if sectors:
        total_mv = sum(s["mv"] for s in sectors) or 1
        top = sectors[0]
        top_share = top["mv"] / total_mv * 100
        if top_share >= 45:
            items.append({
                "kind": "REBALANCE",
                "message": f"セクター偏在（{top['sector']} {top_share:.1f}%）。分散を検討。",
                "reasons": [{"metric": "sector_top_share", "value": top_share, "th": 45}],
                "score": 0.6
            })

    return sorted(items, key=lambda x: x["score"], reverse=True)