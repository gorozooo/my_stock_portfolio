# aiapp/services/fundamentals/edinet_parse_service.py
# -*- coding: utf-8 -*-
"""
EDINETの「提出書類一覧（documents.json）」の raw キャッシュを解析して、
“ファンダメンタル特徴量の元” になる集計を作る（まだ銘柄コード紐付けはしない）。

目的（ルートBのB-2: parse -> daily aggregates）:
- まずは raw の documents 一覧から「どんな提出がどれだけ出たか」を安定して数える
- 銘柄コード(4桁)への紐付けは次段（edinetコード↔証券コードマップが必要なので分離）

出力（例）:
media/aiapp/fundamentals/daily/20260114/edinet_agg_20260114.json

中身（例）:
{
  "meta": {...},
  "agg_by_edinet": {
     "E00001": {
        "doc_count": 3,
        "doc_type_counts": {"120":2, "140":1},
        "sec_code": "XXXX",  # EDINETが返す場合のみ
        "jcn": "...",        # 法人番号が返る場合のみ
        "latest_submit_datetime": "...",
        "flags": {"has_securities_report": true, ...}
     },
     ...
  }
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

JST = timezone(timedelta(hours=9))

RAW_DIR = Path("media/aiapp/fundamentals/raw")
DAILY_DIR = Path("media/aiapp/fundamentals/daily")
DAILY_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ParseResult:
    ok: bool
    saved_path: Optional[str]
    meta: Dict[str, Any]


def _safe_json_dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _ymd_from_any(s: str) -> Optional[str]:
    """
    "2026-01-14" / "20260114" / "2026/01/14" などを 20260114 に正規化。
    """
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
        y = int(parts[0])
        m = int(parts[1])
        d = int(parts[2])
        return f"{y:04d}{m:02d}{d:02d}"
    return None


def _read_raw_documents(day: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    raw の保存形式:
      { "meta": {...}, "data": <EDINET documents.json のJSON> }
    """
    day = str(day)
    path = RAW_DIR / day / f"edinet_documents_{day}.json"
    if not path.exists():
        return {}, {"error": "raw_not_found", "path": str(path)}

    try:
        j = json.loads(path.read_text(encoding="utf-8"))
    except Exception as ex:
        return {}, {"error": "raw_invalid_json", "detail": str(ex), "path": str(path)}

    meta = j.get("meta") if isinstance(j, dict) else {}
    data = j.get("data") if isinstance(j, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    if not isinstance(data, dict):
        data = {}

    return meta, data


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(v).strip()
    except Exception:
        return ""


def _safe_bool(v: Any) -> bool:
    return bool(v) is True


def _max_dt(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """
    ISOっぽい日時文字列の比較。壊れてたら雑に優先。
    """
    if not a:
        return b
    if not b:
        return a
    try:
        da = datetime.fromisoformat(a.replace("Z", "+00:00"))
        db = datetime.fromisoformat(b.replace("Z", "+00:00"))
        return a if da >= db else b
    except Exception:
        # パースできない場合は長い方を新しめ扱い（雑だけど落ちない）
        return a if len(a) >= len(b) else b


def parse_edinet_documents_day(
    day: str,
    *,
    overwrite: bool = False,
) -> ParseResult:
    """
    day=YYYYMMDD の raw を読んで集計して daily に出す。
    """
    day_norm = _ymd_from_any(day) or str(day).strip()
    out_dir = DAILY_DIR / day_norm
    _ensure_dir(out_dir)

    out_path = out_dir / f"edinet_agg_{day_norm}.json"
    if out_path.exists() and not overwrite:
        return ParseResult(ok=True, saved_path=str(out_path), meta={"note": "cache_hit", "day": day_norm})

    raw_meta, raw_data = _read_raw_documents(day_norm)
    if "error" in raw_data:
        # エラーも daily に吐いて調査可能にする
        payload = {
            "meta": {
                "day": day_norm,
                "parsed_at": datetime.now(JST).isoformat(timespec="seconds"),
                "raw_error": raw_data,
            },
            "agg_by_edinet": {},
        }
        out_path.write_text(_safe_json_dump(payload), encoding="utf-8")
        return ParseResult(ok=False, saved_path=str(out_path), meta=payload["meta"])

    # EDINET documents.json の主要キー: results
    results = raw_data.get("results")
    if not isinstance(results, list):
        results = []

    agg_by_edinet: Dict[str, Any] = {}

    for rec in results:
        if not isinstance(rec, dict):
            continue

        edinet_code = _safe_str(rec.get("edinetCode"))
        if not edinet_code:
            continue

        doc_type = _safe_str(rec.get("docTypeCode"))  # 例: "120" 有価証券報告書等
        sec_code = _safe_str(rec.get("secCode"))      # 例: 証券コードが入ることがある（空のことも多い）
        jcn = _safe_str(rec.get("JCN"))               # 法人番号が返る場合
        filer = _safe_str(rec.get("filerName"))
        submit_dt = _safe_str(rec.get("submitDateTime"))
        ordinance = _safe_str(rec.get("ordinanceCode"))
        form = _safe_str(rec.get("formCode"))
        doc_id = _safe_str(rec.get("docID"))

        # ----- 初期化 -----
        if edinet_code not in agg_by_edinet:
            agg_by_edinet[edinet_code] = {
                "edinet_code": edinet_code,
                "filer_name": filer or None,
                "sec_code": sec_code or None,
                "jcn": jcn or None,
                "doc_count": 0,
                "doc_type_counts": {},
                "latest_submit_datetime": None,
                "latest_doc_id": None,
                "latest_doc_type": None,
                "latest_form_code": None,
                "latest_ordinance_code": None,
                "flags": {
                    "has_securities_report": False,   # 120系
                    "has_quarterly_report": False,    # 130系
                    "has_extra_report": False,        # 臨報っぽい
                    "has_amendment": False,           # 訂正書
                },
            }

        a = agg_by_edinet[edinet_code]
        a["doc_count"] = int(a.get("doc_count") or 0) + 1

        # docType counts
        dtc = a.get("doc_type_counts")
        if not isinstance(dtc, dict):
            dtc = {}
            a["doc_type_counts"] = dtc
        if doc_type:
            dtc[doc_type] = int(dtc.get(doc_type) or 0) + 1

        # latest
        a["latest_submit_datetime"] = _max_dt(a.get("latest_submit_datetime"), submit_dt)  # type: ignore[arg-type]
        # 最新docを雑に更新（submitDateTimeが比較できないケースもあるので、max_dtに合わせて追随）
        if a.get("latest_submit_datetime") == submit_dt and submit_dt:
            a["latest_doc_id"] = doc_id or a.get("latest_doc_id")
            a["latest_doc_type"] = doc_type or a.get("latest_doc_type")
            a["latest_form_code"] = form or a.get("latest_form_code")
            a["latest_ordinance_code"] = ordinance or a.get("latest_ordinance_code")

        # flags（ざっくり）
        # docTypeCodeはEDINETの定義に従う（細かい分類は後で拡張）
        if doc_type.startswith("12"):  # 120台: 有報系のイメージ
            a["flags"]["has_securities_report"] = True
        if doc_type.startswith("13"):  # 130台: 四半期/半期などのイメージ
            a["flags"]["has_quarterly_report"] = True

        # 訂正書っぽい（formCodeやdocTypeCode、またはタイトルに "訂正" が入るなどを拾いたいが、
        # まずは簡易に rec に amendmentFlag があれば優先）
        if _safe_bool(rec.get("amendmentFlag")):
            a["flags"]["has_amendment"] = True
        else:
            title = _safe_str(rec.get("docDescription"))
            if "訂正" in title:
                a["flags"]["has_amendment"] = True

        # 臨報っぽい（descriptionの簡易判定）
        desc = _safe_str(rec.get("docDescription"))
        if "臨時" in desc or "臨報" in desc:
            a["flags"]["has_extra_report"] = True

        # filer_name/sec_code/jcn の穴埋め
        if not a.get("filer_name") and filer:
            a["filer_name"] = filer
        if not a.get("sec_code") and sec_code:
            a["sec_code"] = sec_code
        if not a.get("jcn") and jcn:
            a["jcn"] = jcn

    meta_out: Dict[str, Any] = {
        "day": day_norm,
        "parsed_at": datetime.now(JST).isoformat(timespec="seconds"),
        "raw_meta": raw_meta,
        "results_count": len(results),
        "edinet_count": len(agg_by_edinet),
    }

    payload = {
        "meta": meta_out,
        "agg_by_edinet": agg_by_edinet,
    }

    out_path.write_text(_safe_json_dump(payload), encoding="utf-8")
    return ParseResult(ok=True, saved_path=str(out_path), meta=meta_out)


def latest_daily_path(day: str) -> str:
    day_norm = _ymd_from_any(day) or str(day).strip()
    return str(DAILY_DIR / day_norm / f"edinet_agg_{day_norm}.json")