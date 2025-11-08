# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import math
import os
import random
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import pandas as pd
from django.core.management.base import BaseCommand
from django.conf import settings

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices

# features/scoring が存在するなら使う（無ければ軽量モードのみで動作）
try:
    from aiapp.models.features import compute_features
except Exception:
    compute_features = None  # type: ignore

try:
    from aiapp.models.scoring import score_sample
except Exception:
    score_sample = None  # type: ignore

JST = timezone(timedelta(hours=9))

MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
OUT_DIR     = MEDIA_ROOT / "aiapp" / "picks"
UNIV_DIR    = Path("aiapp") / "data" / "universe"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TARGET_TOPN          = 10
FAST_MIN_BARS        = 80     # features用の最低バー数
LIGHT_MIN_BARS       = 40     # 軽量モードの最低バー数
FAST_TIMEOUT_BUDGET  = 180    # 既定のビルド全体時間（秒）※CLIで上書き可
CHUNK_SLEEP_SEC      = 0.0    # スレッド未使用版なのでスリープ無し

# ------------------------- dataclass / utils ----------------------------------
@dataclass
class PickItem:
    code: str
    name: str
    sector: Optional[str]
    score: float
    ai_confidence: float
    price: float
    entry: float
    tp: float
    sl: float
    reasons: List[str]

def _now_jst_str() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

def _load_universe(label: Optional[str], sample: Optional[int]) -> List[Tuple[str,str,Optional[str]]]:
    """
    label: None|'nk225'|'all'
    returns: list of (code, name, sector_name)
    """
    qs = StockMaster.objects.all().values_list("code","name","sector_name").order_by("code")

    if label == "nk225":
        # 優先的に aiapp/data/universe/nk225.txt（1行1コード）を使う
        path = UNIV_DIR / "nk225.txt"
        codes = None
        if path.exists():
            try:
                codes = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
            except Exception:
                codes = None
        if codes:
            rows = list(qs.filter(code__in=codes))
        else:
            # フォールバック：名称に『先物』『ETF』『REIT』などを含まず、適当に頭から拾う
            rows = [(c,n,s) for (c,n,s) in qs if "ETF" not in (n or "")][:250]
    else:
        rows = list(qs)

    if sample:
        rows = rows[: int(sample)]
    return list(rows)

def _rank_lightweight(code: str, nbars: int = 180) -> Optional[Tuple[float,float,float,float,List[str]]]:
    """
    超軽量スコアリング：
      score = z(ROC5) + 0.5*z(ROC20) - 0.3*z(ATR/price)
      ai_conf = clamped( 3 + sign(ROC20)*1 + (|ROC5|<8% ? 0.5 : 0) )
    戻り値: (score, ai_conf, last_close, tp, sl, reasons)
    """
    df = get_prices(code, nbars)
    if df is None or df.empty or len(df) < LIGHT_MIN_BARS:
        return None
    df = df.dropna(subset=["close"])
    if len(df) < LIGHT_MIN_BARS:
        return None

    close = df["close"].astype("float")
    last  = float(close.iloc[-1])
    # 5日/20日リターン
    def pct(n: int) -> float:
        if len(close) <= n or close.iloc[-n-1] == 0:
            return 0.0
        return (float(close.iloc[-1]) / float(close.iloc[-n-1]) - 1.0) * 100.0

    roc5  = pct(5)
    roc20 = pct(20)

    # ATR 近似：高低差の平均（高速化）
    if set(["high","low"]) <= set(df.columns):
        hl = (df["high"] - df["low"]).abs()
        atr = float(hl.tail(14).mean())
    else:
        # high/low が無い場合は終値の真数変化で代用
        atr = float(close.pct_change().tail(14).abs().mean() * last)

    atr_pct = 0.0 if last == 0 else (atr / last) * 100.0

    # 標準化（簡易 z-score）
    def z(x: float, mean: float, std: float) -> float:
        return 0.0 if std == 0 else (x-mean)/std

    # 直近分布でざっくり標準化
    r5_series  = close.pct_change(5).dropna()*100
    r20_series = close.pct_change(20).dropna()*100
    atr_series = ((df["high"]-df["low"]).abs()/close*100.0).dropna() if set(["high","low"])<=set(df.columns) else (close.pct_change().abs()*100).dropna()

    score = (
        z(roc5,  float(r5_series.mean()),  float(r5_series.std(ddof=0) or 1))
        + 0.5 * z(roc20, float(r20_series.mean()), float(r20_series.std(ddof=0) or 1))
        - 0.3 * z(atr_pct, float(atr_series.mean()), float(atr_series.std(ddof=0) or 1))
    )

    # 目安TP/SL：+7% / -3.5%（短期・攻め）
    tp = last * 1.07
    sl = last * 0.965

    # AI信頼度（1〜5）
    ai_conf = 3.0
    ai_conf += 1.0 if roc20 > 0 else -0.2
    ai_conf += 0.5 if abs(roc5) < 8.0 else -0.2
    ai_conf = max(1.0, min(5.0, round(ai_conf, 2)))

    reasons = [
        f"5日モメンタム {roc5:+.2f}%",
        f"20日モメンタム {roc20:+.2f}%",
        f"ボラ目安 {atr_pct:.2f}%",
        "（軽量スコアで算出）",
    ]
    return (float(score), float(ai_conf), last, float(tp), float(sl), reasons)

