# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, glob, csv
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from django.conf import settings

# =========================
# ヘルパ
# =========================

def _media_root() -> str:
    """MEDIA_ROOT が未設定でもプロジェクトCWDで動くようフォールバック"""
    return getattr(settings, "MEDIA_ROOT", "") or os.getcwd()

def _market_dir() -> str:
    return os.path.join(_media_root(), "market")

def _latest_file(pattern: str) -> Optional[str]:
    """
    一番新しいファイル（ファイル名日付 or mtime）を返す
    """
    paths = glob.glob(pattern)
    if not paths:
        return None
    def _pick_date(p: str) -> Tuple[int, str]:
        base = os.path.basename(p)
        try:
            dt_text = base.split("_", 1)[1].split(".", 1)[0]
            dt = datetime.fromisoformat(dt_text).strftime("%Y%m%d")
            key = int(dt)
        except Exception:
            key = 0
        return (key, p)
    # 日付名が採れるならその順→同率はmtimeでソート
    paths.sort(key=lambda x: _pick_date(x)[0])
    paths.sort(key=lambda x: os.path.getmtime(x))
    return paths[-1]

def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    try:
        b = float(b)
        if abs(b) < 1e-12:
            return default
        return float(a) / b
    except Exception:
        return default

# =========================
# セクター強弱RS
# =========================

def _load_strength_from_json() -> Dict[str, Dict[str, Any]]:
    """
    JSONから強弱テーブルをロード:
      1) MEDIA_ROOT/market/sectors_YYYY-MM-DD.json
      2) MEDIA_ROOT/market/sectors.json
    どちらか最初に見つかった方を採用
    """
    mdir = _market_dir()
    os.makedirs(mdir, exist_ok=True)

    j_hist = _latest_file(os.path.join(mdir, "sectors_*.json"))
    j_single = os.path.join(mdir, "sectors.json")

    for path in [j_hist, j_single]:
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                rows = obj.get("data")
                base_date = obj.get("date")
                if isinstance(rows, list):
                    out: Dict[str, Dict[str, Any]] = {}
                    for r in rows:
                        sec = str(r.get("sector") or r.get("name") or "").strip()
                        if not sec:
                            continue
                        out[sec] = {
                            "rs_score": _safe_float(r.get("rs_score")),
                            "advdec":   (None if r.get("advdec") is None else _safe_float(r.get("advdec"))),
                            "vol_ratio":(None if r.get("vol_ratio") is None else _safe_float(r.get("vol_ratio"))),
                            "date":     r.get("date") or base_date or "",
                        }
                    return out
            except Exception:
                pass
    return {}

