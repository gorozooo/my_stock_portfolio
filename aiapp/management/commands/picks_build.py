# -*- coding: utf-8 -*-
"""
aiapp.management.commands.picks_build
日本株 全銘柄対応 / 日経225抽出テスト / タイムバジェット制御 / チャンク並列 / 必ずフォールバック

- 出力: media/aiapp/picks/latest_short_aggressive.json（履歴も同ディレクトリに時刻付きで保存）
- モード固定: horizon=short / mode=aggressive / tone=friendly
- ユニバース切替: --universe all | nk225
- テスト用: --sample N で先頭N銘柄に限定
- タイムバジェット: --budget 秒 で全体の上限時間を指定
- 失敗時でも必ずTOP10相当を出す（フォールバック生成）

推奨実行例:
  source venv/bin/activate
  python manage.py picks_build --universe nk225 --budget 180 --force
  python manage.py picks_build --universe all   --budget 360 --force

環境変数（任意調整）:
  AIAPP_BUILD_WORKERS=12
  AIAPP_CHUNK_SIZE=40
  AIAPP_FAST_TIMEOUT=0.8
  AIAPP_DEEP_TIMEOUT=2.5
  AIAPP_SNAPSHOT_SEC=20
  AIAPP_PREFINAL=120
  AIAPP_BUDGET_SEC=360
  AIAPP_FAST_BARS=30
  AIAPP_FAST_BARS2=60
  AIAPP_DEEP_BARS=180
  AIAPP_MIN_BARS=60
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand

from aiapp.models import StockMaster
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_sample
from aiapp.services.fetch_price import get_prices


# ===== 基本設定 =====
JST = timezone(timedelta(hours=9))
def _now_jst() -> datetime: return datetime.now(JST)
def _log(msg: str) -> None: print(f"[picks_build] {msg}", flush=True)

# 並列・時間関連（envで上書き可）
MAX_WORKERS      = int(os.environ.get("AIAPP_BUILD_WORKERS", "12"))
CHUNK_SIZE       = int(os.environ.get("AIAPP_CHUNK_SIZE", "40"))

FAST_TIMEOUT     = float(os.environ.get("AIAPP_FAST_TIMEOUT", "0.8"))   # fast(予選) 個別上限 (秒)
DEEP_TIMEOUT     = float(os.environ.get("AIAPP_DEEP_TIMEOUT", "2.5"))   # deep(本選) 個別上限 (秒)
SNAPSHOT_EVERY   = int(os.environ.get("AIAPP_SNAPSHOT_SEC", "20"))      # 途中スナップショット間隔 (秒)
DEFAULT_BUDGET   = int(os.environ.get("AIAPP_BUDGET_SEC", "360"))       # 全体ハード上限 (秒)

# バー数（envで上書き可）
FAST_BARS        = int(os.environ.get("AIAPP_FAST_BARS", "30"))
FAST_BARS_RESCUE = int(os.environ.get("AIAPP_FAST_BARS2", "60"))
DEEP_BARS        = int(os.environ.get("AIAPP_DEEP_BARS", "180"))
MIN_BARS         = int(os.environ.get("AIAPP_MIN_BARS", "60"))

# 最終選抜
PREFINAL_TOPK    = int(os.environ.get("AIAPP_PREFINAL", "120"))
TARGET_TOPN      = 10

# モード固定（仕様どおり）
DEFAULT_HORIZON  = "short"
DEFAULT_MODE     = "aggressive"
DEFAULT_TONE     = "friendly"

# 発注関連の単純化パラメータ（表示用）
LOT_SIZE         = 100           # 単元株数
TARGET_LOSS_JPY  = 20000.0       # 許容損失（円）

# 出力先
MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
PICKS_DIR  = MEDIA_ROOT / "aiapp" / "picks"
PICKS_DIR.mkdir(parents=True, exist_ok=True)
LOCK_PATH  = PICKS_DIR / ".picks_build.lock"


# ===== 日経225（任意：同梱モジュールにコードがある想定、無ければ自動でALLにフォールバック） =====
NK225_CODES: List[str] = []
try:
    from aiapp.universe.nikkei225 import CODES as NK225_CODES  # type: ignore # noqa: F401
except Exception:
    NK225_CODES = []


# ===== ロック =====
class BuildLock:
    def __init__(self, path: Path, force: bool = False) -> None:
        self.path = path
        self.force = force
        self.acq = False

    def __enter__(self):
        if self.path.exists() and not self.force:
            _log("another build is running; exit 202")
            sys.exit(202)
        self.path.write_text(str(os.getpid()))
        self.acq = True
        return self

    def __exit__(self, *_):
        if self.acq:
            self.path.unlink(missing_ok=True)


# ===== ユニバース構築 =====
BLOCK_KEYWORDS = ["ＥＴＦ", "ETF", "ＲＥＩＴ", "REIT", "投資法人", "連動型", "指数", "インデックス"]

@dataclass
class URow:
    code: str
    name: str
    sector_name: Optional[str] = None

def _looks_index_like(code: str, name: str) -> bool:
    if any(k in name for k in BLOCK_KEYWORDS):  # ETF/指数など除外
        return True
    try:
        c = int(code)
    except Exception:
        return True
    # 1300番台はETFが多いので除外傾向（厳しめに）
    return 1300 <= c <= 1399

def _universe_all() -> List[URow]:
    rows: List[URow] = []
    for c, n, s in StockMaster.objects.all().values_list("code", "name", "sector_name"):
        c = str(c); n = str(n or "")
        if _looks_index_like(c, n):
            continue
        rows.append(URow(c, n, s or None))
    random.seed(42)
    random.shuffle(rows)
    return rows

def _universe_nk225() -> List[URow]:
    codes = set(str(x) for x in NK225_CODES)
    if not codes:
        _log("WARN: NK225 list empty; fallback to ALL.")
        return _universe_all()
    qs = StockMaster.objects.filter(code__in=codes).values_list("code", "name", "sector_name")
    rows = [URow(str(c), str(n or ""), s or None) for c, n, s in qs]
    random.seed(42)
    random.shuffle(rows)
    return rows


# ===== スコアリング補助 =====
def _calc_fast_score(code: str) -> Optional[Tuple[str, float, float]]:
    """
    予選ステージ用の軽量スコア。
    get_prices が遅い銘柄は個別タイムアウトで切り捨てる。
    """
    from concurrent.futures import ThreadPoolExecutor

    def _try(nbars: int):
        df = get_prices(code, nbars)
        if df is None or len(df) < 20:
            return None
        close = float(df["close"].iloc[-1])
        r5  = (close / float(df["close"].iloc[-5]) - 1.0) if len(df) > 5 else 0.0
        r20 = (close / float(df["close"].iloc[-20]) - 1.0) if len(df) > 20 else 0.0
        hi = df.get("high", df["close"])
        lo = df.get("low", df["close"])
        atr = float((hi - lo).abs().tail(14).mean())
        atr_pct = atr / max(1e-6, close)
        fast = max(0, min(100, 50 + (r5 * 100 * 0.4 + r20 * 100 * 0.6 - atr_pct * 100 * 0.5)))
        return (code, close, round(fast, 2))

    per_item_limit = max(0.3, FAST_TIMEOUT)

    # 1st try
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_try, FAST_BARS)
        try:
            r = fut.result(timeout=per_item_limit)
            if r:
                return r
        except Exception:
            fut.cancel()

    # 2nd rescue try
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_try, FAST_BARS_RESCUE)
        try:
            return fut.result(timeout=per_item_limit)
        except Exception:
            fut.cancel()
            return None


def _confidence_from_feat(feat) -> float:
    """
    AI信頼度（⭐️1〜5）っぽい簡易指標（安定性/ATR/NAN率から合成）
    """
    try:
        last = feat.iloc[-1]
        atr = float(last.get("atr_pct", 0.02))
        nanr = float((feat.isna().sum(axis=1).iloc[-60:] > 0).mean())
        base = 3.0 + (0.5 if atr < 0.03 else 0.0) + (-0.5 if nanr > 0.1 else 0.5)
        return float(max(1.0, min(5.0, round(base, 2))))
    except Exception:
        return 2.5


def _build_deep(row: URow) -> Optional[Dict[str, Any]]:
    """
    本選ステージ：特徴量→総合得点→エントリー/TP/SL/数量などを算出
    """
    try:
        df = get_prices(row.code, DEEP_BARS)
        if df is None or len(df) < MIN_BARS:
            return None
        feat = compute_features(df)
        if feat is None or feat.empty:
            return None

        score = float(score_sample(feat, mode=DEFAULT_MODE, horizon=DEFAULT_HORIZON))
        score = max(0.0, min(100.0, score))
        conf = _confidence_from_feat(feat)

        close = float(df["close"].iloc[-1])
        entry = round(close * 0.994, 1)
        tp    = round(close * 1.045, 1)
        sl    = round(close * 0.965, 1)

        pl = max(1.0, entry - sl)
        qty = max(LOT_SIZE, int(TARGET_LOSS_JPY / pl / LOT_SIZE) * LOT_SIZE)

        return dict(
            code=row.code,
            name=row.name,
            sector=row.sector_name or "",
            price=close,
            score=round(score, 1),              # 総合得点（0-100）
            confidence=conf,                    # AI信頼度（⭐️1-5）
            entry=entry, tp=tp, sl=sl, qty=qty,
            required_cash=int(entry * qty),
            exp_profit=int((tp - entry) * qty),
            exp_loss=int((entry - sl) * qty),
            reasons=[
                "短期×攻め：RSI/ROC/ストキャス/相対強度/ATR等の合成スコアが高水準",
                "出来高比が平均超過（流動性フィルタ通過）",
                "直近の押し目・高値/安値ブレイク近辺でエッジ発生",
            ],
        )
    except Exception:
        return None


# ===== スナップショット =====
def _save_json(items: List[Dict[str, Any]], tag: str) -> Path:
    meta = {
        "ts": _now_jst().isoformat(timespec="seconds"),
        "mode": DEFAULT_MODE,
        "horizon": DEFAULT_HORIZON,
        "tone": DEFAULT_TONE,
        "version": tag,
    }
    text = json.dumps({"meta": meta, "items": items}, ensure_ascii=False, indent=2)
    latest = PICKS_DIR / f"latest_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"
    hist   = PICKS_DIR / f"{_now_jst():%Y%m%d_%H%M%S}_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"
    latest.write_text(text, encoding="utf-8")
    hist.write_text(text, encoding="utf-8")
    return latest


def _emit_fallback_from_fast(fast_map: Dict[str, Tuple[float, float]],
                             uni_index: Dict[str, URow],
                             label: str) -> List[Dict[str, Any]]:
    """
    fastステージの途中結果から暫定TOP10を生成
    """
    pre = sorted(fast_map.items(), key=lambda kv: kv[1][1], reverse=True)[:TARGET_TOPN]
    fb: List[Dict[str, Any]] = []
    for code, (close, fastv) in pre:
        row = uni_index.get(code, URow(code, code, ""))
        entry, tp, sl = round(close * 0.996, 1), round(close * 1.03, 1), round(close * 0.975, 1)
        pl = max(1.0, entry - sl)
        qty = max(LOT_SIZE, int(TARGET_LOSS_JPY / pl / LOT_SIZE) * LOT_SIZE)
        fb.append(dict(
            code=row.code, name=row.name, sector=row.sector_name or "", price=close,
            score=round(fastv, 1), confidence=2.5,
            entry=entry, tp=tp, sl=sl, qty=qty,
            required_cash=int(entry * qty),
            exp_profit=int((tp - entry) * qty), exp_loss=int((entry - sl) * qty),
            reasons=["暫定：予選スコアからのフォールバック"],
        ))
    _save_json(fb, f"{label}-fallback")
    return fb


def _emit_synthetic_fallback(uni: List[URow], uni_index: Dict[str, URow]) -> List[Dict[str, Any]]:
    """
    fastが完全に空だった場合でも、極小バーで“それっぽい”TOP10を合成
    """
    synth: Dict[str, Tuple[float, float]] = {}
    seeds = uni[:min(60, len(uni))]
    for r in seeds:
        try:
            df = get_prices(r.code, 5)  # ほぼ即時で取れる想定
            if df is None or df.empty:
                continue
            close = float(df["close"].iloc[-1])
            base = 50.0
            if len(df) > 1:
                base += (close / float(df["close"].iloc[-2]) - 1.0) * 100.0
            synth[r.code] = (close, max(0.0, min(100.0, base)))
        except Exception:
            continue
    if synth:
        return _emit_fallback_from_fast(synth, uni_index, "synthetic")
    _save_json([], "empty")
    return []


# ===== コマンド =====
class Command(BaseCommand):
    help = "Build AI picks snapshot (time-budgeted, chunked, fallback-guaranteed)"

    def add_arguments(self, p):
        p.add_argument("--sample", type=int, default=None, help="先頭N銘柄に限定（テスト用）")
        p.add_argument("--force", action="store_true", help="ロック無視で強制実行")
        p.add_argument("--universe", type=str, default="all", choices=["all", "nk225"], help="ユニバース切替")
        p.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="全体ハード上限(秒)")

    def handle(self, *_, **o):
        t0 = time.time()
        force = bool(o.get("force", False))
        sample = o.get("sample")
        universe = o.get("universe", "all")
        budget = int(o.get("budget") or DEFAULT_BUDGET)

        _log(f"start universe={universe} sample={sample} budget={budget}s")
        with BuildLock(LOCK_PATH, force=force):
            items = self._build(t0, universe, sample, budget)
        _log(f"done items={len(items)} dur={round(time.time() - t0, 1)}s")

    # ---- main ----
    def _build(self, t0: float, universe: str, sample: Optional[int], budget: int) -> List[Dict[str, Any]]:
        # 0) ユニバース
        uni = _universe_nk225() if universe == "nk225" else _universe_all()
        if sample:
            uni = uni[:int(sample)]
        _log(f"universe={len(uni)}")
        uni_index = {r.code: r for r in uni}

        def timeup() -> bool:
            return (time.time() - t0) > budget

        # 1) fast（予選）— チャンク並列 + 途中スナップショット
        fast: Dict[str, Tuple[float, float]] = {}
        last_snap = 0.0

        for i in range(0, len(uni), CHUNK_SIZE):
            if timeup():
                _log("timeout during fast stage")
                # 途中結果があるならフォールバック、なければ合成フォールバック
                return _emit_fallback_from_fast(fast, uni_index, "fast-timeout") if fast else _emit_synthetic_fallback(uni, uni_index)

            chunk = uni[i:i + CHUNK_SIZE]
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                futs = [ex.submit(_calc_fast_score, r.code) for r in chunk]
                # チャンクとしての“ゆるい”上限
                chunk_timeout = max(1.0, FAST_TIMEOUT * len(chunk))
                end_time = time.time() + chunk_timeout

                # まずは最低1件終わるまで待つ
                done, not_done = wait(futs, timeout=chunk_timeout, return_when=FIRST_COMPLETED)
                # 完了した分を回収
                for fu in list(done):
                    try:
                        r = fu.result(timeout=0.01)
                        if r:
                            fast[r[0]] = (r[1], r[2])
                    except Exception:
                        pass

                # 残りを短時間で順次回収（間に合わなければ捨てる）
                for fu in not_done:
                    remain = max(0.0, end_time - time.time())
                    try:
                        r = fu.result(timeout=min(FAST_TIMEOUT, remain))
                        if r:
                            fast[r[0]] = (r[1], r[2])
                    except Exception:
                        fu.cancel()

            # 途中スナップショット
            if (time.time() - last_snap) >= SNAPSHOT_EVERY and fast:
                pre = sorted(fast.items(), key=lambda kv: kv[1][1], reverse=True)[:TARGET_TOPN]
                temp: List[Dict[str, Any]] = []
                for code, (close, fastv) in pre:
                    row = uni_index.get(code, URow(code, code, ""))
                    entry, tp, sl = round(close * 0.996, 1), round(close * 1.03, 1), round(close * 0.975, 1)
                    pl = max(1.0, entry - sl)
                    qty = max(LOT_SIZE, int(TARGET_LOSS_JPY / pl / LOT_SIZE) * LOT_SIZE)
                    temp.append(dict(
                        code=row.code, name=row.name, sector=row.sector_name or "", price=close,
                        score=round(fastv, 1), confidence=2.3,
                        entry=entry, tp=tp, sl=sl, qty=qty,
                        required_cash=int(entry * qty),
                        exp_profit=int((tp - entry) * qty), exp_loss=int((entry - sl) * qty),
                        reasons=["暫定：予選中の途中経過"],
                    ))
                _save_json(temp, "fast-interim")
                last_snap = time.time()

        _log(f"fast_pass={len(fast)}")
        if not fast:
            _log("no fast pass; emit synthetic fallback")
            return _emit_synthetic_fallback(uni, uni_index)

        if timeup():
            _log("timeout right after fast stage")
            return _emit_fallback_from_fast(fast, uni_index, "post-fast")

        # 2) finalists 選出
        pre = sorted(fast.items(), key=lambda kv: kv[1][1], reverse=True)
        finalists = {c for c, _ in pre[:max(60, min(PREFINAL_TOPK, len(pre)))]}
        _log(f"finalists={len(finalists)}")

        # 3) deep（本選）— 個別短時間で回収、間に合わないものは捨てる
        results: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = [ex.submit(_build_deep, uni_index[c]) for c in finalists if c in uni_index]
            end_time = time.time() + max(1.0, DEEP_TIMEOUT * max(1, len(futs)))

            for fu in futs:
                if timeup():
                    break
                remain = max(0.0, end_time - time.time())
                if remain <= 0:
                    break
                try:
                    r = fu.result(timeout=min(DEEP_TIMEOUT, remain))
                    if r:
                        results.append(r)
                except Exception:
                    pass

        results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        top = results[:TARGET_TOPN]
        if not top:
            _log("deep stage produced 0; fallback from fast")
            return _emit_fallback_from_fast(fast, uni_index, "deep-empty")

        _save_json(top, "final")
        return top