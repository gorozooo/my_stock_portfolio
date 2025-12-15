# -*- coding: utf-8 -*-
"""
build_ml_dataset.py

目的:
  media/aiapp/behavior/latest_behavior.jsonl（紙シミュ結果ログ）から
  “ML学習用データセット（X,y）” を正規化して生成する。

出力:
  - media/aiapp/ml/train/YYYY_MM/train.parquet   (推奨: pyarrow必要)
    失敗したら:
  - media/aiapp/ml/train/YYYY_MM/train.csv

  ついでに:
  - media/aiapp/ml/train/latest_train.parquet または latest_train.csv

設計方針:
  - 1行JSON = 1トレード = 1サンプル
  - “未来情報リーク” を入れない（Xは当日確定情報のみ）
  - yは eval_* から作る（win/lose/flat, pl, R, hold_days, tp_first/sl_first）
  - 文字の不可視混入に耐える（NFKC + 制御文字除去）

使い方:
  python manage.py build_ml_dataset --days 180
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

JST = dt_timezone(timedelta(hours=9))

BROKERS = ("rakuten", "sbi", "matsui")


# ----------------------------
# text normalize
# ----------------------------

_CTRL_RE = re.compile(r"[\u0000-\u001F\u007F-\u009F\u200B-\u200D\uFEFF]")

def _clean_text(s: Any) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = _CTRL_RE.sub("", s)
    return s.strip()


def _safe_float(x: Any) -> Optional[float]:
    if x in (None, "", "null"):
        return None
    try:
        v = float(x)
        if not np.isfinite(v):
            return None
        return float(v)
    except Exception:
        return None


def _safe_int(x: Any) -> Optional[int]:
    if x in (None, "", "null"):
        return None
    try:
        return int(x)
    except Exception:
        return None


def _norm_code(code: Any) -> str:
    s = _clean_text(code)
    if s.endswith(".T"):
        s = s[:-2]
    return s


def _to_dt_any(s: Any) -> Optional[datetime]:
    """
    "2025-12-01"
    "2025-12-01T09:00:00+09:00"
    両対応（JSTに寄せてnaiveへ）
    """
    if not s:
        return None
    try:
        ss = _clean_text(s)
        if "T" in ss:
            dt = datetime.fromisoformat(ss.replace("Z", "+00:00"))
            try:
                dt = dt.astimezone(JST)
            except Exception:
                pass
            return dt.replace(tzinfo=None)
        return datetime.fromisoformat(ss).replace(tzinfo=None)
    except Exception:
        return None


def _sum_qty(d: Dict[str, Any]) -> int:
    total = 0
    for b in BROKERS:
        q = _safe_int(d.get(f"qty_{b}"))
        if q is None:
            q = 0
        total += int(q)
    return int(total)


def _sum_pl(d: Dict[str, Any]) -> Optional[float]:
    """
    eval_pl_* を合算。全部 no_position/欠損でも 0 になることがあるので、
    “本当に何も無い古いデータ” は None を返す。
    """
    total = 0.0
    any_found = False
    for b in BROKERS:
        v = _safe_float(d.get(f"eval_pl_{b}"))
        if v is None:
            v = 0.0
        else:
            any_found = True
        total += float(v)
    return float(total) if any_found else None


def _get_label(d: Dict[str, Any]) -> Optional[str]:
    v = d.get("_combined_label")
    if v is None:
        # 古い互換（eval_label_* から推定）
        labels: List[str] = []
        for b in BROKERS:
            s = _clean_text(d.get(f"eval_label_{b}")).lower()
            if s:
                labels.append(s)
        if not labels:
            return None
        sset = set(labels)
        if "win" in sset and "lose" in sset:
            return "mixed"
        if "win" in sset:
            return "win"
        if "lose" in sset:
            return "lose"
        if sset <= {"flat"}:
            return "flat"
        if sset <= {"no_position"}:
            return "skip"
        return "unknown"
    return _clean_text(v).lower()


def _touch_first(d: Dict[str, Any]) -> str:
    # 任意教師: TP先/SL先（順序）
    r = _clean_text(d.get("eval_exit_reason")).lower()
    if r == "hit_tp":
        return "tp_first"
    if r == "hit_sl":
        return "sl_first"
    return "none"


def _hold_days(d: Dict[str, Any]) -> Optional[int]:
    # 任意教師: 保有日数
    v = _safe_int(d.get("eval_horizon_days"))
    if v is not None:
        return int(v)

    en = _to_dt_any(d.get("eval_entry_ts"))
    ex = _to_dt_any(d.get("eval_exit_ts"))
    if en and ex:
        dd = (ex.date() - en.date()).days
        if dd >= 0:
            return int(dd)

    td = _to_dt_any(d.get("trade_date"))
    cd = _to_dt_any(d.get("eval_close_date"))
    if td and cd:
        dd = (cd.date() - td.date()).days
        if dd >= 0:
            return int(dd)

    return None


def _y_r_from_cashrisk(d: Dict[str, Any], y_pl: float) -> Optional[float]:
    """
    y_r（R）を “現金リスク” で割って作る。
      risk_cash = (entry - sl) * qty_total   ※ BUY想定
    side=SELL でも符号が崩れないよう abs を使う。
    """
    qty = _sum_qty(d)
    if qty <= 0:
        return None

    entry = _safe_float(d.get("entry"))
    sl = _safe_float(d.get("sl"))
    if entry is None or sl is None:
        # design_risk があればそれを使う（1株あたり）
        dr = _safe_float(d.get("design_risk"))
        if dr is None:
            return None
        risk_cash = abs(float(dr)) * float(qty)
    else:
        risk_cash = abs(float(entry) - float(sl)) * float(qty)

    if not np.isfinite(risk_cash) or risk_cash <= 0:
        return None

    return float(y_pl) / float(risk_cash)


def _extract_feature_snapshot(d: Dict[str, Any]) -> Dict[str, Any]:
    """
    Xコア:
      feature_snapshot（当日確定）を最優先
    """
    fs = d.get("feature_snapshot")
    if isinstance(fs, dict):
        return dict(fs)
    return {}


# ----------------------------
# categorical id maps (stable)
# ----------------------------

def _load_map(path: Path) -> Dict[str, int]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_map(path: Path, mp: Dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mp, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _get_id(mp: Dict[str, int], key: str) -> int:
    key = _clean_text(key)
    if not key:
        key = "UNKNOWN"
    if key in mp:
        return int(mp[key])
    # 追加は “既存最大+1”
    nxt = int(max(mp.values(), default=0) + 1)
    mp[key] = nxt
    return nxt


@dataclass
class Row:
    # identifiers
    run_id: str
    code: str
    trade_date: str

    # X (core + design + context)
    ATR14: Optional[float]
    SLOPE_25: Optional[float]
    RET_20: Optional[float]
    RSI14: Optional[float]
    BB_Z: Optional[float]
    VWAP_GAP_PCT: Optional[float]

    design_rr: Optional[float]
    design_risk: Optional[float]
    design_reward: Optional[float]
    risk_atr: Optional[float]
    reward_atr: Optional[float]

    score_100: Optional[int]

    side_id: int
    style_id: int
    horizon_id: int
    sector_id: int
    universe_id: int
    mode_id: int

    # y
    y_label: str            # win/lose/flat
    y_pl: float
    y_r: Optional[float]
    y_hold_days: Optional[int]
    y_touch_tp_first: str   # tp_first/sl_first/none


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return []
    def gen():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    yield json.loads(s)
                except Exception:
                    continue
    return gen()


class Command(BaseCommand):
    help = "紙シミュJSONL → ML学習用データセット（Parquet/CSV）を生成"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=180, help="直近何日分を見るか（run_date/ts基準）")
        parser.add_argument("--include-live", action="store_true", help="mode=live も含める（通常OFF）")
        parser.add_argument("--out", type=str, default="", help="出力先ディレクトリ（空なら media/aiapp/ml/train）")
        parser.add_argument("--force-csv", action="store_true", help="Parquetを使わずCSV出力に固定")
        parser.add_argument("--min-qty", type=int, default=1, help="学習対象にする最小数量（合算qty）")
        parser.add_argument("--dry-run", action="store_true", help="書き出さず件数だけ表示")

    def handle(self, *args, **opts) -> None:
        days = int(opts.get("days") or 180)
        include_live = bool(opts.get("include_live") or False)
        force_csv = bool(opts.get("force_csv") or False)
        min_qty = int(opts.get("min_qty") or 1)
        dry_run = bool(opts.get("dry_run") or False)

        out_dir_opt = _clean_text(opts.get("out") or "")
        base_out = Path(out_dir_opt) if out_dir_opt else (Path(settings.MEDIA_ROOT) / "aiapp" / "ml" / "train")
        base_out.mkdir(parents=True, exist_ok=True)

        behavior_path = Path(settings.MEDIA_ROOT) / "aiapp" / "behavior" / "latest_behavior.jsonl"
        if not behavior_path.exists():
            self.stdout.write(self.style.ERROR(f"[build_ml_dataset] not found: {behavior_path}"))
            return

        now = datetime.now(JST).replace(tzinfo=None)
        cutoff = now - timedelta(days=days)

        # id maps（永続）
        meta_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "ml" / "meta"
        mp_side = _load_map(meta_dir / "side_map.json")
        mp_style = _load_map(meta_dir / "style_map.json")
        mp_horizon = _load_map(meta_dir / "horizon_map.json")
        mp_sector = _load_map(meta_dir / "sector_map.json")
        mp_univ = _load_map(meta_dir / "universe_map.json")
        mp_mode = _load_map(meta_dir / "mode_map.json")

        rows: List[Row] = []

        scanned = 0
        kept = 0
        skipped_label = 0
        skipped_qty = 0
        skipped_time = 0
        skipped_pl = 0

        for d in _iter_jsonl(behavior_path):
            scanned += 1

            mode = _clean_text(d.get("mode")).lower() or "unknown"
            if (not include_live) and mode == "live":
                continue

            # 日付フィルタ: run_date → ts の順に使う
            dt_run = _to_dt_any(d.get("run_date")) or _to_dt_any(d.get("ts"))
            if dt_run is not None and dt_run < cutoff:
                skipped_time += 1
                continue

            label = _get_label(d)
            if label not in ("win", "lose", "flat"):
                skipped_label += 1
                continue

            qty_total = _sum_qty(d)
            if qty_total < min_qty:
                skipped_qty += 1
                continue

            y_pl = _sum_pl(d)
            if y_pl is None:
                skipped_pl += 1
                continue

            code = _norm_code(d.get("code"))
            run_id = _clean_text(d.get("run_id")) or "unknown_run"
            trade_date = _clean_text(d.get("trade_date") or d.get("run_date") or "")
            if not trade_date:
                # 最後の砦
                if dt_run is not None:
                    trade_date = dt_run.date().isoformat()
                else:
                    trade_date = "1970-01-01"

            fs = _extract_feature_snapshot(d)

            # コア特徴量（当日確定）
            ATR14 = _safe_float(fs.get("ATR14")) or _safe_float(d.get("atr_14"))
            SLOPE_25 = _safe_float(fs.get("SLOPE_25")) or _safe_float(d.get("slope_25"))
            RET_20 = _safe_float(fs.get("RET_20")) or _safe_float(d.get("ret_20"))
            RSI14 = _safe_float(fs.get("RSI14"))
            BB_Z = _safe_float(fs.get("BB_Z"))
            VWAP_GAP_PCT = _safe_float(fs.get("VWAP_GAP_PCT"))

            # design
            design_rr = _safe_float(d.get("design_rr"))
            design_risk = _safe_float(d.get("design_risk"))
            design_reward = _safe_float(d.get("design_reward"))
            risk_atr = _safe_float(d.get("risk_atr"))
            reward_atr = _safe_float(d.get("reward_atr"))

            # context (IDs)
            side = _clean_text(d.get("side")).upper() or "UNKNOWN"
            style = _clean_text(d.get("style")).lower() or "unknown"
            horizon = _clean_text(d.get("horizon")).lower() or "unknown"
            sector = _clean_text(d.get("sector")) or "UNKNOWN"
            universe = _clean_text(d.get("universe")).lower() or "unknown"
            mode_s = mode or "unknown"

            side_id = _get_id(mp_side, side)
            style_id = _get_id(mp_style, style)
            horizon_id = _get_id(mp_horizon, horizon)
            sector_id = _get_id(mp_sector, sector)
            universe_id = _get_id(mp_univ, universe)
            mode_id = _get_id(mp_mode, mode_s)

            score_100 = _safe_int(d.get("score_100"))

            # y (optional)
            y_hold = _hold_days(d)
            y_touch = _touch_first(d)
            y_r = _y_r_from_cashrisk(d, float(y_pl))

            rows.append(
                Row(
                    run_id=run_id,
                    code=code,
                    trade_date=trade_date,

                    ATR14=ATR14,
                    SLOPE_25=SLOPE_25,
                    RET_20=RET_20,
                    RSI14=RSI14,
                    BB_Z=BB_Z,
                    VWAP_GAP_PCT=VWAP_GAP_PCT,

                    design_rr=design_rr,
                    design_risk=design_risk,
                    design_reward=design_reward,
                    risk_atr=risk_atr,
                    reward_atr=reward_atr,

                    score_100=score_100,

                    side_id=side_id,
                    style_id=style_id,
                    horizon_id=horizon_id,
                    sector_id=sector_id,
                    universe_id=universe_id,
                    mode_id=mode_id,

                    y_label=label,
                    y_pl=float(y_pl),
                    y_r=y_r,
                    y_hold_days=y_hold,
                    y_touch_tp_first=y_touch,
                )
            )
            kept += 1

        # map保存（カテゴリが増えてもIDが安定する）
        _save_map(meta_dir / "side_map.json", mp_side)
        _save_map(meta_dir / "style_map.json", mp_style)
        _save_map(meta_dir / "horizon_map.json", mp_horizon)
        _save_map(meta_dir / "sector_map.json", mp_sector)
        _save_map(meta_dir / "universe_map.json", mp_univ)
        _save_map(meta_dir / "mode_map.json", mp_mode)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("===== build_ml_dataset summary ====="))
        self.stdout.write(f"  source: {behavior_path}")
        self.stdout.write(f"  days={days} include_live={include_live} min_qty={min_qty} dry_run={dry_run}")
        self.stdout.write(f"  scanned={scanned} kept={kept}")
        self.stdout.write(f"  skipped: time={skipped_time} label={skipped_label} qty={skipped_qty} pl_missing={skipped_pl}")

        if kept <= 0:
            self.stdout.write(self.style.WARNING("[build_ml_dataset] no rows kept. (win/lose/flat & pl が足りない可能性)"))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING("[build_ml_dataset] dry-run: write skipped."))
            return

        df = pd.DataFrame([r.__dict__ for r in rows])

        # 月単位に分割
        # trade_date から YYYY_MM を作る（壊れてたら run_date相当で fallback）
        def _to_yyyymm(s: str) -> str:
            try:
                dt = datetime.fromisoformat(str(s)[:10])
                return dt.strftime("%Y_%m")
            except Exception:
                return now.strftime("%Y_%m")

        df["yyyymm"] = df["trade_date"].apply(_to_yyyymm)

        # parquet可否
        can_parquet = False
        if not force_csv:
            try:
                import pyarrow  # noqa: F401
                can_parquet = True
            except Exception:
                can_parquet = False

        written_files: List[Path] = []

        for yyyymm, g in df.groupby("yyyymm"):
            out_month = base_out / yyyymm
            out_month.mkdir(parents=True, exist_ok=True)

            g2 = g.drop(columns=["yyyymm"], errors="ignore").reset_index(drop=True)

            if can_parquet:
                outp = out_month / "train.parquet"
                g2.to_parquet(outp, index=False)
                written_files.append(outp)
            else:
                outc = out_month / "train.csv"
                g2.to_csv(outc, index=False, encoding="utf-8")
                written_files.append(outc)

        # latest（全件）
        latest = base_out / ("latest_train.parquet" if can_parquet else "latest_train.csv")
        df2 = df.drop(columns=["yyyymm"], errors="ignore").reset_index(drop=True)
        if can_parquet:
            df2.to_parquet(latest, index=False)
        else:
            df2.to_csv(latest, index=False, encoding="utf-8")
        written_files.append(latest)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"[build_ml_dataset] written: {len(written_files)} files"))
        self.stdout.write(f"  out_dir: {base_out}")
        self.stdout.write(f"  latest: {latest}")