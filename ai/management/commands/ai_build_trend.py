from __future__ import annotations
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from ai.models import TrendResult


# ====== データ構造 ======
@dataclass
class Row:
    code: str
    date: str
    close: float
    volume: float
    name: str
    sector: str


# ====== ユーティリティ ======
def _read_snapshot_csv(root: Path) -> Dict[str, List[Row]]:
    """スナップショット ohlcv.csv を code ごとにまとめて返す"""
    fp = root / "ohlcv.csv"
    if not fp.exists():
        raise FileNotFoundError(f"CSVが見つかりません: {fp}")

    per_code: Dict[str, List[Row]] = {}
    with fp.open("r", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        needed = {"code", "date", "close", "volume", "name", "sector"}
        if not needed.issubset(set(rdr.fieldnames or [])):
            raise ValueError(f"CSVのヘッダが不足しています: 必要={needed}, 実際={rdr.fieldnames}")

        for rec in rdr:
            raw_code = str(rec["code"]).strip()
            code = raw_code.split(".", 1)[0] if "." in raw_code else raw_code
            if not code:
                continue

            try:
                close = float(rec["close"])
            except Exception:
                # 価格が壊れてる行はスキップ
                continue
            try:
                vol = float(rec["volume"])
            except Exception:
                vol = 0.0

            row = Row(
                code=code,
                date=str(rec["date"]).strip(),
                close=close,
                volume=vol,
                name=str(rec.get("name") or "").strip(),
                sector=str(rec.get("sector") or "").strip(),
            )
            per_code.setdefault(code, []).append(row)

    # 日付順
    for k in per_code:
        per_code[k].sort(key=lambda r: r.date)
    return per_code


def _read_master_universe(base: Path) -> Dict[str, dict]:
    """data/universe/master.csv を辞書で返す（フォールバック用・制限ではない）"""
    master = {}
    fp = base / "data" / "universe" / "master.csv"
    if not fp.exists():
        return master
    with fp.open("r", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        for rec in rdr:
            code = (rec.get("code") or "").strip()
            if not code:
                continue
            master[code] = {
                "name": (rec.get("name") or "").strip(),
                "sector": (rec.get("sector") or "").strip(),
            }
    return master


def _sma(values: List[float], window: int) -> Optional[float]:
    if len(values) < window or window <= 0:
        return None
    return sum(values[-window:]) / window


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _trend_from_pair(a: Optional[float], b: Optional[float], tol: float = 0.002) -> int:
    if a is None or b is None or b == 0:
        return 0
    if a > b * (1 + tol):
        return 1
    if a < b * (1 - tol):
        return -1
    return 0


def _linear_slope(y: List[float]) -> Optional[float]:
    n = len(y)
    if n < 5:
        return None
    sx = (n - 1) * n / 2
    sx2 = (n - 1) * n * (2 * n - 1) / 6
    sy = sum(y)
    sxy = sum(i * v for i, v in enumerate(y))
    den = n * sx2 - sx * sx
    if den == 0:
        return None
    return (n * sxy - sx * sy) / den


def _weekly_monthly_trend(closes: List[float]) -> Tuple[int, int, Optional[float], Optional[float], Optional[float]]:
    ma5 = _sma(closes, 5)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)
    w = _trend_from_pair(ma5, ma20)
    m = _trend_from_pair(ma20, ma60)
    return w, m, ma5, ma20, ma60


def _rs_index(last_price: float, ma20: Optional[float]) -> float:
    if (ma20 or 0) <= 0 or last_price <= 0:
        return 1.0
    return float(last_price / ma20)


def _vol_spike(last_vol: float, vols: List[float]) -> float:
    base = _mean(vols[-20:]) or 0.0
    if base <= 0:
        return 1.0
    return float(last_vol / base)


def _confidence(days: int, w_trend_num: int, m_trend_num: int) -> float:
    base = min(1.0, max(0.0, days / 60.0))
    bonus = 0.2 if (w_trend_num == m_trend_num and w_trend_num != 0) else 0.0
    return float(max(0.0, min(1.0, base + bonus)))


def _D(x: Optional[float]) -> Decimal:
    if x is None:
        return Decimal("0")
    try:
        return Decimal(str(x))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


# ====== メイン ======
class Command(BaseCommand):
    help = "スナップショットCSVから TrendResult を“全銘柄”更新（name/sector は空なら master.csv/既存DBで補完）"

    def add_arguments(self, parser):
        parser.add_argument("--root", type=str, required=True, help="例: media/ohlcv/snapshots/2025-11-06")
        parser.add_argument("--asof", type=str, required=False, help="YYYY-MM-DD（未指定は今日）")
        parser.add_argument(
            "--only-master", action="store_true",
            help="True の場合、master.csv に載っている銘柄だけを更新（通常は使わない）"
        )
        parser.add_argument("--dry-run", action="store_true", help="保存せず計算・ログだけ出す")

    def handle(self, *args, **options):
        base = Path(__file__).resolve().parents[4]  # .../my_stock_portfolio/
        root = Path(options["root"]).resolve()
        asof = options.get("asof") or timezone.localdate().strftime("%Y-%m-%d")
        only_master = bool(options.get("only-master"))
        dry = bool(options.get("dry-run"))

        per_code = _read_snapshot_csv(root)
        master = _read_master_universe(base)

        codes = list(per_code.keys())
        if only_master:
            codes = [c for c in codes if c in master]

        total = len(codes)
        created = 0
        updated = 0
        skipped = 0
        errors = 0

        self.stdout.write(f"[ai_build_trend] as_of={asof} root={root}")
        self.stdout.write(f"[ai_build_trend] codes in snapshot={len(per_code)} / processing={total}")
        if only_master:
            self.stdout.write("[ai_build_trend] only_master=True（master.csvに載っている銘柄のみ処理）")

        # 1銘柄ごとに try/except（途中例外で全ロールバックを避ける）
        for i, code in enumerate(codes, 1):
            rows = per_code.get(code) or []
            if not rows:
                skipped += 1
                continue

            closes = [r.close for r in rows if r.close is not None]
            vols = [r.volume for r in rows if r.volume is not None]
            if not closes:
                skipped += 1
                continue

            last = rows[-1]
            last_price_f = float(closes[-1])
            last_vol_f = float(vols[-1]) if vols else 0.0

            slope = _linear_slope(closes[-30:])
            w_num, m_num, ma5, ma20, ma60 = _weekly_monthly_trend(closes)
            rs = _rs_index(last_price_f, ma20)
            volb = _vol_spike(last_vol_f, vols)
            conf = _confidence(len(closes), w_num, m_num)

            # master.csv→スナップショット→既存DB の順で補完
            fallback = master.get(code, {})
            try:
                obj = TrendResult.objects.filter(code=code).first()
                name = (last.name or fallback.get("name") or (obj.name if obj else "") or "").strip()
                sector = (last.sector or fallback.get("sector") or (obj.sector_jp if obj else "") or "").strip()

                if obj is None:
                    obj = TrendResult(code=code)
                    is_new = True
                else:
                    is_new = False

                obj.name = name
                obj.sector_jp = sector
                obj.last_price = _D(last_price_f)          # ← NOT NULL対策
                obj.last_volume = _D(last_vol_f)
                obj.daily_slope = _D(slope if slope is not None else 0.0)
                obj.weekly_trend = _D(w_num)               # +1/0/-1 を Decimal で保存
                obj.monthly_trend = _D(m_num)
                obj.ma5 = _D(ma5 if ma5 is not None else 0.0)
                obj.ma20 = _D(ma20 if ma20 is not None else 0.0)
                obj.ma60 = _D(ma60 if ma60 is not None else 0.0)
                obj.rs_index = _D(rs)
                obj.vol_spike = _D(volb)
                obj.confidence = _D(conf)
                obj.as_of = asof

                if not dry:
                    obj.save()

                if is_new:
                    created += 1
                else:
                    updated += 1

            except Exception as e:
                errors += 1
                self.stderr.write(f"[{i}/{total}] {code}: ERROR {e}")

            if i % 50 == 0 or i == total:
                self.stdout.write(f" ..progress {i}/{total} (new:{created} upd:{updated} skip:{skipped} err:{errors})")

        self.stdout.write(self.style.SUCCESS(
            f"Updated TrendResult: total={total} created={created} updated={updated} skipped={skipped} errors={errors} (as_of={asof})"
        ))