# portfolio/ml/train.py
import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from portfolio.models_advisor import AdviceItem
from portfolio.ml.feature_builder import build_features, label_from_outcome

MODEL_PATH = "/var/models/advisor.pkl"

def fit_model():
    """提案履歴から学習"""
    X, y = [], []
    for it in AdviceItem.objects.select_related("session").all():
        x = build_features(it.session.context_json.get("kpis", {}), it.session.context_json.get("sectors", []))
        X.append(list(x.values()))
        y.append(label_from_outcome(it))
    if not X:
        return None
    clf = GradientBoostingClassifier()
    clf.fit(np.array(X), np.array(y))
    joblib.dump({"model": clf, "columns": list(build_features({}, []).keys())}, MODEL_PATH)
    return MODEL_PATH

def predict_proba(feat: dict) -> float:
    """モデルによる成功確率予測"""
    try:
        bundle = joblib.load(MODEL_PATH)
        model = bundle["model"]
        cols = bundle["columns"]
        X = np.array([[feat.get(c, 0) for c in cols]])
        return float(model.predict_proba(X)[0, 1])
    except Exception:
        return 0.5