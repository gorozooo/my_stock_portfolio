# aiapp/management/commands/build_behavior_dataset.py
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
行動ログ × 市場データ 統合データセット生成コマンド

目的
------
- /media/aiapp/simulate/*.jsonl に保存された「自動シミュレ」ログと、
  市場データ（価格・簡易特徴量）を突き合わせて、
  「行動 × 市場 × 結果」の 1行レコードを JSONL で吐き出す。

出力
------
- /media/aiapp/behavior/ ディレクトリを作成
- 本日の日付で JSONL を生成（例: 20251125_behavior_dataset.jsonl）
- 直近の出力を参照しやすいように latest_behavior.jsonl も作成

想定される 1 レコードの主要フィールド（例）
------
{
  "ts": "...",                 # シミュレ実行時刻（元の JSONL より）
  "user_id": 1,
  "mode": "demo" / "live",

  "code": "6723",
  "name": "ルネサス",
  "sector": "電気機器",

  "entry": 2812.0,
  "tp": 2890.0,
  "sl": 2740.0,

  "qty_rakuten": 100,
  "qty_matsui": 100,

  "est_pl_rakuten": ...,
  "est_loss_rakuten": ...,
  "est_pl_matsui": ...,
  "est_loss_matsui": ...,

  "eval_label_rakuten": "win" / "lose" / "flat" / null,
  "eval_pl_rakuten": ...,
  "eval_label_matsui": "...",
  "eval_pl_matsui": ...,
  "eval_close_px": 2890.0,
  "eval_close_date": "2025-11-30",
  "eval_horizon_days": 5,

  "last_close": 2810.0,
  "atr_14": 25.3,
  "slope_20": 1.23,
  "trend_daily": "up" / "down" / "flat",

  "created_date": "2025-11-25"
}
"""

import json
from dataclasses import dataclass, asdict
from datetime import date as _date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

# ---- 価格＆特徴量系のサービス/モデルを“あれば”使う ----
try:
    from aiapp.services.fetch_price import get_prices  # type: ignore
except Exception:  # pragma: no cover
    def get_prices(code: str, nbars: int = 120, period: str = "1y") -> pd.DataFrame:  # type: ignore
        return pd.DataFrame()

try:
    from aiapp.models.features import make_features, FeatureConfig  # type: ignore
except Exception:  # pragma: no cover
    make_features = None
    FeatureConfig = None

try:
    from aiapp.models import StockMaster  # type: ignore
except Exception:  # pragma: no cover
    StockMaster = None  # type: ignore


# =========================================================
# データ構造
# =========================================================

@dataclass
class BehaviorRow:
    # 元のシミュレ情報
    ts: Optional[str] = None
    user_id: Optional[int] = None
    mode: Optional[str] = None

    code: Optional[str] = None
    name: Optional[str] = None
    sector: Optional[str] = None

    price_date: Optional[str] = None

    entry: Optional[float] = None
    tp: Optional[float] = None
    sl: Optional[float] = None

    qty_rakuten: Optional[float] = None
    qty_matsui: Optional[float] = None

    est_pl_rakuten: Optional[float] = None
    est_loss_rakuten: Optional[float] = None
    est_pl_matsui: Optional[float] = None
    est_loss_matsui: Optional[float] = None

    reasons_text: Optional[List[str]] = None

    # 評価結果（ai_sim_eval による）
    eval_label_rakuten: Optional[str] = None
    eval_pl_rakuten: Optional[float] = None
    eval_label_matsui: Optional[str] = None
    eval_pl_matsui: Optional[float] = None
    eval_close_px: Optional[float] = None
    eval_close_date: Optional[str] = None
    eval_horizon_days: Optional[int] = None

    # 市場データ（簡易特徴量）
    last_close: Optional[float] = None
    atr_14: Optional[float] = None
    slope_20: Optional[float] = None
    trend_daily: Optional[str] = None  # "up" / "down" / "flat" / None

    # メタ
    created_date: Optional[str] = None


# =========================================================
# ユーティリティ
# =========================================================

def _safe_float(x: Any) -> Optional[float]:
    if x in (None, "", "null"):
        return None
    try:
        v = float(x)
        if not np.isfinite(v):
            return None
        return v
    except Exception:
        return None


def _safe_int(x: Any) -> Optional[int]:
    if x in (None, "", "null"):
        return None
    try:
        return int(x)
    except Exception:
        return None


def _to_naive_date(d: Any) -> Optional[_date]:
    if isinstance(d, _date):
        return d
    if isinstance(d, timezone.datetime):
        return d.date()
    if isinstance(d, str):
        s = d.strip()
        if not s:
            return None
        s = s.replace("/", "-")
        try:
            y, m, day = s.split("-")
            return _date(int(y), int(m), int(day))
        except Exception:
            return None
    return None


def _get_price_date_from_rec(rec: Dict[str, Any]) -> Optional[_date]:
    """
    シミュレ記録から「価格基準日」を推定する。
    - price_date があればそれを優先
    - 無ければ ts の日付部分
    """
    if rec.get("price_date"):
        d = _to_naive_date(rec["price_date"])
        if d:
            return d

    ts_str = rec.get("ts")
    if isinstance(ts_str, str) and ts_str:
        try:
            dt = timezone.datetime.fromisoformat(ts_str)
        except Exception:
            # オフセット付き等をざっくり削って再トライ
            try:
                base = ts_str.split("+")[0].split("Z")[0]
                dt = timezone.datetime.fromisoformat(base)
            except Exception:
                dt = None
        if dt is not None:
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_default_timezone())
            dt = timezone.localtime(dt)
            return dt.date()

    return None


def _load_sector_map(codes: List[str]) -> Dict[str, Tuple[str, str]]:
    """
    StockMaster があれば code -> (name, sector) のマップを作る。
    """
    result: Dict[str, Tuple[str, str]] = {}
    if not codes or StockMaster is None:
        return result

    try:
        qs = (
            StockMaster.objects
            .filter(code__in=codes)
            .values("code", "name", "sector_name")
        )
        for r in qs:
            c = str(r.get("code") or "").strip()
            if not c:
                continue
            nm = r.get("name") or ""
            sec = r.get("sector_name") or ""
            result[c] = (nm, sec)
    except Exception:
        # 取得失敗時は空のまま返す
        pass

    return result


def _compute_market_features(
    code: str,
    price_date: Optional[_date],
    *,
    nbars: int = 60,
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[str]]:
    """
    市場データから簡易特徴量を計算する:
      - last_close
      - atr_14
      - slope_20
      - trend_daily ("up"/"down"/"flat")
    """
    code_str = str(code).strip()
    if not code_str:
        return None, None, None, None

    try:
        df = get_prices(code_str, nbars=nbars, period="1y")
    except Exception:
        df = pd.DataFrame()

    if df is None or df.empty:
        return None, None, None, None

    # index を DatetimeIndex に揃える
    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df.index = pd.to_datetime(df.index)
        except Exception:
            return None, None, None, None

    # price_date が指定されていれば、その日までのデータに限定
    if price_date is not None:
        df = df[df.index.date <= price_date]
        if df.empty:
            return None, None, None, None

    # 終値
    if "Close" not in df.columns:
        return None, None, None, None

    close_s = df["Close"].astype("float64")

    # last_close
    last_close = float(close_s.iloc[-1])

    # ATR14 を簡易計算 (High/Low/Close があれば)
    atr_14: Optional[float] = None
    if all(c in df.columns for c in ("High", "Low", "Close")):
        high = df["High"].astype("float64")
        low = df["Low"].astype("float64")
        close_prev = df["Close"].astype("float64").shift(1)

        tr1 = high - low
        tr2 = (high - close_prev).abs()
        tr3 = (low - close_prev).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr_s = tr.rolling(window=14, min_periods=1).mean()
        if not atr_s.empty:
            atr_14 = float(atr_s.iloc[-1])

    # slope_20（直近20本の単回帰の傾き）
    slope_20: Optional[float] = None
    trend_daily: Optional[str] = None
    try:
        window = 20
        sub = close_s.dropna().iloc[-window:]
        if len(sub) >= 3:
            x = np.arange(len(sub), dtype="float64")
            y = sub.values.astype("float64")
            # 最小二乗法の傾き
            x_mean = x.mean()
            y_mean = y.mean()
            denom = ((x - x_mean) ** 2).sum()
            if denom != 0:
                slope = float(((x - x_mean) * (y - y_mean)).sum() / denom)
                slope_20 = slope
                # 傾きに応じてざっくりトレンド判定
                # 閾値は適当だが、0.0 付近をフラット扱いにする
                if slope > 0:
                    trend_daily = "up"
                elif slope < 0:
                    trend_daily = "down"
                else:
                    trend_daily = "flat"
    except Exception:
        pass

    return last_close, atr_14, slope_20, trend_daily


def _build_behavior_row(
    rec: Dict[str, Any],
    sector_map: Dict[str, Tuple[str, str]],
    created_date: _date,
) -> Optional[BehaviorRow]:
    """
    1つのシミュレレコードから BehaviorRow を構築する。
    不足が激しい場合は None を返す。
    """
    code = str(rec.get("code") or "").strip()
    if not code:
        return None

    # ユーザーID / モード / TS
    ts_str = rec.get("ts")
    user_id = _safe_int(rec.get("user_id"))
    mode = rec.get("mode")
    mode = mode.lower() if isinstance(mode, str) else None

    # 価格基準日
    price_date = _get_price_date_from_rec(rec)

    # StockMaster から name/sector を補完
    name: Optional[str] = None
    sector: Optional[str] = None
    if code in sector_map:
        nm, sec = sector_map[code]
        if nm:
            name = nm
        if sec:
            sector = sec

    # シミュレの基本フィールド
    entry = _safe_float(rec.get("entry"))
    tp = _safe_float(rec.get("tp"))
    sl = _safe_float(rec.get("sl"))

    qty_rakuten = _safe_float(rec.get("qty_rakuten"))
    qty_matsui = _safe_float(rec.get("qty_matsui"))

    est_pl_rakuten = _safe_float(rec.get("est_pl_rakuten"))
    est_loss_rakuten = _safe_float(rec.get("est_loss_rakuten"))
    est_pl_matsui = _safe_float(rec.get("est_pl_matsui"))
    est_loss_matsui = _safe_float(rec.get("est_loss_matsui"))

    # 理由テキスト（あれば）
    reasons_raw = rec.get("reasons_text")
    if isinstance(reasons_raw, list):
        reasons_text = [str(x) for x in reasons_raw]
    else:
        reasons_text = None

    # 評価結果
    eval_label_rakuten = rec.get("eval_label_rakuten")
    eval_pl_rakuten = _safe_float(rec.get("eval_pl_rakuten"))
    eval_label_matsui = rec.get("eval_label_matsui")
    eval_pl_matsui = _safe_float(rec.get("eval_pl_matsui"))
    eval_close_px = _safe_float(rec.get("eval_close_px"))
    eval_close_date = rec.get("eval_close_date")
    eval_horizon_days = _safe_int(rec.get("eval_horizon_days"))

    # 市場データ（簡易特徴量）
    last_close, atr_14, slope_20, trend_daily = _compute_market_features(
        code=code,
        price_date=price_date,
        nbars=60,
    )

    row = BehaviorRow(
        ts=str(ts_str) if ts_str is not None else None,
        user_id=user_id,
        mode=mode,
        code=code,
        name=name or rec.get("name"),
        sector=sector,
        price_date=price_date.isoformat() if price_date else None,
        entry=entry,
        tp=tp,
        sl=sl,
        qty_rakuten=qty_rakuten,
        qty_matsui=qty_matsui,
        est_pl_rakuten=est_pl_rakuten,
        est_loss_rakuten=est_loss_rakuten,
        est_pl_matsui=est_pl_matsui,
        est_loss_matsui=est_loss_matsui,
        reasons_text=reasons_text,
        eval_label_rakuten=str(eval_label_rakuten) if eval_label_rakuten is not None else None,
        eval_pl_rakuten=eval_pl_rakuten,
        eval_label_matsui=str(eval_label_matsui) if eval_label_matsui is not None else None,
        eval_pl_matsui=eval_pl_matsui,
        eval_close_px=eval_close_px,
        eval_close_date=str(eval_close_date) if eval_close_date is not None else None,
        eval_horizon_days=eval_horizon_days,
        last_close=last_close,
        atr_14=atr_14,
        slope_20=slope_20,
        trend_daily=trend_daily,
        created_date=created_date.isoformat(),
    )

    return row


# =========================================================
# メインコマンド
# =========================================================

class Command(BaseCommand):
    """
    行動ログ × 市場データ の統合データセットを生成するコマンド。

    使い方:
      python manage.py build_behavior_dataset
      python manage.py build_behavior_dataset --days 5
      python manage.py build_behavior_dataset --user 1
    """

    help = "AI自動シミュレログと市場データを統合した行動学習用データセットを出力する"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=getattr(settings, "AIAPP_SIM_HORIZON_DAYS", 5),
            help="評価 horizon_days の目安（今は ai_sim_eval の結果をそのまま使うので主に表示用）",
        )
        parser.add_argument(
            "--user",
            type=int,
            default=None,
            help="特定ユーザーIDのシミュレのみを対象にしたい場合",
        )

    def handle(self, *args, **options) -> None:
        horizon_days: int = options["days"]
        user_filter: Optional[int] = options["user"]

        today = timezone.localdate()
        created_date = today

        sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"
        if not sim_dir.exists():
            self.stdout.write(
                self.style.WARNING(f"[build_behavior_dataset] シミュレディレクトリが存在しません: {sim_dir}")
            )
            return

        behavior_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "behavior"
        behavior_dir.mkdir(parents=True, exist_ok=True)

        # 出力ファイルパス
        out_name = f"{today.strftime('%Y%m%d')}_behavior_dataset.jsonl"
        out_path = behavior_dir / out_name
        latest_path = behavior_dir / "latest_behavior.jsonl"

        self.stdout.write(
            f"[build_behavior_dataset] simulate_dir={sim_dir} -> out={out_path.name} (horizon_days={horizon_days}, user={user_filter})"
        )

        # まず全シミュレログを読み込んで、対象コード一覧を集める
        simulate_records: List[Dict[str, Any]] = []
        codes: List[str] = []

        for path in sorted(sim_dir.glob("*.jsonl")):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  読み込み失敗: {path.name}: {e}"))
                continue

            for line in text.splitlines():
                raw = line.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except Exception:
                    # 壊れた行はスキップ
                    continue

                # user フィルタ
                if user_filter is not None:
                    uid = rec.get("user_id")
                    if uid != user_filter:
                        continue

                simulate_records.append(rec)
                c = str(rec.get("code") or "").strip()
                if c:
                    codes.append(c)

        if not simulate_records:
            self.stdout.write(self.style.WARNING("[build_behavior_dataset] 対象シミュレ記録がありません。"))
            return

        # StockMaster から name / sector をまとめて取得
        sector_map = _load_sector_map(list(set(codes)))

        # 各レコードから BehaviorRow を構築
        rows: List[BehaviorRow] = []
        for rec in simulate_records:
            row = _build_behavior_row(rec, sector_map=sector_map, created_date=created_date)
            if row is None:
                continue
            rows.append(row)

        if not rows:
            self.stdout.write(self.style.WARNING("[build_behavior_dataset] BehaviorRow が 0 件でした。"))
            return

        # JSONL として書き出し
        try:
            with out_path.open("w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(asdict(row), ensure_ascii=False))
                    f.write("\n")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[build_behavior_dataset] 書き込み失敗: {e}"))
            return

        # latest_behavior.jsonl としても保存
        try:
            if latest_path.exists():
                latest_path.unlink()
            out_path.replace(latest_path)
            # latest をコピーしたので、元の out_path は latest_path になった
            # もう一度 out_path にも残したい場合はコピーする
            with out_path.open("w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(asdict(row), ensure_ascii=False))
                    f.write("\n")
        except Exception:
            # latest の作成に失敗しても致命ではないので無視
            pass

        self.stdout.write(
            self.style.SUCCESS(
                f"[build_behavior_dataset] 出力件数: {len(rows)}件 -> {out_path}"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                "[build_behavior_dataset] 完了（latest_behavior.jsonl も更新済みのはず）"
            )
        )