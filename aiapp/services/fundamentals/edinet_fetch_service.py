# aiapp/services/fundamentals/edinet_fetch_service.py
# -*- coding: utf-8 -*-
"""
EDINET（金融庁）API から提出書類メタ情報を取得して raw キャッシュに保存する。

目的（ルートBのB-1: fetch + cache）:
- まずは「提出書類の一覧（JSON）」を日次で確実に取れる状態にする
- 解析（特徴量化）は別サービスに分離（次ファイルで作る）

出力（例）:
media/aiapp/fundamentals/raw/20260114/edinet_documents_20260114.json

前提:
- requests が入っていること（ほとんどの環境で入っているはず）
- APIキーがあるなら環境変数 EDINET_API_KEY で渡せる（無くても動くが制限/失敗しやすい）
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests


# -----------------------------
# 設定
# -----------------------------
EDINET_BASE = "https://disclosure.edinet-fsa.go.jp/api/v2"

FUND_RAW_DIR = Path("media/aiapp/fundamentals/raw")
FUND_RAW_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_TIMEOUT = (8.0, 20.0)  # (connect, read)
DEFAULT_RETRY = 3
DEFAULT_RETRY_SLEEP = 2.0


@dataclass
class FetchResult:
    ok: bool
    status_code: int
    saved_path: Optional[str]
    meta: Dict[str, Any]


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _safe_json_dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)


def _get_api_key() -> Optional[str]:
    k = os.getenv("EDINET_API_KEY") or os.getenv("FSA_EDINET_API_KEY")
    if not k:
        return None
    k = str(k).strip()
    return k or None


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "my_stock_portfolio/aiapp fundamentals_fetch (edinet)",
            "Accept": "application/json",
        }
    )
    return s


def _request_json(
    url: str,
    params: Dict[str, Any],
    *,
    timeout: Tuple[float, float] = DEFAULT_TIMEOUT,
    retry: int = DEFAULT_RETRY,
    retry_sleep: float = DEFAULT_RETRY_SLEEP,
) -> Tuple[int, Dict[str, Any]]:
    """
    失敗しても落ちないように、最低限のリトライとエラー情報を返す。
    戻り: (status_code, json_or_error_dict)
    """
    s = _session()
    last_err: Optional[str] = None

    for i in range(max(1, int(retry))):
        try:
            r = s.get(url, params=params, timeout=timeout)
            sc = int(r.status_code)

            # 429/5xx はリトライ対象（軽く待つ）
            if sc == 429 or 500 <= sc <= 599:
                last_err = f"http_{sc}"
                if i < retry - 1:
                    time.sleep(retry_sleep * (i + 1))
                    continue

            # JSONとして読めない場合もあるので保護
            try:
                j = r.json()
            except Exception:
                j = {"error": "invalid_json", "status_code": sc, "text": (r.text[:2000] if r.text else "")}
            return sc, j

        except Exception as ex:
            last_err = str(ex)
            if i < retry - 1:
                time.sleep(retry_sleep * (i + 1))
                continue

    return 0, {"error": "request_failed", "detail": last_err or "unknown"}


def fetch_documents_list(
    target_date: date,
    *,
    save: bool = True,
    overwrite: bool = False,
) -> FetchResult:
    """
    EDINET 提出書類一覧（documents.json 相当）を取得して保存する。
    """
    day = _ymd(target_date)
    out_dir = FUND_RAW_DIR / day
    _ensure_dir(out_dir)

    out_path = out_dir / f"edinet_documents_{day}.json"
    if out_path.exists() and not overwrite:
        return FetchResult(
            ok=True,
            status_code=200,
            saved_path=str(out_path),
            meta={
                "note": "cache_hit",
                "date": day,
                "path": str(out_path),
            },
        )

    api_key = _get_api_key()

    # EDINET v2: /documents.json?date=YYYY-MM-DD&type=2
    # type=2 は JSON 返却（メタ一覧）
    url = f"{EDINET_BASE}/documents.json"
    params: Dict[str, Any] = {
        "date": target_date.strftime("%Y-%m-%d"),
        "type": 2,
    }
    if api_key:
        params["Subscription-Key"] = api_key

    sc, data = _request_json(url, params)

    ok = sc == 200 and isinstance(data, dict) and ("results" in data or "metadata" in data or "message" in data)
    meta: Dict[str, Any] = {
        "date": day,
        "url": url,
        "status_code": sc,
        "has_api_key": bool(api_key),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }

    # 失敗でも raw を残して調査できるように保存する（save=True の場合）
    if save:
        try:
            payload = {
                "meta": meta,
                "data": data,
            }
            out_path.write_text(_safe_json_dump(payload), encoding="utf-8")
            saved = str(out_path)
        except Exception as ex:
            saved = None
            meta["save_error"] = str(ex)
    else:
        saved = None

    return FetchResult(ok=bool(ok), status_code=int(sc), saved_path=saved, meta=meta)


def fetch_recent_documents_lists(
    days: int = 7,
    *,
    save: bool = True,
    overwrite: bool = False,
    sleep_sec: float = 0.8,
) -> Dict[str, Any]:
    """
    直近N日分をまとめて取得（cron等で安定運用用）
    - 休日/祝日でも「空」が返ることがあるので、落ちないことを優先。
    """
    days = max(1, int(days))
    today = date.today()
    results = []

    for i in range(days):
        d = today - timedelta(days=i)
        r = fetch_documents_list(d, save=save, overwrite=overwrite)
        results.append(
            {
                "date": _ymd(d),
                "ok": r.ok,
                "status_code": r.status_code,
                "saved_path": r.saved_path,
            }
        )
        # 叩きすぎ防止（429回避のため軽く待つ）
        if i < days - 1 and sleep_sec:
            time.sleep(float(sleep_sec))

    summary = {
        "days": days,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }
    return summary


def latest_cached_day() -> Optional[str]:
    """
    media/aiapp/fundamentals/raw の中で一番新しい YYYYMMDD を返す。
    """
    try:
        xs = [p.name for p in FUND_RAW_DIR.iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 8]
        xs.sort(reverse=True)
        return xs[0] if xs else None
    except Exception:
        return None