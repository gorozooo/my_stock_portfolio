from __future__ import annotations
import csv
import unicodedata
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


def _clean_text(s: str) -> str:
    """
    表示が乱れる不可視文字（ゼロ幅系など）を除去し、前後の空白をトリム。
    """
    if not s:
        return ""
    # 正規化（互換分解→合成）
    s = unicodedata.normalize("NFKC", s)
    # ゼロ幅系や制御文字を除去
    bad = {
        "\u200b",  # ZERO WIDTH SPACE
        "\u200c",  # ZERO WIDTH NON-JOINER
        "\u200d",  # ZERO WIDTH JOINER
        "\uFEFF",  # ZERO WIDTH NO-BREAK SPACE
        "\u2060",  # WORD JOINER
    }
    s = "".join(ch for ch in s if (ch not in bad and ch.isprintable()))
    return s.strip()


def _read_snapshot_csv(root: Path) -> Dict[str, List[Row]]:
    """
    スナップショットCSVを読み込み、codeごとの時系列に分配して返す。
    - 無効コード（数値でない、空、NaNなど）は読み飛ばす
    - close が欠損の行は読み飛ばす
    必須ヘッダ: code,date,close,volume,name,sector
    """
    fp = root / "ohlcv.csv"
    if not fp.exists():
        raise FileNotFoundError(f"CSVが見つかりません: {fp}")

    per_code: Dict[str, List[Row]] = {}
    skipped_invalid_code = 0
    skipped_no_close = 0

    with fp.open("r", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        needed = {"code", "date", "close", "volume", "name", "sector"}
        if not needed.issubset(set(rdr.fieldnames or [])):
            raise ValueError(f"CSVのヘッダが不足しています: 必要={needed}, 実際={rdr.fieldnames}")

        for rec in rdr:
            raw_code = str(rec["code"]).strip()
            code = raw_code.split(".", 1)[0] if "." in raw_code else raw_code

            # 無効コードはスキップ（空/非数字/NaNなど）
            if not code or (not code.isdigit()):
                skipped_invalid_code += 1
                continue

            # close は必須
            try:
                close = float(rec["close"])
            except Exception:
                skipped_no_close += 1
                continue

            # volume 欠損は 0 とみなす
            try:
                vol = float(rec["volume"])
            except Exception:
                vol = 0.0

            row = Row(
                code=code,
                date=str(rec["date"]).strip(),
                close=close,
                volume=vol,
                name=_clean_text(rec.get("name") or ""),
                sector=_clean_text(rec.get("sector") or ""),
            )
            per_code.setdefault(code, []).append(row)

    # 昇順（日付が古い→新しい）に整列
    for k in per_code:
        per_code[k].sort(key=lambda r: r.date)

    # 軽い統計を添えてデバッグしやすく
    total_rows = sum(len(v) for v in per_code.values())
    print(f"[reader] codes={len(per_code)} rows={total_rows} "
          f"(skip_invalid_code={skipped_invalid_code}, skip_no_close={skipped_no_close})")
    return per_code


def _sma(values: List[float], window: int) -> Optional[float]:
    if len(values) < window or window <= 0:
        return None
    return sum(values[-window:]) / window


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _trend_from_pair(a: Optional[float], b: Optional[float], tol: float = 0.002) -> int:
    """
    2つの平均を比較して +1/0/-1 を返す。tol はフラット許容差（0.2%）
    """
    if a is None or b is None or b == 0:
        return 0
    if a > b * (1 + tol):
        return 1
    if a < b * (1 - tol):
        return -1
    return 0


def _linear_slope(y: List[float]) -> Optional[float]:
    """
    最小二乗直線の傾き（x=0..n-1）。標本数<5 は None。
    """
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
    """
    週足: ma5 vs ma20、月足: ma20 vs ma60 → +1/0/-1
    """
    ma5 = _sma(closes, 5)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)
    w = _trend_from_pair(ma5, ma20)
    m = _trend_from_pair(ma20, ma60)
    return w, m, ma5, ma20, ma60


def _rs_index(last_price: float, ma20: Optional[float]) -> float:
    """
    代替RS: 直近終値 / 20日MA
    """
    if (ma20 or 0) <= 0 or last_price <= 0:
        return 1.0
    return float(last_price / ma20)


def _vol_spike(last_vol: float, vols: List[float]) -> float:
    base = _mean(vols[-20:]) or 0.0
    if base <= 0:
        return 1.0
    return float(last_vol / base)


def _confidence(days: int, w_trend_num: int, m_trend_num: int) -> float:
    """
    データ量（~60日）+ 方向一貫性ボーナスから 0.0..1.0
    """
    base = min(1.0, max(0.0, days / 60.0))
    bonus = 0.2 if (w_trend_num == m_trend_num and w_trend_num != 0) else 0.0
    return float(max(0.0, min(1.0, base + bonus)))


def _D(x: Optional[float]) -> Decimal:
    """
    Decimal 変換（None→0）。floatの誤差は文字列経由で抑制。
    """
    if x is None:
        return Decimal("0")
    try:
        return Decimal(str(x))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


# ===== メイン =====

class Command(BaseCommand):
    help = "スナップショットCSVから TrendResult を更新（name/sector は空なら既存DBを保持。無効コードは読み飛ばす）"

    def add_arguments(self, parser):
        parser.add_argument("--root", type=str, required=True, help="例: media/ohlcv/snapshots/2025-11-05")
        parser.add_argument("--asof", type=str, required=False, help="YYYY-MM-DD（未指定は今日）")

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

            slope = _linear_slope(closes[-30:])
            w_num, m_num, ma5, ma20, ma60 = _weekly_monthly_trend(closes)
            rs = _rs_index(last_price_f, ma20)
            volb = _vol_spike(last_vol_f, vols)
            conf = _confidence(len(closes), w_num, m_num)

            # 既存を取得 → CSVが空なら既存値を保持
            try:
                obj = TrendResult.objects.get(code=code)
                name = last.name or (obj.name or "")
                sector = last.sector or (obj.sector_jp or "")
            except TrendResult.DoesNotExist:
                obj = TrendResult(code=code)
                name = last.name or ""
                sector = last.sector or ""

            obj.name = _clean_text(name)
            obj.sector_jp = _clean_text(sector)
            obj.last_price = _D(last_price_f)
            obj.last_volume = _D(last_vol_f)
            obj.daily_slope = _D(slope if slope is not None else 0.0)
            obj.weekly_trend = _D(w_num)     # +1/0/-1（数値で保存）
            obj.monthly_trend = _D(m_num)    # +1/0/-1（数値で保存）
            obj.ma5 = _D(ma5 if ma5 is not None else 0.0)
            obj.ma20 = _D(ma20 if ma20 is not None else 0.0)
            obj.ma60 = _D(ma60 if ma60 is not None else 0.0)
            obj.rs_index = _D(rs)
            obj.vol_spike = _D(volb)
            obj.confidence = _D(conf)
            obj.as_of = asof
            obj.save()
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"Updated TrendResult: {updated} items (as_of={asof})"))
        if invalid:
            self.stdout.write(self.style.WARNING(f"invalid_rows: {invalid}"))