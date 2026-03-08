"""
[FILE] shihyo_load_daily_prices.py
[PATH] <project_root>/shihyo/management/commands/shihyo_load_daily_prices.py

このファイルは何？
- aiapp.StockMaster を参照して、日本株の日次価格を取得し、
  shihyo.ShihyoDailyPrice に保存するコマンドです。
- まずは市場の偏り集計の土台データを作るためのコマンドです。
- 価格取得には yfinance を使います。
- 1日1銘柄1行で upsert（date + code）します。

このコマンドで保存するもの:
- code / name
- sector_code / sector_name
- close / prev_close / change / change_pct
- volume
- source / raw_payload

使い方の例:
- 今日時点の直近営業日の価格を取得
  python manage.py shihyo_load_daily_prices

- 対象日を指定
  python manage.py shihyo_load_daily_prices --date 2026-03-09

- お試しで50銘柄だけ
  python manage.py shihyo_load_daily_prices --limit 50

- 特定コードだけ
  python manage.py shihyo_load_daily_prices --codes 7203,6501,8306
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from aiapp.models import StockMaster
from shihyo.models import ShihyoDailyPrice

try:
    import pandas as pd
    import yfinance as yf
except Exception:
    pd = None
    yf = None


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _safe_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        if pd is not None and pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


def _safe_int(v) -> Optional[int]:
    try:
        if v is None:
            return None
        if pd is not None and pd.isna(v):
            return None
        return int(v)
    except Exception:
        return None


def _to_symbol(code: str) -> Optional[str]:
    code = (code or "").strip()
    if not code.isdigit():
        return None
    if len(code) not in (4, 5):
        return None
    return f"{code}.T"


def _parse_target_date(s: str) -> date:
    if not s:
        return date.today()
    return datetime.strptime(s, "%Y-%m-%d").date()


def _pick_target_and_prev_close(df: "pd.DataFrame", target_date: date) -> tuple[Optional[float], Optional[float], Optional[int], Optional[str]]:
    """
    DataFrame の index から target_date 以下の最終営業日を選び、
    その日の close / 1営業日前の close / volume / 実際に使った日付文字列 を返す。
    """
    if df is None or df.empty:
        return None, None, None, None

    try:
        work = df.copy()
        work = work.sort_index()
    except Exception:
        return None, None, None, None

    valid_dates = [idx for idx in work.index if idx.date() <= target_date]
    if not valid_dates:
        return None, None, None, None

    target_idx = valid_dates[-1]
    pos = work.index.get_loc(target_idx)

    close_value = _safe_float(work.iloc[pos].get("Close"))
    volume_value = _safe_int(work.iloc[pos].get("Volume"))

    prev_close_value: Optional[float] = None
    if isinstance(pos, int) and pos > 0:
        prev_close_value = _safe_float(work.iloc[pos - 1].get("Close"))

    actual_date = target_idx.date().isoformat()
    return close_value, prev_close_value, volume_value, actual_date


class Command(BaseCommand):
    help = "aiapp.StockMaster を参照して日次価格を取得し、shihyo.ShihyoDailyPrice に保存します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--date",
            type=str,
            default="",
            help="対象日 (YYYY-MM-DD)。未指定なら今日基準で直近営業日を採用",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="テスト用。先頭から N 銘柄だけ取得",
        )
        parser.add_argument(
            "--codes",
            type=str,
            default="",
            help="カンマ区切りで特定コードだけ取得 (例: 7203,6501,8306)",
        )
        parser.add_argument(
            "--chunk",
            type=int,
            default=100,
            help="yfinance へ投げる同時取得件数",
        )
        parser.add_argument(
            "--lookback-days",
            type=int,
            default=10,
            help="対象日から何日前まで遡って取得するか。前営業日参照用",
        )

    def handle(self, *args, **options):
        if yf is None or pd is None:
            self.stdout.write(
                self.style.ERROR(
                    "yfinance / pandas が見つかりません。まず `pip install yfinance pandas` を実行してください。"
                )
            )
            return

        target_date = _parse_target_date(options.get("date") or "")
        chunk_size = max(1, int(options.get("chunk") or 100))
        lookback_days = max(3, int(options.get("lookback_days") or 10))
        limit = max(0, int(options.get("limit") or 0))

        codes_arg = (options.get("codes") or "").strip()
        requested_codes = [c.strip() for c in codes_arg.split(",") if c.strip()] if codes_arg else []

        masters_qs = StockMaster.objects.all().order_by("code")
        if requested_codes:
            masters_qs = masters_qs.filter(code__in=requested_codes)
        if limit > 0:
            masters_qs = masters_qs[:limit]

        masters = list(masters_qs)
        if not masters:
            self.stdout.write(self.style.WARNING("対象の StockMaster が見つかりませんでした。"))
            return

        symbol_map: dict[str, StockMaster] = {}
        skipped = 0

        for master in masters:
            symbol = _to_symbol(master.code)
            if not symbol:
                skipped += 1
                continue
            symbol_map[symbol] = master

        symbols = list(symbol_map.keys())
        if not symbols:
            self.stdout.write(self.style.WARNING("取得対象の日本株コードがありませんでした。"))
            return

        start_date = target_date - timedelta(days=lookback_days)
        end_date = target_date + timedelta(days=1)

        created_count = 0
        updated_count = 0
        no_price_count = 0
        failed_chunks = 0

        self.stdout.write(
            f"[shihyo_load_daily_prices] target_date={target_date} symbols={len(symbols)} "
            f"range={start_date}..{end_date}"
        )

        for part in _chunked(symbols, chunk_size):
            try:
                data = yf.download(
                    tickers=part,
                    start=start_date.isoformat(),
                    end=end_date.isoformat(),
                    progress=False,
                    group_by="ticker",
                    auto_adjust=False,
                    threads=True,
                )
            except Exception as e:
                failed_chunks += 1
                self.stdout.write(self.style.WARNING(f"chunk取得失敗: size={len(part)} error={e}"))
                continue

            upserts: list[tuple[StockMaster, dict]] = []

            for symbol in part:
                master = symbol_map[symbol]

                try:
                    if isinstance(data.columns, pd.MultiIndex):
                        if symbol not in data.columns.get_level_values(0):
                            no_price_count += 1
                            continue
                        df = data[symbol].copy()
                    else:
                        # 1銘柄だけのとき
                        df = data.copy()

                    close_value, prev_close_value, volume_value, actual_date = _pick_target_and_prev_close(df, target_date)

                    if close_value is None or actual_date is None:
                        no_price_count += 1
                        continue

                    change_value = None
                    change_pct_value = None

                    if prev_close_value is not None and prev_close_value != 0:
                        change_value = close_value - prev_close_value
                        change_pct_value = (change_value / prev_close_value) * 100.0

                    upserts.append(
                        (
                            master,
                            {
                                "date": actual_date,
                                "close": close_value,
                                "prev_close": prev_close_value,
                                "change": change_value,
                                "change_pct": change_pct_value,
                                "volume": volume_value,
                                "turnover": None,
                                "source": "yfinance",
                                "raw_payload": {
                                    "symbol": symbol,
                                    "target_date": target_date.isoformat(),
                                    "actual_date": actual_date,
                                    "download_start": start_date.isoformat(),
                                    "download_end": end_date.isoformat(),
                                },
                            },
                        )
                    )
                except Exception:
                    no_price_count += 1
                    continue

            with transaction.atomic():
                for master, row in upserts:
                    obj, created = ShihyoDailyPrice.objects.update_or_create(
                        date=row["date"],
                        code=master.code,
                        defaults={
                            "name": master.name or "",
                            "sector_code": master.sector_code or None,
                            "sector_name": master.sector_name or None,
                            "close": row["close"],
                            "prev_close": row["prev_close"],
                            "change": row["change"],
                            "change_pct": row["change_pct"],
                            "volume": row["volume"],
                            "turnover": row["turnover"],
                            "source": row["source"],
                            "raw_payload": row["raw_payload"],
                        },
                    )
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                "[shihyo_load_daily_prices] done "
                f"created={created_count} updated={updated_count} "
                f"no_price={no_price_count} skipped={skipped} failed_chunks={failed_chunks}"
            )
        )