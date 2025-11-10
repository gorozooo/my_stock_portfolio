# -*- coding: utf-8 -*-
from __future__ import annotations

"""
AIピック生成コマンド（FULL/LITE 共通）
- ネット取得で“タイムアウトによる取りこぼし”をしない設計
  * 個々の銘柄処理で fut.result(timeout=…) は使わない
  * 進行管理は「全体の予算(budget)」でのみ制御（超えたら収集を打ち切る）
- UI は media/aiapp/picks/latest.json / latest_full.json / latest_lite.json を参照
- 依存：
    aiapp.services.fetch_price.get_prices(code, nbars) -> pd.DataFrame(index=Date, cols=[open,high,low,close,volume])
    aiapp.models.features.compute_features(df) -> 特徴量ベクトル（score用）
    aiapp.models.scoring.score_sample(feat, mode, horizon) -> 実数スコア
"""

import os
import json
import time
import math
import pathlib
import datetime as dt
from typing import List, Tuple, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, ALL_COMPLETED, FIRST_COMPLETED

import numpy as np
import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_sample

# -------- パス類 --------
PICKS_DIR = pathlib.Path(getattr(settings, "MEDIA_ROOT", "media")) / "aiapp" / "picks"
UNIVERSE_DIR = pathlib.Path("aiapp/data/universe")

# -------- 環境変数（既定は“開発でも通す”側に寄せる） --------
MIN_SCORE = float(os.getenv("AIAPP_MIN_SCORE", "0.0"))             # 最低スコア（LITE/FULL共通の足切り）
REQUIRE_TREND = bool(int(os.getenv("AIAPP_REQUIRE_TREND", "0")))   # トレンド必須にするか
SKIP_LIQ = bool(int(os.getenv("AIAPP_SKIP_LIQ", "1")))             # 流動性チェック省略（テスト優先）
ALLOW_ETF = bool(int(os.getenv("AIAPP_ALLOW_ETF", "1")))           # ETF/ETN を許可

# エントリー/TP/SLの暫定“短期・攻め”ルール（ユーザー合意版）
#  - high/low の 14日平均レンジ（簡易ATR）を使用
#  - entry  = last + 0.05 * ATR（軽くブレイクを要求）
#  - tp     = entry + 1.5 * ATR
#  - sl     = entry - 1.0 * ATR
ENTRY_ATR_K = 0.05
TP_ATR_K = 1.5
SL_ATR_K = 1.0

# UI 表示を安定させるため、丸めは “整数円” に寄せる
def _yen(v: float | int | None) -> Optional[int]:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return int(round(float(v)))


