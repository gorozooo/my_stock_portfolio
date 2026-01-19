# aiapp/services/policy_news/build_service.py
# -*- coding: utf-8 -*-
"""
policy_news（ニュース/政策/社会情勢）の生成サービス（build層）。

やること:
- input_policy_news.json を読む（無ければ空）
- items を schema(PolicyNewsItem) に合わせて復元
- sector_delta が無い/全0 の場合、sectors + factors から自動生成（B案）
- latest_policy_news.json / stamp を出力

注意:
- PolicyNewsItem のスキーマは今後揺れる可能性があるため、
  __init__ が受け取れるキーだけを自動選別して渡す。
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schema import PolicyNewsItem, PolicyNewsSnapshot
from .repo import dump_policy_news_snapshot, load_policy_news_snapshot
from .settings import JST, POLICY_NEWS_DIR, LATEST_POLICY_NEWS, dt_now_stamp

INPUT_POLICY_NEWS = POLICY_NEWS_DIR / "input_policy_news.json"

# 自動 sector_delta 生成の強さ（小さめ）
AUTO_SECTOR_K = 0.30
# 1セクターあたり上限（暴れ防止）
AUTO_SECTOR_CLAMP = (-1.0, 1.0)


def _safe_json_load(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:
            return None
        return v
    except Exception:
        return None


def _clamp(x: float, lo: float, hi: float) -> float:
    try:
        v = float(x)
    except Exception:
        return 0.0
    if v != v:
        return 0.0
    return max(lo, min(hi, v))


def _norm_text(s: Any) -> str:
    return str(s or "").strip()


def _safe_list(v) -> List[Any]:
    return v if isinstance(v, list) else []


def _safe_dict(v) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _sector_delta_all_zero(d: Dict[str, Any]) -> bool:
    if not isinstance(d, dict) or not d:
        return True
    for _, v in d.items():
        fv = _safe_float(v)
        if fv is None:
            continue
        if abs(float(fv)) > 0.0:
            return False
    return True


def _auto_build_sector_delta(*, sectors: List[str], factors: Dict[str, Any]) -> Dict[str, float]:
    """
    sectors + factors から sector_delta を自動生成（均等割り）。
    strength = fx + rates + risk
    per = strength * AUTO_SECTOR_K / n
    """
    secs = [s for s in (str(x).strip() for x in (sectors or [])) if s]
    if not secs:
        return {}

    fx = _safe_float(factors.get("fx")) or 0.0
    rates = _safe_float(factors.get("rates")) or 0.0
    risk = _safe_float(factors.get("risk")) or 0.0

    strength = float(fx) + float(rates) + float(risk)
    n = max(1, len(secs))
    per = (strength * float(AUTO_SECTOR_K)) / float(n)

    lo, hi = AUTO_SECTOR_CLAMP
    per2 = _clamp(per, lo, hi)

    out: Dict[str, float] = {}
    for s in secs:
        out[s] = float(per2)
    return out


def _make_policy_news_item(**kwargs) -> PolicyNewsItem:
    """
    PolicyNewsItem の __init__ が受け取れるキーだけを残して生成する。
    スキーマ揺れ対策。
    """
    sig = inspect.signature(PolicyNewsItem)
    allowed = set(sig.parameters.keys())
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    return PolicyNewsItem(**filtered)


def build_policy_news_snapshot(*, asof: str, source: str = "manual_seed") -> PolicyNewsSnapshot:
    """
    input_policy_news.json から policy_news snapshot を作る。
    - asof は policy_build と揃える
    - sector_delta が空/全0なら、sectors+factors から自動生成（B案）
    """
    POLICY_NEWS_DIR.mkdir(parents=True, exist_ok=True)

    seed = _safe_json_load(INPUT_POLICY_NEWS)

    items_in = seed.get("items")
    items: List[PolicyNewsItem] = []

    if isinstance(items_in, list):
        for d in items_in:
            if not isinstance(d, dict):
                continue

            _id = _norm_text(d.get("id"))
            if not _id:
                continue

            title = _norm_text(d.get("title")) or None
            category = _norm_text(d.get("category")) or "misc"
            reason = _norm_text(d.get("reason")) or None
            src = _norm_text(d.get("source")) or None
            url = _norm_text(d.get("url")) or None

            # sectors / factors（入力に合わせる）
            sectors_raw = _safe_list(d.get("sectors"))
            sectors = [str(x).strip() for x in sectors_raw if str(x).strip()]

            factors_in = _safe_dict(d.get("factors"))
            factors: Dict[str, float] = {}
            for k in ("fx", "rates", "risk"):
                fv = _safe_float(factors_in.get(k))
                if fv is not None:
                    factors[k] = float(fv)

            # sector_delta（任意）
            sector_delta_in = _safe_dict(d.get("sector_delta"))
            sector_delta: Dict[str, float] = {}
            for k, v in sector_delta_in.items():
                kk = _norm_text(k)
                fv = _safe_float(v)
                if kk and fv is not None:
                    sector_delta[kk] = float(fv)

            # B案：sector_delta が空/全0なら自動生成
            if _sector_delta_all_zero(sector_delta):
                auto = _auto_build_sector_delta(sectors=sectors, factors=factors)
                if auto:
                    sector_delta = auto

            it = _make_policy_news_item(
                id=_id,
                category=category,
                title=title,
                sectors=sectors,
                factors=factors,
                sector_delta=sector_delta,
                reason=reason,
                source=src,
                url=url,
            )
            items.append(it)

    meta = seed.get("meta") if isinstance(seed.get("meta"), dict) else {}
    meta2: Dict[str, Any] = dict(meta)
    meta2.update(
        {
            "engine": "policy_news_build",
            "source": source,
            "built_at": datetime.now(JST).isoformat(),
            "input_path": str(INPUT_POLICY_NEWS),
            "auto_sector_k": float(AUTO_SECTOR_K),
            "auto_sector_clamp": list(AUTO_SECTOR_CLAMP),
        }
    )

    snap = PolicyNewsSnapshot(asof=str(asof), items=items, meta=meta2)

    # dump→repo再ロードで factors_sum/sector_sum を repo と同一ロジックで付与
    tmp = dump_policy_news_snapshot(snap)
    s = json.dumps(tmp, ensure_ascii=False, separators=(",", ":"))
    _tmp_path = POLICY_NEWS_DIR / "__tmp_policy_news_build.json"
    _tmp_path.write_text(s, encoding="utf-8")
    snap2 = load_policy_news_snapshot(_tmp_path)
    try:
        _tmp_path.unlink(missing_ok=True)
    except Exception:
        pass

    # build側 meta を優先
    snap2.meta = meta2
    snap2.asof = str(asof)
    return snap2


def emit_policy_news_json(snap: PolicyNewsSnapshot) -> None:
    POLICY_NEWS_DIR.mkdir(parents=True, exist_ok=True)

    payload = dump_policy_news_snapshot(snap)
    s = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    # latest
    LATEST_POLICY_NEWS.write_text(s, encoding="utf-8")

    # stamped
    stamped = POLICY_NEWS_DIR / f"{dt_now_stamp()}_policy_news.json"
    stamped.write_text(s, encoding="utf-8")