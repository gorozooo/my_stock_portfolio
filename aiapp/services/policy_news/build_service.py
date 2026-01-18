# aiapp/services/policy_news/build_service.py
# -*- coding: utf-8 -*-
"""
これは何のファイル？
- policy_news（ニュース/政策/社会情勢）の “生成” を行うサービス（build層）。

今はまず「手動seed / 仮JSON」でも回るように、以下だけ提供する:
- input_policy_news.json を読み（無ければ最小の空で生成）
- asof を外から渡せる（fundamentals由来の日付を揃えるため）
- latest_policy_news.json / stamp を出力

後で拡張する場所:
- ニュース自動取得（RSS/公式/報道）
- LLM要約→分類→impact生成
- セクター影響の学習（A/Bログから更新）
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .schema import PolicyNewsItem, PolicyNewsSnapshot
from .repo import dump_policy_news_snapshot, load_policy_news_snapshot
from .settings import JST, POLICY_NEWS_DIR, LATEST_POLICY_NEWS, dt_now_stamp

INPUT_POLICY_NEWS = POLICY_NEWS_DIR / "input_policy_news.json"


def _safe_json_load(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe_float(x):
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except Exception:
        return None


def _safe_dict(v) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _safe_list(v):
    return v if isinstance(v, list) else []


def _norm_text(s: Any) -> str:
    return str(s or "").strip()


def build_policy_news_snapshot(*, asof: str, source: str = "manual") -> PolicyNewsSnapshot:
    """
    手動seed から policy_news snapshot を作る。
    - asof は policy_build と揃えるために外から渡す
    """
    POLICY_NEWS_DIR.mkdir(parents=True, exist_ok=True)

    seed = _safe_json_load(INPUT_POLICY_NEWS)

    items_in = seed.get("items")
    items: list[PolicyNewsItem] = []

    if isinstance(items_in, list):
        for d in items_in:
            if not isinstance(d, dict):
                continue

            _id = _norm_text(d.get("id"))
            if not _id:
                continue

            # 互換対応:
            # - 新: factors/sectors
            # - 旧: impact/sector_delta
            factors_in = _safe_dict(d.get("factors")) or _safe_dict(d.get("impact"))
            impact: Dict[str, float] = {}
            for k in ("fx", "rates", "risk"):
                fv = _safe_float(factors_in.get(k))
                if fv is not None:
                    impact[k] = float(fv)

            # sector_delta は以下の優先:
            # 1) sector_delta が dict で来ていればそれを採用
            # 2) sectors が list で来ていれば、最低限キーだけ作る（値は0.0）
            sector_delta_in = d.get("sector_delta")
            sector_delta: Dict[str, float] = {}
            if isinstance(sector_delta_in, dict) and sector_delta_in:
                for k, v in sector_delta_in.items():
                    kk = _norm_text(k)
                    fv = _safe_float(v)
                    if kk and fv is not None:
                        sector_delta[kk] = float(fv)
            else:
                sectors_in = d.get("sectors")
                if isinstance(sectors_in, list):
                    for s in sectors_in:
                        ss = _norm_text(s)
                        if ss and ss not in sector_delta:
                            sector_delta[ss] = 0.0

            items.append(
                PolicyNewsItem(
                    id=_id,
                    category=_norm_text(d.get("category")) or "misc",
                    title=_norm_text(d.get("title")) or None,
                    impact=impact,
                    sector_delta=sector_delta,
                    reason=_norm_text(d.get("reason")) or None,
                    source=_norm_text(d.get("source")) or None,
                    url=_norm_text(d.get("url")) or None,
                )
            )

    meta = seed.get("meta") if isinstance(seed.get("meta"), dict) else {}
    meta2: Dict[str, Any] = dict(meta)
    meta2.update(
        {
            "engine": "policy_news_build",
            "source": source,
            "built_at": datetime.now(JST).isoformat(),
            "input_path": str(INPUT_POLICY_NEWS),
        }
    )

    snap = PolicyNewsSnapshot(asof=str(asof), items=items, meta=meta2)

    # 一度 dump → repo で再ロードすると、集計（factors_sum/sector_sum）が確実に付く
    tmp = dump_policy_news_snapshot(snap)
    s = json.dumps(tmp, ensure_ascii=False, separators=(",", ":"))
    POLICY_NEWS_DIR.mkdir(parents=True, exist_ok=True)
    _tmp_path = POLICY_NEWS_DIR / "__tmp_policy_news_build.json"
    _tmp_path.write_text(s, encoding="utf-8")
    snap2 = load_policy_news_snapshot(_tmp_path)
    try:
        _tmp_path.unlink(missing_ok=True)
    except Exception:
        pass

    # meta/asof は build 側を優先
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