def _rank_full(code: str, nbars: int = 180) -> Optional[Tuple[float,float,float,float,List[str]]]:
    """
    フル：features + score_sample が使えるときだけ。
    使えない場合は None を返す → 呼び出し側で軽量にフォールバック。
    """
    if compute_features is None or score_sample is None:
        return None

    df = get_prices(code, nbars)
    if df is None or df.empty or len(df) < FAST_MIN_BARS:
        return None
    feat = compute_features(df)
    if feat is None or feat.empty:
        return None

    s = float(score_sample(feat, mode="aggressive", horizon="short"))
    last = float(df["close"].iloc[-1])

    # entry/tp/sl は features の典型値があれば使う、なければ軽量ルール
    tp = last * 1.07
    sl = last * 0.965

    # 信頼度は暫定：featuresに 'ai_conf' 列があれば拾う
    ai_conf = 3.0
    try:
        ai_col = [c for c in feat.columns if "ai_conf" in c.lower()]
        if ai_col:
            ai_conf = float(feat[ai_col[-1]].iloc[-1])
            ai_conf = max(1.0, min(5.0, ai_conf))
    except Exception:
        pass

    reasons = [
        "トレンド/モメンタム等の複合スコア（フル）",
        "詳細はfeatures/scoreの内訳に依存",
    ]
    return (s, ai_conf, last, tp, sl, reasons)

