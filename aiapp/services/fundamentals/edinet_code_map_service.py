# aiapp/services/fundamentals/edinet_code_map_service.py
# -*- coding: utf-8 -*-
"""
EDINET集計（edinet_agg_YYYYMMDD.json）を「銘柄コード(4桁)」へ寄せるサービス。

ルートBの方針:
- まず“マップがある分だけ”確実に紐付ける（無い分は unknown に落として落ちない設計）
- マップは外部依存なので、ここでは「ローカルJSON」を読むだけにする
  （次のステップで JPX/EDINETのマスタ取り込みを作る）

入力:
- media/aiapp/fundamentals/daily/YYYYMMDD/edinet_agg_YYYYMMDD.json
- media/aiapp/fundamentals/master/edinet_code_map.json

edinet_code_map.json 例:
{
  "meta": {"source":"manual", "updated_at":"2026-01-14"},
  "map": {
     "E00001": {"ticker":"7203", "name":"トヨタ自動車"},
     "E00002": {"ticker":"9984", "name":"ソフトバンクグループ"}
  }
}

出力:
media/aiapp/fundamentals/daily/YYYYMMDD/edinet_by_ticker_YYYYMMDD.json

中身:
{
  "meta": {...},
  "by_ticker": {
     "7203": {
        "ticker":"7203",
        "name":"...",
        "doc_count": ...,
        "doc_type_counts": {...},
        "flags": {...},
        "edinet_codes": ["E00001", ...]
     },
     ...
  },
  "unknown_edinet": { ... }  # マップが無くて紐付けできない分
}
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

JST = timezone(timedelta(hours=9))

DAILY_DIR = Path("media/aiapp/fundamentals/daily")
MASTER_DIR = Path("media/aiapp/fundamentals/master")
MASTER_DIR.mkdir(parents=True, exist_ok=True)


def _safe_json_dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)


def _ymd_from_any(s: str) -> Optional[str]:
    if not s:
        return None
    x = str(s).strip()
    if not x:
        return None
    x = x.replace("/", "-").replace(".", "-")
    if len(x) == 8 and x.isdigit():
        return x
    parts = x.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        y = int(parts[0]); m = int(parts[1]); d = int(parts[2])
        return f"{y:04d}{m:02d}{d:02d}"
    return None


def _read_json(path: Path) -> Tuple[Dict[str, Any], Optional[str]]:
    if not path.exists():
        return {}, f"not_found:{path}"
    try:
        j = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(j, dict):
            return j, None
        return {}, f"not_dict:{path}"
    except Exception as ex:
        return {}, f"invalid_json:{path}:{ex}"


def _normalize_ticker(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    # "7203.T" -> "7203"
    if s.endswith(".T"):
        s = s[:-2]
    # 4桁以外も来うるので、ここでは文字列として保持（後で絞る）
    return s


def load_edinet_code_map() -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]], Optional[str]]:
    """
    returns: (meta, map, err)
    """
    path = MASTER_DIR / "edinet_code_map.json"
    j, err = _read_json(path)
    if err:
        return {}, {}, err

    meta = j.get("meta") if isinstance(j.get("meta"), dict) else {}
    mp = j.get("map") if isinstance(j.get("map"), dict) else {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in mp.items():
        if not isinstance(v, dict):
            continue
        edinet = str(k).strip()
        if not edinet:
            continue
        t = _normalize_ticker(v.get("ticker"))
        if not t:
            continue
        out[edinet] = {
            "ticker": t,
            "name": (str(v.get("name")).strip() if v.get("name") else None),
        }
    return meta, out, None


def build_by_ticker_for_day(day: str, *, overwrite: bool = False) -> Dict[str, Any]:
    day_norm = _ymd_from_any(day) or str(day).strip()
    in_path = DAILY_DIR / day_norm / f"edinet_agg_{day_norm}.json"
    out_path = DAILY_DIR / day_norm / f"edinet_by_ticker_{day_norm}.json"

    if out_path.exists() and not overwrite:
        return {"ok": True, "note": "cache_hit", "path": str(out_path)}

    agg_j, err = _read_json(in_path)
    if err:
        payload = {
            "meta": {
                "day": day_norm,
                "built_at": datetime.now(JST).isoformat(timespec="seconds"),
                "error": "agg_missing_or_invalid",
                "detail": err,
            },
            "by_ticker": {},
            "unknown_edinet": {},
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_safe_json_dump(payload), encoding="utf-8")
        return {"ok": False, "path": str(out_path), "error": payload["meta"]}

    agg_by_edinet = agg_j.get("agg_by_edinet")
    if not isinstance(agg_by_edinet, dict):
        agg_by_edinet = {}

    map_meta, mp, map_err = load_edinet_code_map()

    by_ticker: Dict[str, Any] = {}
    unknown_edinet: Dict[str, Any] = {}

    for edinet_code, rec in agg_by_edinet.items():
        if not isinstance(rec, dict):
            continue

        m = mp.get(edinet_code)
        if not m:
            unknown_edinet[edinet_code] = rec
            continue

        ticker = _normalize_ticker(m.get("ticker"))
        if not ticker:
            unknown_edinet[edinet_code] = rec
            continue

        if ticker not in by_ticker:
            by_ticker[ticker] = {
                "ticker": ticker,
                "name": m.get("name") or rec.get("filer_name"),
                "doc_count": 0,
                "doc_type_counts": {},
                "flags": {
                    "has_securities_report": False,
                    "has_quarterly_report": False,
                    "has_extra_report": False,
                    "has_amendment": False,
                },
                "latest_submit_datetime": None,
                "edinet_codes": [],
            }

        t = by_ticker[ticker]
        t["edinet_codes"].append(edinet_code)

        # counts
        t["doc_count"] = int(t.get("doc_count") or 0) + int(rec.get("doc_count") or 0)

        # doc_type_counts merge
        dtc = rec.get("doc_type_counts")
        if isinstance(dtc, dict):
            dst = t.get("doc_type_counts")
            if not isinstance(dst, dict):
                dst = {}
                t["doc_type_counts"] = dst
            for k, v in dtc.items():
                try:
                    dst[str(k)] = int(dst.get(str(k)) or 0) + int(v)
                except Exception:
                    continue

        # flags merge (OR)
        flags = rec.get("flags")
        if isinstance(flags, dict):
            for fk in ("has_securities_report", "has_quarterly_report", "has_extra_report", "has_amendment"):
                if bool(flags.get(fk)) is True:
                    t["flags"][fk] = True

        # latest datetime (string compare tolerant)
        a = t.get("latest_submit_datetime")
        b = rec.get("latest_submit_datetime")
        if not a:
            t["latest_submit_datetime"] = b
        elif b:
            try:
                da = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
                db = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
                if db > da:
                    t["latest_submit_datetime"] = b
            except Exception:
                # fallback: longer string wins
                if len(str(b)) >= len(str(a)):
                    t["latest_submit_datetime"] = b

    payload = {
        "meta": {
            "day": day_norm,
            "built_at": datetime.now(JST).isoformat(timespec="seconds"),
            "source_agg": str(in_path),
            "map_meta": map_meta,
            "map_error": map_err,
            "ticker_count": len(by_ticker),
            "unknown_edinet_count": len(unknown_edinet),
        },
        "by_ticker": by_ticker,
        "unknown_edinet": unknown_edinet,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_safe_json_dump(payload), encoding="utf-8")
    return {"ok": True, "path": str(out_path), "meta": payload["meta"]}