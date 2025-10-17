や portfolio/services/market.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, glob, csv
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from django.conf import settings

# ========== ヘルパ ==========

def _media_root() -> str:
    """MEDIA_ROOT が未設定でもプロジェクトCWDで動くようフォールバック"""
    return getattr(settings, "MEDIA_ROOT", "") or os.getcwd()

def _market_dir() -> str:
    return os.path.join(_media_root(), "market")

def _latest_file(pattern: str) -> Optional[str]:
    """
    pattern 例:
      - os.path.join(_market_dir(), "sectors_*.json")
      - os.path.join(_market_dir(), "indexes_*.json")
    一番「新しい日付っぽい（ファイル名内）」 or mtime が新しいものを返却。
    """
    paths = glob.glob(pattern)
    if not paths:
        return None
    # まずファイル名中の YYYY-MM-DD を拾ってソート
    def _pick_date(p: str) -> Tuple[int, str]:
        base = os.path.basename(p)
        # 例: sectors_2025-01-10.json → 20250110
        try:
            dt_text = base.split("_", 1)[1].split(".", 1)[0]
            dt = datetime.fromisoformat(dt_text).strftime("%Y%m%d")
            key = int(dt)
        except Exception:
            key = 0
        return (key, p)
    paths.sort(key=lambda x: _pick_date(x)[0])
    # 日付キーが同じ/0の物は mtime で最後に上書き判断
    paths.sort(key=lambda x: os.path.getmtime(x))
    return paths[-1]

def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

# ========== 公開API: セクター強弱RS ==========

def latest_sector_strength() -> Dict[str, Dict[str, Any]]:
    """
    直近の “セクター強弱RS” テーブルを返す。
    返り値: { sector_name: { "rs_score": -1..+1, "advdec": float|None, "vol_ratio": float|None, "date": "YYYY-MM-DD" } }
    取得順序:
      1) MEDIA_ROOT/market/sectors_YYYY-MM-DD.json （一番新しい日付）
      2) MEDIA_ROOT/market/sectors.json            （単発ファイル）
      3) MEDIA_ROOT/market/sectors_*.csv           （CSV → JSON同等に読み替え）
      4) データが無ければ {} を返す（呼び元は静かにスキップ）
    JSON 例:
      {
        "date": "2025-01-10",
        "data": [
          {"sector": "情報・通信", "rs_score": 0.42, "advdec": 0.15, "vol_ratio": 1.08},
          {"sector": "電気機器",   "rs_score": 0.31}
        ]
      }
    CSV 例（ヘッダ任意/日本語OK）:
      sector,rs_score,advdec,vol_ratio,date
      情報・通信,0.42,0.15,1.08,2025-01-10
    """
    mdir = _market_dir()
    os.makedirs(mdir, exist_ok=True)

    # 1) JSON(履歴)
    j_hist = _latest_file(os.path.join(mdir, "sectors_*.json"))
    # 2) JSON(単発)
    j_single = os.path.join(mdir, "sectors.json")
    # 3) CSV(履歴)
    c_hist = _latest_file(os.path.join(mdir, "sectors_*.csv"))
    # 4) CSV(単発)
    c_single = os.path.join(mdir, "sectors.csv")

    # JSON優先
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
                # 破損はスキップ
                pass

    # CSV fallback
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

    # データ無し
    return {}

# ========== 公開API: 指数スナップショット＆RS ==========