def _emit_snapshot(items: List[PickItem], tag: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    path = OUT_DIR / f"picks_{tag}_{ts}.json"
    payload = {
        "generated_at": _now_jst_str(),
        "style": "aggressive",
        "horizon": "short",
        "items": [asdict(x) for x in items],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # 最新のシンボリックリンク
    latest = OUT_DIR / f"latest_{tag}.json"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.symlink_to(path.name)
    except Exception:
        # Windowsや権限で失敗する場合はコピー
        latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[picks_build] wrote {path.name} items={len(items)}")
    return path

# ------------------------------- command -------------------------------------
class Command(BaseCommand):
    help = "Build AI picks snapshot (short x aggressive) with robust fallbacks."

    def add_arguments(self, parser):
        parser.add_argument("--universe", type=str, default=None, help="nk225 / all / None")
        parser.add_argument("--sample", type=int, default=None, help="limit universe size")
        parser.add_argument("--budget", type=int, default=FAST_TIMEOUT_BUDGET, help="build time budget(sec)")
        parser.add_argument("--force", action="store_true")

    # main
    def handle(self, *args, **opts):
        label   = opts.get("universe")
        sample  = opts.get("sample")
        budget  = int(opts.get("budget") or FAST_TIMEOUT_BUDGET)
        force   = bool(opts.get("force"))

        start   = time.time()
        rows    = _load_universe(label, sample)
        print(f"[picks_build] start universe={label or 'all'} sample={sample} budget={budget}s")
        print(f"[picks_build] universe={len(rows)}")

        # 1) フルモード試行（features+score がある場合）
        finalists: List[PickItem] = []
        if compute_features is not None and score_sample is not None:
            for i, (code, name, sector) in enumerate(rows, 1):
                if time.time() - start > budget * 0.6:  # 予算の60%で切り替え
                    print("[picks_build] switch to lightweight (budget guard)")
                    break
                try:
                    r = _rank_full(code)
                except Exception:
                    r = None
                if r is None:
                    continue
                score, ai_conf, last, tp, sl, reasons = r
                item = PickItem(
                    code=code, name=name, sector=sector or "",
                    score=score, ai_confidence=ai_conf,
                    price=last, entry=last, tp=tp, sl=sl, reasons=reasons
                )
                finalists.append(item)
                if len(finalists) >= TARGET_TOPN * 6:
                    # 6倍まで貯めたら十分
                    break

            if finalists:
                # スコア降順で上位抽出
                finalists.sort(key=lambda x: x.score, reverse=True)
                items = finalists[:TARGET_TOPN]
                _emit_snapshot(items, tag="full")
                print(f"[picks_build] done items={len(items)} dur={time.time()-start:.1f}s")
                return

            print("[picks_build] fast_pass=0")

        # 2) 軽量モード（必ず拾う）
        lite_pool: List[PickItem] = []
        for i, (code, name, sector) in enumerate(rows, 1):
            if time.time() - start > budget * 0.95:
                print("[picks_build] budget nearly exhausted; stop collecting")
                break
            try:
                r = _rank_lightweight(code)
            except Exception:
                r = None
            if r is None:
                continue
            score, ai_conf, last, tp, sl, reasons = r
            lite_pool.append(
                PickItem(
                    code=code, name=name, sector=sector or "",
                    score=score, ai_confidence=ai_conf,
                    price=last, entry=last, tp=tp, sl=sl, reasons=reasons
                )
            )

        if lite_pool:
            lite_pool.sort(key=lambda x: x.score, reverse=True)
            items = lite_pool[:TARGET_TOPN]
            _emit_snapshot(items, tag="lite")
            print(f"[picks_build] done items={len(items)} dur={time.time()-start:.1f}s")
            return

        # 3) それでもゼロなら、終値が取れた銘柄から無理やり10件
        print("[picks_build] lightweight=0; emit synthetic fallback")
        synthetic: List[PickItem] = []
        for (code, name, sector) in rows[: TARGET_TOPN * 3]:
            df = get_prices(code, 30)
            if df is None or df.empty:
                continue
            last = float(df["close"].dropna().iloc[-1])
            if not math.isfinite(last) or last <= 0:
                continue
            synthetic.append(
                PickItem(
                    code=code, name=name, sector=sector or "",
                    score=0.0, ai_confidence=2.0,
                    price=last, entry=last, tp=last*1.05, sl=last*0.97,
                    reasons=["データ不十分のため暫定表示"]
                )
            )
            if len(synthetic) >= TARGET_TOPN:
                break

        if not synthetic:
            # 本当に何も取れない場合でも、空で保存してUIが「更新日時」を持てるようにする
            _emit_snapshot([], tag="empty")
            print(f"[picks_build] done items=0 dur={time.time()-start:.1f}s")
            return

        _emit_snapshot(synthetic, tag="synthetic")
        print(f"[picks_build] done items={len(synthetic)} dur={time.time()-start:.1f}s")