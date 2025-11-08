# -*- coding: utf-8 -*-
"""
picks_build (staged+robust)
- 予選(軽量) → 本選(重い) の二段構成
- 国内個別株優先のユニバースフィルタ（ETF/REIT/連動型/投信 等を除外）
- 偏り回避の offset / ランダムサンプリング
- 予選バー数30に緩和、レスキュー再試行あり
- fast_pass==0 の場合も、暫定10件を必ず返す（UIを空にしない）

使い方:
  python manage.py picks_build --sample 300 --offset 0 --force
  # 本番は --sample を外す（全量）
  # 偏り回避に --offset 300 等で先頭スキップも可能
"""
from __future__ import annotations

import json
import math
import os
import random
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

MAX_WORKERS = int(os.environ.get("AIAPP_BUILD_WORKERS", "12"))

# 予選／本選の足本数
FAST_BARS = 30            # 予選は短く（30）。取りにくい環境でも通す
FAST_BARS_RESCUE = 60     # 予選レスキュー用（失敗時に一度だけ長めで再取得）
DEEP_BARS = 180           # 本選

MIN_BARS = 60             # 本選の最低バー数

# 本選に進める目標数（全体規模に応じて自動調整もする）
PREFINAL_TOPK = 120

LOT_SIZE = 100
TARGET_LOSS_JPY = 20_000.0

MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
PICKS_DIR = MEDIA_ROOT / "aiapp" / "picks"
PICKS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_HORIZON = "short"
DEFAULT_MODE = "aggressive"
DEFAULT_TONE = "friendly"
LOCK_PATH = PICKS_DIR / ".picks_build.lock"

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

BLOCK_KEYWORDS = [
    "ＥＴＦ", "ETF", "上場投信", "投資法人", "ＲＥＩＴ", "REIT",
    "連動型", "ETN", "指数", "インデックス", "債券", "国債",
]
# 1300番台にETFが多いので、一部を除外気味にする（完全ではないが効果あり）
def _looks_index_like(code: str, name: str) -> bool:
    try:
        c = int(str(code))
    except Exception:
        return True
    if any(k in name for k in BLOCK_KEYWORDS):
        return True
    # 1xxx のうち 1300台はETF多め、1800台(鉱業)はOKなので限定的に弾く
    if 1300 <= c <= 1399:
        # ただし明らかに個別株なら残したいが名前で判定困難のためここは弾く
        return True
    return False

@dataclass
class URow:
    code: str
    name: str
    sector_name: Optional[str] = None

def _iter_universe(limit: Optional[int] = None, offset: int = 0, shuffle: bool = True) -> List[URow]:
    """
    国内個別株優先でユニバース抽出。
    - ETF/REIT/連動型/投信 等を名称で除外（簡易）
    - offset/limit/shuffle で偏りを回避
    """
    qs = StockMaster.objects.all().values_list("code", "name", "sector_name")
    rows: List[URow] = []
    for code, name, sector_name in qs:
        sname = str(name or "")
        scode = str(code or "")
        if _looks_index_like(scode, sname):
            continue
        rows.append(URow(scode, sname, sector_name or None))
    total = len(rows)
    if shuffle:
        random.seed(42)  # 再現性のあるシャッフル
        random.shuffle(rows)
    if offset > 0:
        rows = rows[offset:]
    if limit:
        rows = rows[:limit]
    _log(f"universe(filtered)={len(rows)} / total={total} (offset={offset}, shuffle={shuffle})")
    return rows

# ====== 予選（軽量スコア） =====================================================

def _calc_fast_score(code: str) -> Optional[Tuple[str, float, float]]:
    """
    予選用の軽量スコア：
      return (code, close, fast_score_0_100)
    失敗時は None。まず FAST_BARS で試し、ダメなら FAST_BARS_RESCUE でも一度再試行。
    """
    def _try(nbars: int) -> Optional[Tuple[str, float, float]]:
        df = get_prices(code, nbars)
        if df is None or len(df) < min(20, nbars):
            return None
        close = float(df["close"].iloc[-1])
        # 簡易モメンタム + ボラ控除
        r5 = (close / float(df["close"].iloc[-5]) - 1.0) if len(df) > 5 else 0.0
        r20 = (close / float(df["close"].iloc[-20]) - 1.0) if len(df) > 20 else 0.0
        hi = df["high"] if "high" in df.columns else df["close"]
        lo = df["low"]  if "low"  in df.columns else df["close"]
        tr = (hi - lo).abs()
        atr = float(tr.tail(14).mean()) if len(tr) >= 14 else float(tr.mean())
        atr_pct = atr / max(1e-6, close)
        raw = (r5 * 100) * 0.4 + (r20 * 100) * 0.6 - (atr_pct * 100) * 0.5
        fast = max(0.0, min(100.0, 50.0 + raw))
        return (code, close, float(round(fast, 2)))
    try:
        out = _try(FAST_BARS)
        if out is not None:
            return out
        # レスキュー
        out = _try(FAST_BARS_RESCUE)
        return out
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
                f"RSI={feat['rsi'].iloc[-1]:.0f}" if 'rsi' in feat.columns else "RSI=–",
                f"MACDヒスト={feat.get('macd_hist', [0])[-1]:+.3f}" if 'macd_hist' in feat.columns else "MACD=–",
                f"VWAP乖離={feat.get('vwap_diff_pct', [0])[-1]:+.2%}" if 'vwap_diff_pct' in feat.columns else "VWAP=–",
            ],
        }
    except Exception:
        return None

