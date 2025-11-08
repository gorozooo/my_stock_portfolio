# -*- coding: utf-8 -*-
"""
picks_build
- 日本株ユニバースから「短期×攻め」の10銘柄スナップショットを生成
- スナップショットは MEDIA_ROOT/aiapp/picks/ に保存
- テスト用に --sample N でユニバースを N 銘柄に制限可能（例：--sample 300）

主な特長
  * 空振り時でも前回スナップショットを維持（UIが空にならない）
  * 並列取得（AIAPP_BUILD_WORKERS 環境変数で調整、既定12）
  * 軽量流動性フィルタ（終値×100株 >= 10万円）
  * 最低本数 MIN_BARS=60（通過率↑）

使い方:
  python manage.py picks_build --sample 300      # ←テスト（300銘柄だけ処理）
  python manage.py picks_build                   # ←全銘柄
  AIAPP_BUILD_WORKERS=12 python manage.py picks_build --force

依存:
  - 既存の aiapp.services.fetch_price.get_prices
  - 既存の aiapp.models.features.compute_features
  - 既存の aiapp.models.scoring.score_sample
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.management.base import BaseCommand

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_sample  # (feat, mode, horizon)

# ======== チューニング項目 ====================================================

MAX_WORKERS = int(os.environ.get("AIAPP_BUILD_WORKERS", "12"))  # 8〜16で調整
MIN_BARS = 60                     # 最低バー数
LOT_SIZE = 100
MIN_NOTIONAL = 100_000            # 10万円（必要なら 200_000 などに上げてOK）

# 出力先
MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
PICKS_DIR = MEDIA_ROOT / "aiapp" / "picks"
PICKS_DIR.mkdir(parents=True, exist_ok=True)

# 対象（現状は短期×攻めを確実に）
DEFAULT_HORIZON = "short"
DEFAULT_MODE = "aggressive"
DEFAULT_TONE = "friendly"

LOCK_PATH = PICKS_DIR / ".picks_build.lock"

# ======== ユーティリティ ======================================================

JST = timezone(timedelta(hours=9))


def _now_jst() -> datetime:
    return datetime.now(JST)


def _log(msg: str) -> None:
    print(f"[picks_build] {msg}", flush=True)


class BuildLock:
    """単純ロック。--force ありなら無視して続行"""
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
            try:
                self.path.unlink(missing_ok=True)
            except Exception:
                pass

# ======== ドメイン処理 ========================================================

@dataclass
class UniverseRow:
    code: str
    name: str
    sector_name: Optional[str] = None


def _iter_universe(limit: Optional[int] = None) -> List[UniverseRow]:
    """
    StockMaster → ユニバース行配列。limit 指定で先頭から制限。
    """
    qs = StockMaster.objects.all().values_list("code", "name", "sector_name")
    out: List[UniverseRow] = []
    for code, name, sector_name in qs:
        out.append(UniverseRow(str(code), str(name), sector_name or None))
        if limit and len(out) >= limit:
            break
    return out


def _liquid_enough(code: str) -> bool:
    """
    軽量の流動性チェック：直近終値×100株 >= MIN_NOTIONAL
    """
    try:
        df = get_prices(code, 10)
        if df is None or len(df) == 0:
            return False
        close = float(df["close"].iloc[-1])
        return (close * LOT_SIZE) >= MIN_NOTIONAL
    except Exception:
        return False


def _confidence_from_feat(feat_df) -> float:
    """
    仮のAI信頼度(1.0〜5.0)。将来は仮想売買の勝率などを合成。
    """
    try:
        last = feat_df.iloc[-1]
        atr_pct = float(last.get("atr_pct", 0.02))  # ATR/Price
        # 直近60日で欠損行があった比率
        nan_rate = float((feat_df.isna().sum(axis=1).iloc[-60:] > 0).mean())
        base = 3.0
        if atr_pct < 0.03:
            base += 0.5
        if nan_rate < 0.05:
            base += 0.5
        else:
            base -= 0.5
        return float(max(1.0, min(5.0, round(base, 2))))
    except Exception:
        return 2.5


def _build_one(row: UniverseRow) -> Optional[Dict[str, Any]]:
    """
    1銘柄処理：価格→特徴量→スコア→信頼度→提案（Entry/TP/SL/数量）
    """
    try:
        df = get_prices(row.code, 180)
        if df is None or len(df) < MIN_BARS:
            return None

        feat = compute_features(df)
        if feat is None or len(feat) == 0:
            return None

        score = float(score_sample(feat, mode=DEFAULT_MODE, horizon=DEFAULT_HORIZON))
        score = max(0.0, min(100.0, score))  # クリップ

        conf = _confidence_from_feat(feat)
        close = float(df["close"].iloc[-1])

        # 短期×攻め：やや攻め寄りの幅
        entry = round(close * 0.994, 1)
        tp    = round(close * 1.045, 1)
        sl    = round(close * 0.965, 1)

        per_loss = max(1.0, entry - sl)
        target_loss = 20_000.0  # 2万円目安（テスト用固定）
        qty = max(LOT_SIZE, int(target_loss / per_loss / LOT_SIZE) * LOT_SIZE)

        item = {
            "code": row.code,
            "name": row.name,
            "sector": row.sector_name or "",
            "price": close,
            "score": round(score, 1),        # 総合得点 0–100
            "confidence": round(conf, 2),    # AI信頼度 1–5
            "entry": entry, "tp": tp, "sl": sl,
            "qty": qty,
            "required_cash": int(round(entry * qty)),
            "exp_profit": int(round((tp - entry) * qty)),
            "exp_loss": int(round((entry - sl) * qty)),
            "reasons": [
                f"RSI={feat['rsi'].iloc[-1]:.0f}",
                f"MACDヒスト={feat.get('macd_hist', [0])[-1]:+.3f}",
                f"VWAP乖離={feat.get('vwap_diff_pct', [0])[-1]:+.2%}",
                f"近5日={feat.get('ret_5d', [0])[-1]:+.2%}",
                f"ATR/Price={feat.get('atr_pct', [0])[-1]:.2%}",
            ],
        }
        return item
    except Exception:
        return None

# ======== コマンド本体 ========================================================

class Command(BaseCommand):
    help = "Build AI picks snapshot (short × aggressive). Use --sample N to limit universe."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="ignore lock file and run")
        parser.add_argument("--sample", type=int, default=None,
                            help="limit universe size for testing (e.g., 300)")

    def handle(self, *args, **options):
        force = bool(options.get("force", False))
        sample: Optional[int] = options.get("sample")

        started = time.time()
        label = f"{DEFAULT_HORIZON}/{DEFAULT_MODE}"
        if sample:
            label += f" sample={sample}"
        _log(f"start {label}")

        with BuildLock(LOCK_PATH, force=force):
            items = self._build_snapshot(sample=sample)

        dur = round(time.time() - started, 1)
        _log(f"done items={len(items)} dur={dur}s")

    # ------------------------------------------------------------------

    def _build_snapshot(self, sample: Optional[int]) -> List[Dict[str, Any]]:
        # 1) ユニバース
        uni = _iter_universe(limit=sample)
        _log(f"universe={len(uni)}")

        # 2) 流動性フィルタ
        uni2: List[UniverseRow] = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fut = {ex.submit(_liquid_enough, r.code): r for r in uni}
            for f in as_completed(fut):
                try:
                    if f.result():
                        uni2.append(fut[f])
                except Exception:
                    pass
        _log(f"after liquidity={len(uni2)}")

        # 3) 候補生成（並列）
        results: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fut = {ex.submit(_build_one, r): r for r in uni2}
            for f in as_completed(fut):
                try:
                    itm = f.result()
                    if itm:
                        results.append(itm)
                except Exception:
                    pass

        results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        topn = results[:10]

        # 4) スナップショット保存（空なら前回維持）
        meta = {
            "ts": _now_jst().isoformat(timespec="seconds"),
            "mode": DEFAULT_MODE,
            "horizon": DEFAULT_HORIZON,
            "tone": DEFAULT_TONE,
            "universe": len(uni),
            "universe_liquid": len(uni2),
            "version": "picks-v3.2",
            "sample": sample or 0,
        }

        latest = PICKS_DIR / f"latest_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"
        hist   = PICKS_DIR / f"{_now_jst().strftime('%Y%m%d_%H%M%S')}_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"

        if not topn:
            if latest.exists():
                _log("no new items; keep previous snapshot")
                return json.loads(latest.read_text(encoding="utf-8")).get("items", [])
            snap0 = {"meta": meta, "items": []}
            latest.write_text(json.dumps(snap0, ensure_ascii=False, indent=2), encoding="utf-8")
            hist.write_text(json.dumps(snap0, ensure_ascii=False, indent=2), encoding="utf-8")
            return []

        snap = {"meta": meta, "items": topn}
        txt = json.dumps(snap, ensure_ascii=False, indent=2)
        latest.write_text(txt, encoding="utf-8")
        hist.write_text(txt, encoding="utf-8")
        return topn