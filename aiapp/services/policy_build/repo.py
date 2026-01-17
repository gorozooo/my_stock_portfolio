# aiapp/services/policy_build/repo.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

POLICY_LATEST = Path("media/aiapp/policy/latest_policy.json")


@dataclass
class PolicySnapshot:
    asof: str
    sector_rows: Dict[str, Any]
    meta: Dict[str, Any]


def _safe_float(x) -> float:
    try:
        if x is None:
            return 0.0
        v = float(x)
        if v != v:
            return 0.0
        return float(v)
    except Exception:
        return 0.0


def _components_compact_from_any(d: Any) -> Dict[str, float]:
    """
    ★互換：policy_build 側の出力が
      - components_full: fx/risk/us_rates/jp_rates
      - components: fx/risk/rates
    のどちらでも、最終的に meta["components"] が fx/risk/rates になるように寄せる。

    ここは “読む側(repo)” なので、壊れてても落とさず 0 埋め。
    """
    if not isinstance(d, dict):
        return {"fx": 0.0, "risk": 0.0, "rates": 0.0}

    fx = _safe_float(d.get("fx"))
    risk = _safe_float(d.get("risk"))

    # すでに rates があるならそれを優先
    if "rates" in d:
        rates = _safe_float(d.get("rates"))
        return {"fx": fx, "risk": risk, "rates": rates}

    # 古い/別形式: jp_rates と us_rates から rates を合成（k=0.5固定）
    us_rates = _safe_float(d.get("us_rates"))
    jp_rates = _safe_float(d.get("jp_rates"))
    rates = jp_rates + (us_rates * 0.5)
    return {"fx": fx, "risk": risk, "rates": rates}


def _normalize_loaded_payload(j: Dict[str, Any]) -> Dict[str, Any]:
    """
    読み込んだ JSON を “hybrid_adjust_service が壊れない形” に寄せる。
    - top-level meta["components"] は必ず fx/risk/rates を持つ
    - sector_rows[*]["meta"]["components"] も同様
    - components_full があれば残す（触らない）
    """
    meta = j.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        j["meta"] = meta

    # top-level components の補完
    if "components" in meta and isinstance(meta.get("components"), dict):
        meta["components"] = _components_compact_from_any(meta.get("components"))
    else:
        # components_full があるならそこから作る
        cf = meta.get("components_full")
        if isinstance(cf, dict):
            meta["components"] = _components_compact_from_any(cf)
        else:
            meta["components"] = {"fx": 0.0, "risk": 0.0, "rates": 0.0}

    # sector_rows meta の補完
    rows = j.get("sector_rows")
    if isinstance(rows, dict):
        for _, row in rows.items():
            if not isinstance(row, dict):
                continue
            rm = row.get("meta")
            if not isinstance(rm, dict):
                rm = {}
                row["meta"] = rm

            if "components" in rm and isinstance(rm.get("components"), dict):
                rm["components"] = _components_compact_from_any(rm.get("components"))
            else:
                cf = rm.get("components_full")
                if isinstance(cf, dict):
                    rm["components"] = _components_compact_from_any(cf)
                else:
                    # row側に full が無い場合：top-level を流用（無難）
                    rm["components"] = dict(meta.get("components") or {"fx": 0.0, "risk": 0.0, "rates": 0.0})

    return j


def load_policy_snapshot() -> PolicySnapshot:
    if not POLICY_LATEST.exists():
        return PolicySnapshot(asof="1970-01-01", sector_rows={}, meta={"error": "missing", "source": str(POLICY_LATEST)})

    try:
        j = json.loads(POLICY_LATEST.read_text(encoding="utf-8"))
    except Exception:
        return PolicySnapshot(asof="1970-01-01", sector_rows={}, meta={"error": "invalid_json", "source": str(POLICY_LATEST)})

    if not isinstance(j, dict):
        return PolicySnapshot(asof="1970-01-01", sector_rows={}, meta={"error": "invalid_format", "source": str(POLICY_LATEST)})

    # ★互換正規化（読む側で守る）
    j = _normalize_loaded_payload(j)

    asof = str(j.get("asof") or "1970-01-01")
    sector_rows = j.get("sector_rows") if isinstance(j.get("sector_rows"), dict) else {}
    meta = j.get("meta") if isinstance(j.get("meta"), dict) else {}
    return PolicySnapshot(asof=asof, sector_rows=sector_rows, meta=meta)