# ====== コマンド ===============================================================

class Command(BaseCommand):
    help = "Build AI picks snapshot (staged+robust)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="ignore lock file and run")
        parser.add_argument("--sample", type=int, default=None,
                            help="limit universe size for testing (e.g., 300)")
        parser.add_argument("--offset", type=int, default=0,
                            help="skip first N candidates after filtering (to rotate universe)")
        parser.add_argument("--no-shuffle", action="store_true",
                            help="do not shuffle filtered universe before slicing")

    def handle(self, *args, **options):
        force = bool(options.get("force", False))
        sample: Optional[int] = options.get("sample")
        offset: int = int(options.get("offset", 0))
        shuffle: bool = not bool(options.get("no_shuffle", False))

        began = time.time()
        label = f"{DEFAULT_HORIZON}/{DEFAULT_MODE}" + (f" sample={sample}" if sample else "")
        _log(f"start {label}")

        with BuildLock(LOCK_PATH, force=force):
            items = self._build_snapshot(sample=sample, offset=offset, shuffle=shuffle)

        dur = round(time.time() - began, 1)
        _log(f"done items={len(items)} dur={dur}s")

    # ------------------------------------------------------------------

    def _build_snapshot(self, sample: Optional[int], offset: int, shuffle: bool) -> List[Dict[str, Any]]:
        # 1) ユニバース（国内個別株優先・ETF等除外・偏り回避）
        uni: List[URow] = _iter_universe(limit=sample, offset=offset, shuffle=shuffle)

        # 2) 予選（軽量スコア、失敗時レスキュー）
        fast_scores: Dict[str, Tuple[float, float]] = {}  # code -> (close, fast_score)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fut = {ex.submit(_calc_fast_score, r.code): r for r in uni}
            for f in as_completed(fut):
                try:
                    res = f.result(timeout=15)
                    if not res:
                        continue
                    code, close, fast = res
                    fast_scores[code] = (close, fast)
                except Exception:
                    pass

        _log(f"fast_pass={len(fast_scores)}")

        # fast_pass が少なすぎるときは「大型株っぽい名称」の救済を追加抽出
        if len(fast_scores) < 30:
            big_keywords = ["トヨタ", "ソニー", "東京エレ", "キーエンス", "任天堂", "ファースト", "三菱", "三井", "住友", "KDDI", "NTT", "ソフトバンク"]
            extra = [r for r in uni if any(k in r.name for k in big_keywords)]
            extra = extra[:200]  # 上限
            _log(f"rescue extra candidates={len(extra)}")
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                fut2 = {ex.submit(_calc_fast_score, r.code): r for r in extra}
                for f in as_completed(fut2):
                    try:
                        res = f.result(timeout=15)
                        if not res:
                            continue
                        code, close, fast = res
                        fast_scores.setdefault(code, (close, fast))
                    except Exception:
                        pass
            _log(f"fast_pass(after rescue)={len(fast_scores)}")

        # 上位を本選へ
        pre_list = sorted(fast_scores.items(), key=lambda kv: kv[1][1], reverse=True)
        # ユニバース規模に応じてPRE TOPKを調整（最低60）
        target_k = max(60, min(PREFINAL_TOPK, int(len(uni) * 0.4) or 60))
        finalists = {code for code, _ in pre_list[:target_k]}
        _log(f"finalists={len(finalists)} (target={target_k})")

        # 3) 本選
        results: List[Dict[str, Any]] = []
        code2row = {r.code: r for r in uni}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fut3 = {ex.submit(_build_deep, code2row[c]): c for c in finalists if c in code2row}
            for f in as_completed(fut3):
                try:
                    item = f.result(timeout=25)
                    if item:
                        results.append(item)
                except Exception:
                    pass

        results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        topn = results[:10]

        # 4) スナップショット保存（空振り回避付き）
        meta = {
            "ts": _now_jst().isoformat(timespec="seconds"),
            "mode": DEFAULT_MODE,
            "horizon": DEFAULT_HORIZON,
            "tone": DEFAULT_TONE,
            "universe": len(uni),
            "fast_pass": len(fast_scores),
            "finalists": len(finalists),
            "version": "picks-v4.1-robust",
            "sample": sample or 0,
            "offset": offset,
            "shuffle": shuffle,
        }
        latest = PICKS_DIR / f"latest_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"
        hist   = PICKS_DIR / f"{_now_jst().strftime('%Y%m%d_%H%M%S')}_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"

        if not topn:
            if latest.exists():
                _log("no new items; keep previous snapshot")
                return json.loads(latest.read_text(encoding="utf-8")).get("items", [])
            # 初回でも空にしない：予選上位から暫定10件を組み立てる
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
                    "score": round(fast, 1),
                    "confidence": 2.5,
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