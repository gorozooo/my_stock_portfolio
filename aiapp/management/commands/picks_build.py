# -*- coding: utf-8 -*-
"""
picks_build
- 日本株ユニバースから「短期×攻め」の10銘柄スナップショットを生成
- 3つの改良点を同梱：
  A) MIN_BARS を 60 に緩和（通過率↑）
  B) 軽量の流動性フィルタ（終値×100株 >= 10万円）
  C) 新規が0件でも前回スナップショットを維持（空振り回避）

使い方:
  python manage.py picks_build
  python manage.py picks_build --force        # ロック無視で強制実行

スナップショット保存先:
  MEDIA_ROOT/aiapp/picks/latest_short_aggressive.json
  （タイムスタンプ付きの履歴ファイルも同フォルダに保存）
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
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
from aiapp.models.scoring import score_sample  # 既存のシグネチャ: (feat, mode, horizon)

# ==== 設定（必要に応じて調整） ===============================================

# パフォーマンス/通過率
MAX_WORKERS = int(os.environ.get("AIAPP_BUILD_WORKERS", "12"))  # サーバ重ければ 8〜12 を目安
MIN_BARS = 60  # 連続足の最低本数（以前の80→60へ緩和）

# 流動性フィルタ（軽量版）：終値×100株が閾値以上
LOT_SIZE = 100
MIN_NOTIONAL = 100_000  # 10万円（様子を見て20万などに上げてもOK）

# スナップショット保存先
MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
PICKS_DIR = MEDIA_ROOT / "aiapp" / "picks"
PICKS_DIR.mkdir(parents=True, exist_ok=True)

# ビルド対象（今回は短期×攻めを確実に）
DEFAULT_HORIZON = "short"
DEFAULT_MODE = "aggressive"
DEFAULT_TONE = "friendly"  # UI表示用メタ

# ロックファイル
LOCK_PATH = PICKS_DIR / ".picks_build.lock"

# ==== 小物ユーティリティ =====================================================

JST = timezone(timedelta(hours=9))


def _now_jst() -> datetime:
    return datetime.now(JST)


def _ts() -> str:
    return _now_jst().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str) -> None:
    print(f"[picks_build] {msg}", flush=True)


class BuildLock:
    """単純なロックファイル。--force の時は無視して続行"""

    def __init__(self, path: Path, force: bool = False) -> None:
        self.path = path
        self.force = force
        self.acquired = False

    def __enter__(self):
        if self.path.exists():
            if self.force:
                _log("lock exists but --force specified; continue")
            else:
                _log("another build is running; exit 202")
                sys.exit(202)
        try:
            self.path.write_text(str(os.getpid()))
            self.acquired = True
        except Exception:
            if self.force:
                _log("lock write failed but --force specified; continue")
            else:
                raise
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.acquired:
            try:
                self.path.unlink(missing_ok=True)
            except Exception:
                pass


# ==== ドメイン処理 ===========================================================

@dataclass
class UniverseRow:
    code: str
    name: str
    sector_name: Optional[str] = None


def _iter_universe(limit: Optional[int] = None) -> List[UniverseRow]:
    """
    StockMaster からユニバースを取得。
    sector_name があれば一緒に持たせる（なければ None）。
    """
    qs = StockMaster.objects.all().values_list("code", "name", "sector_name")
    rows = []
    for i, (code, name, sector_name) in enumerate(qs):
        rows.append(UniverseRow(str(code), str(name), sector_name or None))
        if limit and len(rows) >= limit:
            break
    return rows


def _liquid_enough(code: str) -> bool:
    """
    軽量の流動性チェック。
    - 直近10本の価格が取れるか
    - 最終終値×100株が 10万円以上か
    """
    try:
        df = get_prices(code, 10)
        if df is None or len(df) == 0:
            return False
        px = float(df["close"].iloc[-1])
        return (px * LOT_SIZE) >= MIN_NOTIONAL
    except Exception:
        return False


def _confidence_from_feat(feat_df) -> float:
    """
    仮のAI信頼度スコア（1.0〜5.0）
    - 将来的に「仮想エントリーの勝率」「特徴量の安定性」「乖離の適正」を反映
    - いまは簡易：直近ATR/価格の大小と欠損率で控えめに決める
    """
    try:
        last = feat_df.iloc[-1]
        atr_pct = float(last.get("atr_pct", 0.02))  # 例: ATR/price
        nan_rate = float((feat_df.isna().sum(axis=1).iloc[-60:] > 0).mean())  # 直近60日の欠損率
        base = 3.0
        base += (0.5 if atr_pct < 0.03 else 0.0)
        base += (0.5 if nan_rate < 0.05 else -0.5)
        return float(max(1.0, min(5.0, round(base, 2))))
    except Exception:
        return 2.5


def _build_one(row: UniverseRow) -> Optional[Dict[str, Any]]:
    """
    1銘柄処理：
      - 価格取得
      - 本数判定
      - 特徴量
      - 総合得点（score_sample）
      - 簡易のAI信頼度
    最低限、UIが読む key を揃える。Entry/TP/SL は控えめに自動算出。
    """
    try:
        df = get_prices(row.code, 180)
        if df is None or len(df) < MIN_BARS:
            return None

        feat = compute_features(df)
        if feat is None or len(feat) == 0:
            return None

        score = float(score_sample(feat, mode=DEFAULT_MODE, horizon=DEFAULT_HORIZON))
        score = max(0.0, min(100.0, score))  # 0〜100にクリップ

        conf = _confidence_from_feat(feat)

        # 価格周り
        close = float(df["close"].iloc[-1])

        # エントリー/TP/SL（短期×攻めの暫定ロジック）
        entry = round(close * 0.994, 1)
        tp = round(close * 1.045, 1)
        sl = round(close * 0.965, 1)

        # 数量は外部 sizing に任せず、ここでは仮の固定（UIの枠を満たす）
        # リスク0.02を仮定し、想定損失を2万円以内に収める目安
        per_share_loss = max(1.0, entry - sl)
        target_loss_jpy = 20000.0
        qty = max(LOT_SIZE, int(target_loss_jpy / per_share_loss / LOT_SIZE) * LOT_SIZE)

        required_cash = int(round(entry * qty))
        exp_profit = int(round((tp - entry) * qty))
        exp_loss = int(round((entry - sl) * qty))

        # 表示用の理由（数値＋短文、文章化は view 側で整形済み想定）
        reasons = [
            f"RSI={feat['rsi'].iloc[-1]:.0f}",
            f"MACDヒスト={feat.get('macd_hist', [0])[-1]:+.3f}",
            f"VWAP乖離={feat.get('vwap_diff_pct', [0])[-1]:+.2%}",
            f"近5日={feat.get('ret_5d', [0])[-1]:+.2%}",
            f"ATR/Price={feat.get('atr_pct', [0])[-1]:.2%}",
        ]

        item = {
            "code": row.code,
            "name": row.name,
            "sector": row.sector_name or "",
            "price": close,
            "score": round(score, 1),           # 総合得点（0〜100）
            "confidence": round(conf, 2),       # AI信頼度（⭐︎1〜5）
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "qty": qty,
            "required_cash": required_cash,
            "exp_profit": exp_profit,
            "exp_loss": exp_loss,
            "reasons": reasons,
        }
        return item
    except Exception:
        # 個別銘柄の落ちても全体は止めない
        return None


# ==== コマンド本体 ===========================================================

class Command(BaseCommand):
    help = "Build AI picks snapshot (short × aggressive)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="ignore lock file and run")

    def handle(self, *args, **options):
        force = bool(options.get("force", False))
        start = time.time()
        _log(f"start {DEFAULT_HORIZON}/{DEFAULT_MODE}")

        with BuildLock(LOCK_PATH, force=force):
            items = self._build_snapshot()

        dur = round(time.time() - start, 1)
        _log(f"done items={len(items)} dur={dur}s")

    # ----------------------------------------------------------------------

    def _build_snapshot(self) -> List[Dict[str, Any]]:
        # 1) ユニバース
        uni: List[UniverseRow] = _iter_universe()  # フルユニバース
        _log(f"universe={len(uni)}")

        # 2) 軽量の流動性フィルタ
        uni2: List[UniverseRow] = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fut2 = {ex.submit(_liquid_enough, r.code): r for r in uni}
            for fut in as_completed(fut2):
                r = fut2[fut]
                try:
                    if fut.result():
                        uni2.append(r)
                except Exception:
                    pass
        _log(f"after liquidity={len(uni2)}")

        # 3) 並列で候補生成
        results: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(_build_one, r): r for r in uni2}
            for fut in as_completed(futs):
                try:
                    item = fut.result()
                    if item:
                        results.append(item)
                except Exception:
                    # ここでは握りつぶし（全体継続）
                    pass

        # 4) スコア順に並べてTOP10
        results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        topn = results[:10]

        # 5) スナップショット出力（空なら前回を維持）
        meta = {
            "ts": _now_jst().isoformat(timespec="seconds"),
            "mode": DEFAULT_MODE,
            "horizon": DEFAULT_HORIZON,
            "tone": DEFAULT_TONE,
            "universe": len(uni),
            "universe_liquid": len(uni2),
            "version": "picks-v3.1",
        }

        latest = PICKS_DIR / f"latest_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"
        hist = PICKS_DIR / f"{_now_jst().strftime('%Y%m%d_%H%M%S')}_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"

        if not topn:
            if latest.exists():
                _log("no new items; keep previous snapshot")
                return json.loads(latest.read_text(encoding="utf-8")).get("items", [])
            # 初回だけでも空スナップショットを書き出しておく
            snap0 = {"meta": meta, "items": []}
            latest.write_text(json.dumps(snap0, ensure_ascii=False, indent=2), encoding="utf-8")
            hist.write_text(json.dumps(snap0, ensure_ascii=False, indent=2), encoding="utf-8")
            return []

        snap = {"meta": meta, "items": topn}
        text = json.dumps(snap, ensure_ascii=False, indent=2)
        latest.write_text(text, encoding="utf-8")
        hist.write_text(text, encoding="utf-8")
        return topn