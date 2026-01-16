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
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

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


def build_policy_news_snapshot(*, asof: str, source: str = "manual") -> PolicyNewsSnapshot:
    """
    “仮seed” から policy_news snapshot を作る。
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
            _id = str(d.get("id") or "").strip()
            if not _id:
                continue
            items.append(
                PolicyNewsItem(
                    id=_id,
                    category=str(d.get("category") or "misc").strip() or "misc",
                    title=str(d.get("title") or "").strip() or None,
                    impact=d.get("impact") if isinstance(d.get("impact"), dict) else {},
                    sector_delta=d.get("sector_delta") if isinstance(d.get("sector_delta"), dict) else {},
                    reason=str(d.get("reason") or "").strip() or None,
                    source=str(d.get("source") or "").strip() or None,
                    url=str(d.get("url") or "").strip() or None,
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

    # 一度 dump→repoで再ロードすると、集計（factors_sum/sector_sum）が確実に付く
    # （“読む層の集計ロジック”と同一にするため）
    tmp = dump_policy_news_snapshot(snap)
    s = json.dumps(tmp, ensure_ascii=False, separators=(",", ":"))
    # 一時的にメモリでロードする代わりに、repoのロジックに寄せるため軽く復元:
    # （ここはシンプル優先で、repo側の集計に合わせる）
    POLICY_NEWS_DIR.mkdir(parents=True, exist_ok=True)
    _tmp_path = POLICY_NEWS_DIR / "__tmp_policy_news_build.json"
    _tmp_path.write_text(s, encoding="utf-8")
    snap2 = load_policy_news_snapshot(_tmp_path)
    try:
        _tmp_path.unlink(missing_ok=True)  # python3.8+互換
    except Exception:
        pass

    # metaはbuild側が強いので戻す
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