def fetch_indexes_snapshot() -> Dict[str, Dict[str, Any]]:
    """
    主要指数の最新スナップショットを返す。
    取得順序:
      1) MEDIA_ROOT/market/indexes_YYYY-MM-DD.json （最も新しい日付）
      2) MEDIA_ROOT/market/indexes.json            （単発ファイル）
      3) データ無し → 簡易ダミー（0を多用）で返す
    JSON 期待形:
      {
        "date": "2025-01-10",
        "data": [
          {"symbol": "TOPIX", "ret_1d": 0.3, "ret_5d": 1.1, "ret_20d": 4.2, "vol_ratio": 0.95},
          {"symbol": "N225",  "ret_1d": 0.5, "ret_5d": 2.0, "ret_20d": 5.0, "vol_ratio": 1.05}
        ]
      }
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

    # 何も無い場合のダミー（0 差し）
    return {
        "TOPIX": {"ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": 1.0, "date": ""},
        "N225":  {"ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": 1.0, "date": ""},
        "JPX400":{"ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": 1.0, "date": ""},
        "SPX":   {"ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": 1.0, "date": ""},
        "NDX":   {"ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": 1.0, "date": ""},
    }

def calc_relative_strength(index_table: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    """
    シンプルな“相対強弱RS”を -1..+1 に写像して返す。
    入力: fetch_indexes_snapshot() の戻り値
    仕様（簡易版）:
      rs_raw = 0.5*ret_5d + 0.5*ret_20d  （%）
      グループ内の min..max で正規化 → 0..1 → -1..+1 に再写像
      データ不足（全部0など）は 0 を返す
    """
    keys = list(index_table.keys())
    if not keys:
        return {}

    vals: List[float] = []
    for sym in keys:
        r = index_table[sym]
        rs_raw = 0.5 * _safe_float(r.get("ret_5d")) + 0.5 * _safe_float(r.get("ret_20d"))
        vals.append(rs_raw)

    vmin = min(vals) if vals else 0.0
    vmax = max(vals) if vals else 0.0
    span = max(1e-9, (vmax - vmin))

    out: Dict[str, float] = {}
    for sym, rs_raw in zip(keys, vals):
        norm01 = (rs_raw - vmin) / span            # 0..1
        rs = norm01 * 2.0 - 1.0                    # -1..+1
        # クリップ
        if rs < -1.0: rs = -1.0
        if rs > +1.0: rs = +1.0
        out[sym] = rs
    return out
    
def _latest_market_file(kind: str) -> Optional[str]:
    """kind in {"breadth"} を想定。MEDIA_ROOT/market/{kind}_*.json / {kind}.json を探す"""
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
    直近のブレッドスを返す。
    期待JSON:
      {
        "date": "YYYY-MM-DD",
        "adv": 1200, "dec": 800,
        "up_vol": 1.2e9, "down_vol": 0.9e9,
        "new_high": 120, "new_low": 35
      }
    ファイル:
      MEDIA_ROOT/market/breadth_YYYY-MM-DD.json（推奨） or breadth.json
    無ければ {}。
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

def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    try:
        b = float(b)
        if abs(b) < 1e-12:
            return default
        return float(a) / b
    except Exception:
        return default

def breadth_regime(b: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    ブレッドス（騰落/出来高/新高値-新安値）から地合いレジームを推定。
    戻り値:
      {
        "ad_ratio": adv/dec,
        "vol_ratio": up_vol/down_vol,
        "hl_diff": new_high - new_low,
        "score": -1..+1,
        "regime": "RISK_ON|NEUTRAL|RISK_OFF"
      }
    スコア（簡易ルール）:
      - ad_ratio:    >=1.3 → +0.4, <=0.77 → -0.4
      - vol_ratio:   >=1.2 → +0.35, <=0.83 → -0.35
      - hl_diff:     >=+50 → +0.35, <=-50 → -0.35
      合計を -1..+1 にクリップ。
    """
    b = b or latest_breadth()
    if not b:
        return {"ad_ratio": 1.0, "vol_ratio": 1.0, "hl_diff": 0.0, "score": 0.0, "regime": "NEUTRAL"}

    adv = _safe_float(b.get("adv")); dec = _safe_float(b.get("dec"))
    upv = _safe_float(b.get("up_vol")); dnv = _safe_float(b.get("down_vol"))
    nh = _safe_float(b.get("new_high")); nl = _safe_float(b.get("new_low"))

    ad_ratio = _safe_div(adv, dec, 1.0)
    vol_ratio = _safe_div(upv, dnv, 1.0)
    hl_diff = nh - nl

    score = 0.0
    # 騰落
    if ad_ratio >= 1.30: score += 0.40
    elif ad_ratio <= 0.77: score -= 0.40
    # 出来高
    if vol_ratio >= 1.20: score += 0.35
    elif vol_ratio <= 0.83: score -= 0.35
    # 新高値-新安値
    if hl_diff >= 50: score += 0.35
    elif hl_diff <= -50: score -= 0.35

    # クリップ
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
