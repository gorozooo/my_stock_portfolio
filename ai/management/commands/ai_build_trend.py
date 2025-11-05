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


# ===== ユーティリティ =====

@dataclass
class Row:
    code: str
    date: str
    close: float
    volume: float
    name: str
    sector: str


def _read_snapshot_csv(root: Path) -> Dict[str, List[Row]]:
    """
    スナップショットCSV（例: media/ohlcv/snapshots/2025-11-05/ohlcv.csv）を読み込み、
    code ごとに時系列を返す。
      必須ヘッダ: code,date,close,volume,name,sector
    """
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
            # 7203.0 → 7203 のような表記揺れ吸収
            code = raw_code.split(".", 1)[0] if "." in raw_code else raw_code

            # close は必須。欠損行はスキップ。
            try:
                close = float(rec["close"])
            except Exception:
                continue

            # volume は欠損したら 0
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

    # 昇順（日付が古い→新しい）に整列
    for k in per_code:
        per_code[k].sort(key=lambda r: r.date)
    return per_code


def _sma(values: List[float], window: int) -> Optional[float]:
    if len(values) < window or window <= 0:
        return None
    return sum(values[-window:]) / window


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _trend_from_pair(a: Optional[float], b: Optional[float], tol: float = 0.002) -> str:
    """
    2つの平均を比較して up/flat/down を返す。
    tol はフラットとみなす許容差（0.2%既定）
    """
    if a is None or b is None or b == 0:
        return "flat"
    if a > b * (1 + tol):
        return "up"
    if a < b * (1 - tol):
        return "down"
    return "flat"


def _linear_slope(y: List[float]) -> Optional[float]:
    """
    単純な最小二乗直線の傾き（x=0,1,2,..）を返す。
    標本数が少なければ None
    """
    n = len(y)
    if n < 5:
        return None
    # x: 0..n-1
    sx = (n - 1) * n / 2
    sx2 = (n - 1) * n * (2 * n - 1) / 6
    sy = sum(y)
    sxy = sum(i * v for i, v in enumerate(y))
    den = n * sx2 - sx * sx
    if den == 0:
        return None
    return (n * sxy - sx * sy) / den


def _weekly_monthly_trend(closes: List[float]) -> Tuple[str, str, Optional[float], Optional[float], Optional[float]]:
    """
    週足/月足相当の向きをシンプルに判定。
      - 週足:  ma5 と ma20 の比較
      - 月足:  ma20 と ma60 の比較
    返り値: (weekly_trend, monthly_trend, ma5, ma20, ma60)
    """
    ma5 = _sma(closes, 5)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)

    w = _trend_from_pair(ma5, ma20)
    m = _trend_from_pair(ma20, ma60)

    return w, m, ma5, ma20, ma60


def _rs_index(last_price: float, ma20: Optional[float]) -> float:
    """
    簡易RS。指数データがまだ無い前提で、直近終値 / 20日MA を代用。
    """
    if (ma20 or 0) <= 0 or last_price <= 0:
        return 1.0
    return float(last_price / ma20)


def _vol_spike(last_vol: float, vols: List[float]) -> float:
    base = _mean(vols[-20:]) or 0.0
    if base <= 0:
        return 1.0
    return float(last_vol / base)


def _confidence(days: int, w_trend: str, m_trend: str) -> float:
    """
    データ量と方向一貫性から 0.0〜1.0 の簡易confidence。
    """
    base = min(1.0, max(0.0, days / 60.0))  # 60日満額
    bonus = 0.0
    if w_trend == m_trend and w_trend in ("up", "down"):
        bonus = 0.2
    return float(max(0.0, min(1.0, base + bonus)))


def _D(x: Optional[float]) -> Decimal:
    """
    Decimal 変換（None→0）。floatの直入れは誤差が出るので文字列経由。
    """
    if x is None:
        return Decimal("0")
    try:
        return Decimal(str(x))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


# ===== メイン・コマンド =====

class Command(BaseCommand):
    help = "スナップショットCSVから TrendResult を更新（daily_slope / weekly_trend / monthly_trend ほか）"

    def add_arguments(self, parser):
        parser.add_argument("--root", type=str, required=True, help="スナップショットCSVのディレクトリ（例: media/ohlcv/snapshots/2025-11-05）")
        parser.add_argument("--asof", type=str, required=False, help="as_of（日付）。未指定は今日。YYYY-MM-DD")

    @transaction.atomic
    def handle(self, *args, **options):
        root = Path(options["root"]).resolve()
        asof = options.get("asof") or timezone.localdate().strftime("%Y-%m-%d")

        per_code = _read_snapshot_csv(root)
        updated = 0
        invalid = 0

        for code, rows in per_code.items():
            if not rows:
                continue

            closes = [r.close for r in rows if r.close is not None]
            vols = [r.volume for r in rows if r.volume is not None]
            if not closes:
                invalid += 1
                continue

            last = rows[-1]
            last_price_f = float(closes[-1])
            last_vol_f = float(vols[-1]) if vols else 0.0

            # 指標計算
            slope = _linear_slope(closes[-30:])  # 直近30本の回帰直線傾き（daily_slope）
            w_trend, m_trend, ma5, ma20, ma60 = _weekly_monthly_trend(closes)
            rs = _rs_index(last_price_f, ma20)
            volb = _vol_spike(last_vol_f, vols)
            conf = _confidence(len(closes), w_trend, m_trend)

            # ===== ここがポイント：update_or_createで必須カラムを含む全項目を一括指定 =====
            TrendResult.objects.update_or_create(
                code=code,
                defaults={
                    "name": last.name or "",
                    "sector_jp": last.sector or "",
                    "last_price": _D(last_price_f),
                    "last_volume": _D(last_vol_f),
                    "daily_slope": _D(slope if slope is not None else 0.0),
                    "weekly_trend": w_trend,
                    "monthly_trend": m_trend,
                    "ma5": _D(ma5 if ma5 is not None else 0.0),
                    "ma20": _D(ma20 if ma20 is not None else 0.0),
                    "ma60": _D(ma60 if ma60 is not None else 0.0),
                    "rs_index": _D(rs),
                    "vol_spike": _D(volb),
                    "confidence": Decimal(str(conf)),
                    "as_of": asof,
                }
            )
            updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Updated TrendResult: {updated} items (as_of={asof})"
        ))
        if invalid:
            self.stdout.write(self.style.WARNING(
                f"invalid_rows: {invalid}"
            ))