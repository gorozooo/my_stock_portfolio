# portfolio/services/model_resolver.py
from __future__ import annotations
from typing import Dict, Optional, Tuple, List
from django.apps import apps
from django.db.models import Model

# 候補名（フィールド名ゆらぎを吸収）
PRICE_CANDS   = ["current_price", "price", "last_price", "close_price"]
UNIT_CANDS    = ["unit_price", "buy_price", "cost_price", "average_price", "avg_price", "avg_cost"]
SHARES_CANDS  = ["shares", "quantity", "qty", "amount", "units"]
SECTOR_CANDS  = ["sector", "sector_name", "industry", "category"]
CASH_AMT_CANDS= ["amount", "balance", "value"]

def _has_any_field(m: Model, names: List[str]) -> Optional[str]:
    fields = {f.name for f in m._meta.get_fields() if hasattr(f, "name")}
    for n in names:
        if n in fields:
            return n
    return None

def resolve_models(app_label: str = "portfolio") -> Dict[str, Dict]:
    """
    そのアプリ内のモデルを走査して、保有/配当/実現/現金を自動検出。
    戻り値:
      {
        "holding": {"model": ModelClass, "price":"...", "unit":"...", "shares":"...", "sector":"..."},
        "dividend": {"model": ModelClass, "amount":"...", "date":"..."},
        "realized": {"model": ModelClass, "amount":"...", "date":"..."},
        "cash": {"model": ModelClass, "amount":"..."},
      }
    """
    found: Dict[str, Dict] = {}
    try:
        appcfg = apps.get_app_config(app_label)
    except LookupError:
        return found

    for m in appcfg.get_models():
        name = m.__name__.lower()

        # 保有候補: price×shares×unit（いずれも1つ以上見つかる）
        p = _has_any_field(m, PRICE_CANDS)
        u = _has_any_field(m, UNIT_CANDS)
        s = _has_any_field(m, SHARES_CANDS)
        if (p and s) or (p and u):
            # モデル名に stock/holding が入ってたら最優先
            score = 0
            if "stock" in name or "holding" in name or "position" in name:
                score += 2
            # セクターがあれば加点
            sec = _has_any_field(m, SECTOR_CANDS)
            score += 1 if sec else 0
            # 高スコアなら採用（単純置換：最初に見つけた高得点を採用）
            if "holding" not in found or score >= found["holding"].get("_score", -1):
                found["holding"] = {"model": m, "price": p, "unit": u, "shares": s, "sector": sec or None, "_score": score}
            continue

        # 配当候補
        if "dividend" in name or "distribution" in name:
            amt = _has_any_field(m, CASH_AMT_CANDS) or "amount"
            date = (_has_any_field(m, ["received_date","pay_date","date"]) or "date")
            found["dividend"] = {"model": m, "amount": amt, "date": date}
            continue

        # 実現損益候補
        if "realized" in name or "pnl" in name or "trade" in name or "close" in name:
            amt = _has_any_field(m, ["profit_amount","profit","pnl","realized","amount"]) or "profit"
            date = (_has_any_field(m, ["close_date","date","settled_at"]) or "date")
            found["realized"] = {"model": m, "amount": amt, "date": date}
            continue

        # 現金候補
        if "cash" in name or "balance" in name or "wallet" in name:
            amt = _has_any_field(m, CASH_AMT_CANDS) or "amount"
            found["cash"] = {"model": m, "amount": amt}
            continue

    # スコアのメタキーは外す
    if "holding" in found:
        found["holding"].pop("_score", None)
    return found