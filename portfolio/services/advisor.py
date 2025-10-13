# portfolio/services/advisor.py
from .advisor_rules import score_rules
from .advisor_nlg import make_header_summary
from ..ml.feature_builder import build_features
from ..ml.train import predict_proba
from ..models_advisor import AdviceSession, AdviceItem

def summarize(kpis: dict, sectors: list[dict]) -> tuple[str, list[str]]:
    """AIアドバイザーの最終統合"""
    items = score_rules(kpis, sectors)
    feat = build_features(kpis, sectors)
    prob = predict_proba(feat)

    for it in items:
        it["score_ml"] = prob
        it["score_final"] = round(it["score"] * (0.6 + 0.8 * prob), 3)

    items.sort(key=lambda x: x["score_final"], reverse=True)

    sess = AdviceSession.objects.create(context_json={"kpis": kpis, "sectors": sectors})
    for it in items:
        AdviceItem.objects.create(
            session=sess,
            kind=it["kind"],
            message=it["message"],
            score=it["score_final"],
            reasons=it["reasons"]
        )

    header = make_header_summary(kpis)
    lines = [i["message"] for i in items[:4]] or ["現在は提案事項なし"]
    return header, lines