def _load_strength_from_csv() -> Dict[str, Dict[str, Any]]:
    """
    CSVから強弱テーブルをロード:
      1) MEDIA_ROOT/market/sectors_YYYY-MM-DD.csv
      2) MEDIA_ROOT/market/sectors.csv
    """
    mdir = _market_dir()
    os.makedirs(mdir, exist_ok=True)

    c_hist = _latest_file(os.path.join(mdir, "sectors_*.csv"))
    c_single = os.path.join(mdir, "sectors.csv")

    for path in [c_hist, c_single]:
        if path and os.path.exists(path):
            try:
                out: Dict[str, Dict[str, Any]] = {}
                with open(path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        sec = str(row.get("sector") or row.get("セクター") or "").strip()
                        if not sec:
                            continue
                        out[sec] = {
                            "rs_score": _safe_float(row.get("rs_score") or row.get("RS") or row.get("score")),
                            "advdec":   (None if (row.get("advdec") in (None, "", "NA")) else _safe_float(row.get("advdec"))),
                            "vol_ratio":(None if (row.get("vol_ratio") in (None, "", "NA")) else _safe_float(row.get("vol_ratio"))),
                            "date":     row.get("date") or row.get("日付") or "",
                        }
                return out
            except Exception:
                pass
    return {}

def _load_strength_from_db() -> Dict[str, Dict[str, Any]]:
    """
    DB（portfolio.models_market.SectorSignal）から最新日付の強弱をロード。
    戻り値: { sector: {rs_score, advdec=None, vol_ratio=None, date:"YYYY-MM-DD"} }
    """
    try:
        from django.db.models import Max
        from ..models_market import SectorSignal  # 遅延importで循環回避
        last = SectorSignal.objects.aggregate(last=Max("date"))["last"]
        if not last:
            return {}
        rows = (
            SectorSignal.objects
            .filter(date=last)
            .values("sector", "rs_score")
        )
        out: Dict[str, Dict[str, Any]] = {}
        dstr = last.isoformat()
        for r in rows:
            sec = str(r["sector"]).strip()
            if not sec:
                continue
            out[sec] = {
                "rs_score": _safe_float(r.get("rs_score")),
                "advdec": None,
                "vol_ratio": None,
                "date": dstr,
            }
        return out
    except Exception:
        # マイグレーション前/DB未準備でも落とさない
        return {}

def latest_sector_strength() -> Dict[str, Dict[str, Any]]:
    """
    最新の“セクター強弱RS”テーブルを返す（**DB優先**）。
    優先度:
      1) DB: portfolio.models_market.SectorSignal（最新date）
      2) JSON: MEDIA_ROOT/market/sectors_YYYY-MM-DD.json → sectors.json
      3) CSV : MEDIA_ROOT/market/sectors_YYYY-MM-DD.csv → sectors.csv

    DBで得られたセクターはそれを採用し、DBにないセクターはファイル/CSV側で**補完**する。
    """
    # DB
    db_map = _load_strength_from_db()

    # ファイル/CSV（フォールバック or 補完用）
    file_map = _load_strength_from_json()
    if not file_map:
        file_map = _load_strength_from_csv()

    if not db_map and not file_map:
        return {}

    if db_map and not file_map:
        return db_map

    if file_map and not db_map:
        return file_map

    # 両方ある場合は DB を優先してマージ
    merged: Dict[str, Dict[str, Any]] = {}
    merged.update(file_map)  # まず全部ファイルで埋める
    merged.update(db_map)    # DBの値で上書き（優先）
    return merged

# =========================
# 指数スナップショット＆RS
# =========================

def fetch_indexes_snapshot() -> Dict[str, Dict[str, Any]]:
    """
    主要指数の最新スナップショットを返す
    """
    mdir = _market_dir()
    os.makedirs(mdir, exist_ok=True)

    j_hist = _latest_file(os.path.join(mdir, "indexes_*.json"))
    j_single = os.path.join(mdir, "indexes.json")

    for path in [j_hist, j_single]:
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                rows = obj.get("data")
                base_date = obj.get("date")
                if isinstance(rows, list):
                    out: Dict[str, Dict[str, Any]] = {}
                    for r in rows:
                        sym = str(r.get("symbol") or r.get("name") or "").strip()
                        if not sym:
                            continue
                        out[sym] = {
                            "ret_1d":    _safe_float(r.get("ret_1d")),
                            "ret_5d":    _safe_float(r.get("ret_5d")),
                            "ret_20d":   _safe_float(r.get("ret_20d")),
                            "vol_ratio": _safe_float(r.get("vol_ratio")),
                            "date":      r.get("date") or base_date or "",
                        }
                    return out
            except Exception:
                pass

    return {
        "TOPIX": {"ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": 1.0, "date": ""},
        "N225":  {"ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": 1.0, "date": ""},
        "JPX400":{"ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": 1.0, "date": ""},
        "SPX":   {"ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": 1.0, "date": ""},
        "NDX":   {"ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": 1.0, "date": ""},
    }

def calc_relative_strength(index_table: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    """
    シンプルな相対強弱RS (-1..+1)
    """
    keys = list(index_table.keys())
    if not keys:
        return {}
    vals: List[float] = []
    for sym in keys:
        r = index_table[sym]
        rs_raw = 0.5 * _safe_float(r.get("ret_5d")) + 0.5 * _safe_float(r.get("ret_20d"))
        vals.append(rs_raw)
    vmin, vmax = min(vals), max(vals)
    span = max(1e-9, (vmax - vmin))
    out: Dict[str, float] = {}
    for sym, rs_raw in zip(keys, vals):
        norm01 = (rs_raw - vmin) / span
        rs = norm01 * 2.0 - 1.0
        out[sym] = max(-1.0, min(1.0, rs))
    return out

# =========================
# ブレッドス（地合い）
# =========================

def _latest_market_file(kind: str) -> Optional[str]:
    """MEDIA_ROOT/market/{kind}_*.json / {kind}.json"""
    mdir = _market_dir()
    os.makedirs(mdir, exist_ok=True)
    j_hist = _latest_file(os.path.join(mdir, f"{kind}_*.json"))
    j_single = os.path.join(mdir, f"{kind}.json")
    for p in [j_hist, j_single]:
        if p and os.path.exists(p):
            return p
    return None

def latest_breadth() -> Dict[str, Any]:
    """
    直近のブレッドス（騰落・出来高・新高値安値）を返す
    """
    path = _latest_market_file("breadth")
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}

def breadth_regime(b: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    ブレッドス（騰落/出来高/新高値-新安値）から地合いレジームを推定
    """
    b = b or latest_breadth()
    if not b:
        return {"ad_ratio": 1.0, "vol_ratio": 1.0, "hl_diff": 0.0, "score": 0.0, "regime": "NEUTRAL"}

    adv = _safe_float(b.get("adv"))
    dec = _safe_float(b.get("dec"))
    upv = _safe_float(b.get("up_vol"))
    dnv = _safe_float(b.get("down_vol"))
    nh = _safe_float(b.get("new_high"))
    nl = _safe_float(b.get("new_low"))

    ad_ratio = _safe_div(adv, dec, 1.0)
    vol_ratio = _safe_div(upv, dnv, 1.0)
    hl_diff = nh - nl

    score = 0.0
    if ad_ratio >= 1.30: score += 0.40
    elif ad_ratio <= 0.77: score -= 0.40
    if vol_ratio >= 1.20: score += 0.35
    elif vol_ratio <= 0.83: score -= 0.35
    if hl_diff >= 50: score += 0.35
    elif hl_diff <= -50: score -= 0.35

    score = max(-1.0, min(1.0, score))
    if score >= 0.35:
        regime = "RISK_ON"
    elif score <= -0.35:
        regime = "RISK_OFF"
    else:
        regime = "NEUTRAL"

    return {
        "ad_ratio": round(ad_ratio, 3),
        "vol_ratio": round(vol_ratio, 3),
        "hl_diff": float(hl_diff),
        "score": round(score, 3),
        "regime": regime,
    }