# portfolio/services/advisor.py
from __future__ import annotations
from .advisor_rules import score_rules
from .advisor_nlg import make_header_summary
from ..ml.feature_builder import build_features
from ..ml.train import predict_proba
from ..models_advisor import AdviceSession, AdviceItem


def summarize(kpis: dict, sectors: list[dict]) -> tuple[str, list[dict], int]:
    """
    ホーム画面用：AIアドバイザーの最終統合
    返り値:
      header:str … ヘッダー要約
      items:list[dict] … 提案（id, kind, message, score_final, taken を含む）
      session_id:int … 作成したAdviceSessionのID
    """
    # ルールで素案
    items = score_rules(kpis, sectors)

    # MLで成功確率の補正
    feat = build_features(kpis, sectors)
    prob = predict_proba(feat)
    for it in items:
        it["score_ml"] = prob
        it["score_final"] = round(it["score"] * (0.6 + 0.8 * prob), 3)

    items.sort(key=lambda x: x["score_final"], reverse=True)

    # セッション保存
    sess = AdviceSession.objects.create(context_json={"kpis": kpis, "sectors": sectors})
    saved_items: list[dict] = []
    for it in items:
        row = AdviceItem.objects.create(
            session=sess,
            kind=it["kind"],
            message=it["message"],
            score=it["score_final"],
            reasons=it["reasons"],
        )
        saved_items.append({
            "id": row.id,
            "kind": row.kind,
            "message": row.message,
            "score": row.score,
            "taken": row.taken,
        })

    # 見出し文
    header = make_header_summary(kpis)

    # 提案が無い場合のフォールバック
    if not saved_items:
        saved_items = [{
            "id": 0, "kind": "NONE",
            "message": "現在は提案事項なし",
            "score": 0.0, "taken": False
        }]

    return header, saved_items, sess.id