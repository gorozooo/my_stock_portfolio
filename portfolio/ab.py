from __future__ import annotations
import random
from typing import Tuple
from django.utils.crypto import get_random_string
from .models_ab import ABExperiment, ABAssignment, ABEvent

COOKIE_KEY = "abid"

def _get_identity(request, response=None) -> str:
    # 認証ユーザがあれば user.id を、無ければ Cookie を使う
    if getattr(request, "user", None) and request.user.is_authenticated:
        return f"user:{request.user.id}"
    cid = request.COOKIES.get(COOKIE_KEY)
    if not cid:
        cid = f"anon:{get_random_string(24)}"
        if response is not None:
            # Set cookie for 1 year
            response.set_cookie(COOKIE_KEY, cid, max_age=3600*24*365, samesite="Lax")
    return cid

def assign_variant(request, response, exp_key: str, weights=None) -> Tuple[str, str]:
    """
    例: assign_variant(request, response, "ai_advisor_layout", weights={"A":0.5,"B":0.5})
    戻り: (identity, variant)
    """
    identity = _get_identity(request, response)
    exp = ABExperiment.objects.filter(key=exp_key, enabled=True).first()
    if not exp:
        return identity, "A"  # デフォルトA
    # 既存割当
    a = ABAssignment.objects.filter(experiment=exp, identity=identity).first()
    if a:
        return identity, a.variant
    # 新規割当
    variants = exp.variants or ["A","B"]
    if weights:
        vs, ps = zip(*[(k, float(weights.get(k, 0))) for k in variants])
        s = sum(ps) or 1.0
        ps = [p/s for p in ps]
        # 重み付き選択
        r = random.random()
        cum = 0.0
        chosen = vs[-1]
        for v, p in zip(vs, ps):
            cum += p
            if r <= cum:
                chosen = v; break
        variant = chosen
    else:
        variant = random.choice(variants)
    ABAssignment.objects.create(experiment=exp, identity=identity, variant=variant)
    return identity, variant

def log_event(exp_key: str, identity: str, variant: str, name: str, meta=None):
    exp = ABExperiment.objects.filter(key=exp_key).first()
    ABEvent.objects.create(
        experiment=exp, identity=identity, variant=variant, name=name, meta=meta or {}
    )