# -------- 共通ユーティリティ --------
def _ensure_dir(p: pathlib.Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _load_universe(name: str, sample: Optional[int], head: Optional[int]) -> List[Tuple[str, str]]:
    """
    ユニバース読み込み
      - all / jp-all: DBの StockMaster 全件
      - ファイル名: aiapp/data/universe/<name>.txt（コード1行ずつ）
    返り値: [(code, name), ...]
    """
    if name.lower() in ("all", "jp-all", "jpall"):
        qs = list(StockMaster.objects.values_list("code", "name"))
    else:
        path = UNIVERSE_DIR / f"{name}.txt"
        if not path.exists():
            raise CommandError(f"universe file not found: {path}")
        codes = [c.strip() for c in path.read_text().splitlines() if c.strip()]
        # 名前は DB から補完（なければコードをそのまま）
        name_map: Dict[str, str] = {
            c: (StockMaster.objects.filter(code=c).values_list("name", flat=True).first() or c)
            for c in codes
        }
        qs = [(c, name_map.get(c, c)) for c in codes]

    if head:
        qs = qs[: int(head)]
    if sample and len(qs) > sample:
        qs = qs[: sample]
    return qs


def _json_path(tag: str) -> pathlib.Path:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return PICKS_DIR / f"{ts}_{tag}.json"


def _link_latest(src: pathlib.Path, alias: str):
    """
    latest系ファイルへのシンボリックリンク（不可ならコピー）
    """
    dst = PICKS_DIR / alias
    try:
        if dst.exists() or dst.is_symlink():
            dst.unlink()
    except Exception:
        pass
    try:
        # 相対リンクで作る（同一ディレクトリ内なので名前だけでOK）
        dst.symlink_to(src.name)
    except Exception:
        try:
            dst.write_bytes(src.read_bytes())
        except Exception:
            pass


def _calc_atr_like(df: pd.DataFrame, window: int = 14) -> float:
    """
    簡易ATR: (high - low) の移動平均
    df: 必須列 [high, low]
    """
    rng = (df["high"] - df["low"]).rolling(window).mean()
    v = float(rng.iloc[-1]) if len(rng) else float("nan")
    return 0.0 if (math.isnan(v) or math.isinf(v)) else v


def _score_to_100(s: float) -> int:
    """
    任意実数スコア s を 0–100 に正規化（暫定・単純）
      - 0 を基準 50 点
      - 1.0 で +10 点、-1.0 で -10 点（レンジは±∞だがクリップ）
    """
    raw = 50.0 + 10.0 * float(s)
    return int(max(0, min(100, round(raw))))


def _score_to_stars(s100: int) -> int:
    """
    0–100 を ⭐️1–5 へ
    0–39=1, 40–54=2, 55–69=3, 70–84=4, 85–100=5
    """
    if s100 >= 85:
        return 5
    if s100 >= 70:
        return 4
    if s100 >= 55:
        return 3
    if s100 >= 40:
        return 2
    return 1


def _make_item(code: str, name: str, df: pd.DataFrame, mode: str, horizon: str) -> Optional[Dict[str, Any]]:
    """
    1銘柄分のスコアリング → アイテム生成
    - df: 必須列 [open, high, low, close, volume]
    """
    if df is None or df.empty or len(df) < 30:
        return None

    # 特徴量 → スコア
    feat = compute_features(df)
    s = float(score_sample(feat, mode=mode, horizon=horizon))

    # 足切り（開発中は緩め。MIN_SCORE は .env で調整）
    if s < MIN_SCORE:
        return None

    # 現在値（＝直近終値）
    last = float(df["close"].iloc[-1])

    # 簡易ATR
    atr = _calc_atr_like(df, window=14)

    # Entry/TP/SL（“短期・攻め” 暫定ルール）
    # 端数は整数円へ（UIと一貫）
    entry = _yen(last + ENTRY_ATR_K * atr)
    tp = _yen((entry if entry is not None else last) + TP_ATR_K * atr)
    sl = _yen((entry if entry is not None else last) - SL_ATR_K * atr)

    # 0除算回避のための最小ロット値
    lot = 100
    req_cash = entry * lot if entry is not None else last * lot
    est_pl = (tp - entry) * lot if (tp is not None and entry is not None) else None
    est_loss = (entry - sl) * lot if (sl is not None and entry is not None) else None

    # 0–100 → ⭐️1–5
    s100 = _score_to_100(s)
    stars = _score_to_stars(s100)

    item = {
        "code": code,
        "name": name,
        "name_norm": name,         # 将来の表記ゆれ補正用
        "sector": "",              # 後で DB から埋める
        "last_close": _yen(last),
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "score": round(s, 3),      # “(0.227 …)” はテンプレ側で非表示にしている想定
        "score_100": s100,
        "stars": stars,
        "qty": lot,
        "required_cash": _yen(req_cash),
        "est_pl": _yen(est_pl) if est_pl is not None else None,
        "est_loss": _yen(est_loss) if est_loss is not None else None,
        # LITE 用の簡易理由（数値）
        "reasons": {
            "trend_20d_pct": float(df["close"].pct_change(20).iloc[-1]) * 100.0 if len(df) >= 21 else 0.0,
            "vol_ratio_20d": float(
                (df["volume"].iloc[-1]) / max(1.0, float(df["volume"].rolling(20).mean().iloc[-1] or 0.0))
            ) if len(df) >= 20 else 1.0,
            "atr_like": float(atr),
        },
        # 表示用のテキストはテンプレ側で整形しているため、ここではraw数値中心
    }
    return item


def _build_items(
    codes: List[Tuple[str, str]],
    budget_sec: int,
    nbars: int,
    mode: str,
    horizon: str,
    max_workers: int = 8,
) -> List[Dict[str, Any]]:
    """
    タイムボックス内で並列取得・計算を行い、成功した分だけ返す。
    重要：個々の処理にタイムアウトを設けず、全体の budget でだけ制御する。
    """
    start = time.time()
    items: List[Dict[str, Any]] = []

    def work(code: str, name: str) -> Optional[Dict[str, Any]]:
        # 価格取得：ネット事情により遅延しても「待つ」
        df = get_prices(code, nbars)
        if df is None or df.empty:
            return None
        try:
            return _make_item(code, name, df, mode=mode, horizon=horizon)
        except Exception:
            return None

    # すべて発行（個別タイムアウトなし）
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        fut_map = {ex.submit(work, c, n): (c, n) for c, n in codes}

        # “as_completed” は完了したものから順に取れる。ここで budget を見る。
        for fut in as_completed(fut_map):
            # 先に予算超過を判定（“取りこぼしを避ける”ため、超過したら残りは無視して早期終了）
            elapsed = time.time() - start
            if elapsed > max(0, budget_sec):
                break

            try:
                it = fut.result()  # ここで個別タイムアウトは“設けない”
                if it:
                    items.append(it)
            except Exception:
                # 個別エラーは握りつぶし（ネット不調時に全滅しないため）
                pass

    # スコア降順でトップ10（UI側の想定に合わせる）
    items = sorted(items, key=lambda x: x.get("score_100", 0), reverse=True)[:10]
    return items


class Command(BaseCommand):
    help = "AIピック生成（完全版/ライト・スナップショット対応）"

    def add_arguments(self, parser):
        parser.add_argument("--universe", default="all", help="all / nk225 / quick_100 / <file name>")
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--head", type=int, default=None)
        parser.add_argument("--budget", type=int, default=90, help="秒")
        parser.add_argument("--nbars", type=int, default=180)
        parser.add_argument("--nbars-lite", dest="nbars_lite", type=int, default=60, help="ライトモード時の足本数")
        parser.add_argument("--use-snapshot", dest="use_snapshot", action="store_true", help="夜間スナップショット利用")
        parser.add_argument("--lite-only", action="store_true", help="日中ライト表示用")
        parser.add_argument("--force", action="store_true")

        # 本番仕様に寄せたモード指定（CLIから明示できるように）
        parser.add_argument("--style", default="aggressive", choices=["aggressive", "normal", "defensive"])
        parser.add_argument("--horizon", default="short", choices=["short", "mid", "long"])

    def handle(self, *args, **opts):
        universe = opts["universe"]
        sample = opts["sample"]
        head = opts["head"]
        budget = int(opts["budget"])
        nbars = int(opts.get("nbars", 180))
        nbars_lite = int(opts.get("nbars_lite", 60))
        use_snap = bool(opts.get("use_snapshot", False))
        lite = bool(opts["lite_only"])
        force = bool(opts["force"])
        style = str(opts.get("style") or "aggressive")
        horizon = str(opts.get("horizon") or "short")

        _ensure_dir(PICKS_DIR)

        codes = _load_universe(universe, sample, head)
        if not codes:
            self.stdout.write(self.style.WARNING("[picks_build] universe=0"))
            # 空JSONを出力（UIが壊れないように）
            p = _json_path("latest_empty")
            p.write_text(json.dumps({"items": [], "mode": "EMPTY", "updated_at": dt.datetime.now().isoformat()},
                                    ensure_ascii=False))
            _link_latest(p, "latest.json")
            return

        if lite:
            self.stdout.write(f"[picks_build] start LITE universe={len(codes)} budget={budget}s")
            items = _build_items(codes, budget, nbars_lite, mode=style, horizon=horizon)
            # セクター名埋め
            if items:
                sec_map: Dict[str, str] = {
                    c: s for c, s in StockMaster.objects.filter(code__in=[x["code"] for x in items])
                    .values_list("code", "sector_name")
                }
                for it in items:
                    it["sector"] = sec_map.get(it["code"], "") or ""

            p = _json_path("short_aggressive_lite")
            payload = {
                "items": items,
                "mode": "LIVE-FAST",
                "updated_at": dt.datetime.now().isoformat(),
            }
            p.write_text(json.dumps(payload, ensure_ascii=False))
            _link_latest(p, "latest_lite.json")
            _link_latest(p, "latest.json")

            if items:
                self.stdout.write(f"[picks_build] done (lite) items={len(items)} -> {p}")
            else:
                self.stdout.write(self.style.WARNING("[picks_build] items=0 (empty json emitted)"))
            return

        # FULL
        self.stdout.write(f"[picks_build] start FULL universe={len(codes)} budget={budget}s")
        items = _build_items(codes, budget, nbars, mode=style, horizon=horizon)

        # セクター名埋め
        if items:
            sec_map2: Dict[str, str] = {
                c: s for c, s in StockMaster.objects.filter(code__in=[x["code"] for x in items])
                .values_list("code", "sector_name")
            }
            for it in items:
                it["sector"] = sec_map2.get(it["code"], "") or ""

        p = _json_path("short_aggressive_full")
        payload = {
            "items": items,
            "mode": "SNAPSHOT" if use_snap else "FULL",
            "updated_at": dt.datetime.now().isoformat(),
        }
        p.write_text(json.dumps(payload, ensure_ascii=False))
        _link_latest(p, "latest_full.json")
        _link_latest(p, "latest.json")

        if items:
            self.stdout.write(f"[picks_build] done (full) items={len(items)} -> {p}")
        else:
            self.stdout.write(self.style.WARNING("[picks_build] items=0 (empty json emitted)"))