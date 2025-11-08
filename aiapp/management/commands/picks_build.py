# -*- coding: utf-8 -*-
"""
picks_build (staged)
二段構成で高速化：
  1) 予選: get_prices(..., 40~90) で軽いスコアを算出し上位だけ残す
  2) 本選: 残りに対して features + score_sample を実行してTOP10を作成

安全策：
  - 銘柄ごとタイムアウト
  - 空振り時は前回スナップショットを維持
  - それも無ければ予選スコアから暫定10件を組み立てて返す（UIを空にしない）

使い方:
  python manage.py picks_build --sample 300 --force
  （--sample でユニバースを絞ってテスト、本番は付けずに全量）
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_sample  # (feat, mode, horizon)

# ====== チューニング ===========================================================

# 並列度（外部のレート制限を見ながら 8〜16 の範囲で調整）
MAX_WORKERS = int(os.environ.get("AIAPP_BUILD_WORKERS", "12"))

# 予選／本選の足本数
FAST_BARS = 60           # 予選は軽く（40〜90で調整可）
DEEP_BARS = 180          # 本選は従来通り

# 最低バー数（本選で弾き過ぎないよう60に緩和）
MIN_BARS = 60

# 予選で本選に進める数（全量が大きいほどここを増やす）
PREFINAL_TOPK = 120

# 単元・簡易サイジング
LOT_SIZE = 100
TARGET_LOSS_JPY = 20_000.0

# 出力先
MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
PICKS_DIR = MEDIA_ROOT / "aiapp" / "picks"
PICKS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_HORIZON = "short"
DEFAULT_MODE = "aggressive"
DEFAULT_TONE = "friendly"
LOCK_PATH = PICKS_DIR / ".picks_build.lock"

# タイムゾーン
JST = timezone(timedelta(hours=9))

def _now_jst() -> datetime:
    return datetime.now(JST)

def _log(msg: str) -> None:
    print(f"[picks_build] {msg}", flush=True)

class BuildLock:
    def __init__(self, path: Path, force: bool = False) -> None:
        self.path = path
        self.force = force
        self.acquired = False
    def __enter__(self):
        if self.path.exists() and not self.force:
            _log("another build is running; exit 202")
            sys.exit(202)
        try:
            self.path.write_text(str(os.getpid()))
            self.acquired = True
        except Exception:
            if not self.force:
                raise
        return self
    def __exit__(self, exc_type, exc, tb):
        if self.acquired:
            try: self.path.unlink(missing_ok=True)
            except Exception: pass

# ====== ユニバース =============================================================

@dataclass
class URow:
    code: str
    name: str
    sector_name: Optional[str] = None

def _iter_universe(limit: Optional[int] = None) -> List[URow]:
    qs = StockMaster.objects.all().values_list("code", "name", "sector_name")
    out: List[URow] = []
    for code, name, sector_name in qs:
        out.append(URow(str(code), str(name), sector_name or None))
        if limit and len(out) >= limit:
            break
    return out

# ====== 予選（軽量スコア） =====================================================

def _calc_fast_score(code: str) -> Optional[Tuple[str, float, float]]:
    """
    予選用の軽量スコアを返す：
      return (code, close, fast_score)
      fast_score は 0〜100 に丸める簡易指標（上位だけ本選へ）
    - 取得失敗や本数不足は None
    """
    try:
        df = get_prices(code, FAST_BARS)
        if df is None or len(df) < 30:
            return None
        close = float(df["close"].iloc[-1])

        # 軽い指標：直近モメンタム＋ボラ控除
        # 例）ret_20d, ret_5d と ATR%（過剰ボラは減点）
        ret_5 = (close / float(df["close"].iloc[-5]) - 1.0) if len(df) > 5 else 0.0
        ret_20 = (close / float(df["close"].iloc[-20]) - 1.0) if len(df) > 20 else 0.0

        # ATR%の簡易：高低差の指数平均から近似
        hi = df["high"] if "high" in df.columns else df["close"]
        lo = df["low"]  if "low"  in df.columns else df["close"]
        tr = (hi - lo).abs()
        atr = float(tr.tail(14).mean()) if len(tr) >= 14 else float(tr.mean())
        atr_pct = atr / max(1e-6, close)

        raw = (ret_5 * 100) * 0.4 + (ret_20 * 100) * 0.6 - (atr_pct * 100) * 0.5
        fast = max(0.0, min(100.0, 50.0 + raw))  # 0〜100に丸め
        return (code, close, float(round(fast, 2)))
    except Exception:
        return None

# ====== 本選（重い処理） =======================================================

def _confidence_from_feat(feat_df) -> float:
    try:
        last = feat_df.iloc[-1]
        atr_pct = float(last.get("atr_pct", 0.02))
        nan_rate = float((feat_df.isna().sum(axis=1).iloc[-60:] > 0).mean())
        base = 3.0
        if atr_pct < 0.03: base += 0.5
        if nan_rate < 0.05: base += 0.5
        else: base -= 0.5
        return float(max(1.0, min(5.0, round(base, 2))))
    except Exception:
        return 2.5

def _build_deep(row: URow) -> Optional[Dict[str, Any]]:
    try:
        df = get_prices(row.code, DEEP_BARS)
        if df is None or len(df) < MIN_BARS:
            return None
        feat = compute_features(df)
        if feat is None or len(feat) == 0:
            return None

        score = float(score_sample(feat, mode=DEFAULT_MODE, horizon=DEFAULT_HORIZON))
        score = max(0.0, min(100.0, score))

        conf = _confidence_from_feat(feat)
        close = float(df["close"].iloc[-1])

        entry = round(close * 0.994, 1)
        tp    = round(close * 1.045, 1)
        sl    = round(close * 0.965, 1)

        per_loss = max(1.0, entry - sl)
        qty = max(LOT_SIZE, int(TARGET_LOSS_JPY / per_loss / LOT_SIZE) * LOT_SIZE)

        return {
            "code": row.code,
            "name": row.name,
            "sector": row.sector_name or "",
            "price": close,
            "score": round(score, 1),
            "confidence": round(conf, 2),
            "entry": entry, "tp": tp, "sl": sl,
            "qty": qty,
            "required_cash": int(round(entry * qty)),
            "exp_profit": int(round((tp - entry) * qty)),
            "exp_loss": int(round((entry - sl) * qty)),
            "reasons": [
                # 文章化はビュー側で整形、ここは数値の種だけ
                f"RSI={feat['rsi'].iloc[-1]:.0f}" if 'rsi' in feat.columns else "RSI=–",
                f"MACDヒスト={feat.get('macd_hist', [0])[-1]:+.3f}" if 'macd_hist' in feat.columns else "MACD=–",
                f"VWAP乖離={feat.get('vwap_diff_pct', [0])[-1]:+.2%}" if 'vwap_diff_pct' in feat.columns else "VWAP=–",
            ],
        }
    except Exception:
        return None

# ====== コマンド ===============================================================

class Command(BaseCommand):
    help = "Build AI picks snapshot (staged fast→deep). Use --sample N to limit universe for testing."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="ignore lock file and run")
        parser.add_argument("--sample", type=int, default=None,
                            help="limit universe size for testing (e.g., 300)")

    def handle(self, *args, **options):
        force = bool(options.get("force", False))
        sample: Optional[int] = options.get("sample")

        began = time.time()
        label = f"{DEFAULT_HORIZON}/{DEFAULT_MODE}" + (f" sample={sample}" if sample else "")
        _log(f"start {label}")

        with BuildLock(LOCK_PATH, force=force):
            items = self._build_snapshot(sample=sample)

        dur = round(time.time() - began, 1)
        _log(f"done items={len(items)} dur={dur}s")

    # ------------------------------------------------------------------

    def _build_snapshot(self, sample: Optional[int]) -> List[Dict[str, Any]]:
        # 1) ユニバース
        uni: List[URow] = _iter_universe(limit=sample)
        _log(f"universe={len(uni)}")

        # 2) 予選：軽量スコア（タイムアウト内で並列）
        fast_scores: Dict[str, Tuple[float, float]] = {}  # code -> (close, fast_score)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fut = {ex.submit(_calc_fast_score, r.code): r for r in uni}
            for f in as_completed(fut):
                try:
                    res = f.result(timeout=15)  # 銘柄ごと最大15秒
                    if not res: 
                        continue
                    code, close, fast = res
                    fast_scores[code] = (close, fast)
                except Exception:
                    pass

        _log(f"fast_pass={len(fast_scores)}")

        # 上位を本選へ
        # 予選がスカスカでも最低限は本選に回す（例：min 60銘柄）
        pre_list = sorted(fast_scores.items(), key=lambda kv: kv[1][1], reverse=True)
        pre_k = max(60, min(PREFINAL_TOPK, len(pre_list)))
        finalists = {code for code, _ in pre_list[:pre_k]}
        _log(f"finalists={len(finalists)} (target={pre_k})")

        # 3) 本選：features + score_sample
        results: List[Dict[str, Any]] = []
        code2row = {r.code: r for r in uni}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fut2 = {ex.submit(_build_deep, code2row[c]): c for c in finalists if c in code2row}
            for f in as_completed(fut2):
                try:
                    item = f.result(timeout=25)  # 銘柄ごと最大25秒
                    if item:
                        results.append(item)
                except Exception:
                    pass

        # 4) TOP10
        results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        topn = results[:10]

        # 5) スナップショット保存（空なら安全策を実施）
        meta = {
            "ts": _now_jst().isoformat(timespec="seconds"),
            "mode": DEFAULT_MODE,
            "horizon": DEFAULT_HORIZON,
            "tone": DEFAULT_TONE,
            "universe": len(uni),
            "fast_pass": len(fast_scores),
            "finalists": len(finalists),
            "version": "picks-v4.0-staged",
            "sample": sample or 0,
        }
        latest = PICKS_DIR / f"latest_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"
        hist   = PICKS_DIR / f"{_now_jst().strftime('%Y%m%d_%H%M%S')}_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"

        # 空振り回避：前回を維持 or 予選から暫定10件を合成
        if not topn:
            if latest.exists():
                _log("no new items; keep previous snapshot")
                return json.loads(latest.read_text(encoding="utf-8")).get("items", [])
            # 予選から暫定10件を作る（初回でもUIを空にしない）
            fallback = []
            for code, (close, fast) in pre_list[:10]:
                row = code2row.get(code, URow(code, code, ""))
                entry = round(close * 0.996, 1)
                tp    = round(close * 1.030, 1)
                sl    = round(close * 0.975, 1)
                per_loss = max(1.0, entry - sl)
                qty = max(LOT_SIZE, int(TARGET_LOSS_JPY / per_loss / LOT_SIZE) * LOT_SIZE)
                fallback.append({
                    "code": row.code,
                    "name": row.name,
                    "sector": row.sector_name or "",
                    "price": close,
                    "score": round(fast, 1),            # 暫定: 予選スコアを総合得点に
                    "confidence": 2.5,                  # 暫定
                    "entry": entry, "tp": tp, "sl": sl,
                    "qty": qty,
                    "required_cash": int(round(entry * qty)),
                    "exp_profit": int(round((tp - entry) * qty)),
                    "exp_loss": int(round((entry - sl) * qty)),
                    "reasons": ["暫定: 予選スコアから自動生成"],
                })
            snap = {"meta": meta, "items": fallback}
            text = json.dumps(snap, ensure_ascii=False, indent=2)
            latest.write_text(text, encoding="utf-8")
            hist.write_text(text, encoding="utf-8")
            return fallback

        snap = {"meta": meta, "items": topn}
        text = json.dumps(snap, ensure_ascii=False, indent=2)
        latest.write_text(text, encoding="utf-8")
        hist.write_text(text, encoding="utf-8")
        return topn