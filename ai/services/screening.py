from typing import List
from ai.domain.entities import Candidate, TrendTriple, PriceTargets, QuantityPlan
from ai.infra.repositories import fetch_top_trend_candidates, fetch_account_caps
from ai.services.scoring import compute_score, compute_stars
from ai.services.quantity import propose_quantity

def generate_top10_candidates() -> List[Candidate]:
    raw = fetch_top_trend_candidates(limit=40)
    caps = fetch_account_caps()

    cands: List[Candidate] = []
    for f in raw:
        price = float(f.get('price', 2000.0))
        score = compute_score(f)
        stars = compute_stars(f)

        # 価格目安：簡易（後でピボット/MA/レジサポへ置換）
        entry = round(price, 1)
        tp = round(price * 1.05, 1)   # +5%
        sl = round(price * 0.97, 1)   # -3%

        qty = propose_quantity(entry, caps)
        cand = Candidate(
            name=f.get('name','銘柄'),
            code=f.get('code','0000'),
            sector=f.get('sector','不明'),
            score=score,
            stars=stars,
            trend=TrendTriple(
                d=f.get('trend_d','flat'),
                w=f.get('trend_w','flat'),
                m=f.get('trend_m','flat')
            ),
            reasons=[
                '5>20MA上抜け'      if f.get('trend_d')=='up' else '20MA近辺で横ばい',
                '相対強度が高い'    if f.get('strength',0)>1.0 else '相対強度は平均的',
                '出来高が増加'      if f.get('vol_boost',1)>1.3 else '出来高は平常',
                '週足の方向性が良好' if f.get('trend_w')=='up' else '週足は様子見',
                '（懸念）イベント接近の可能性あり'
            ],
            prices=PriceTargets(entry=entry, tp=tp, sl=sl),
            qty=QuantityPlan(**qty),
        )
        cands.append(cand)

    # スコア降順で上位10件
    cands.sort(key=lambda x: x.score, reverse=True)
    return cands[:10]