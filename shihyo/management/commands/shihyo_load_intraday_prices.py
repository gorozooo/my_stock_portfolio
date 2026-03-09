"""
[FILE] shihyo_load_intraday_prices.py
[PATH] <project_root>/shihyo/management/commands/shihyo_load_intraday_prices.py

このファイルは何？
- aiapp.StockMaster を参照して、10:00時点の場中価格を取得し、
  shihyo.ShihyoIntradayPrice に保存するコマンドです。
- yfinance の 1分足を使い、対象日の 10:00 までの実績を保存します。
- 10:00確認(open_1000) の実績集計の土台データになります。

今回の改善ポイント:
- ETF/ETN はデフォルトで除外
- --include-etf を付けたときだけ ETF/ETN も対象にする
- pandas の FutureWarning を解消
- prev_close が無いときは、同じ1分足データの前営業日終値から補完する
- sector_name を保存時に正規化する
"""

from __future__ import annotations

from datetime import datetime, date, time
from zoneinfo import ZoneInfo
from typing import Optional

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from aiapp.models import StockMaster
from shihyo.models import ShihyoDailyPrice, ShihyoIntradayPrice

try:
    import pandas as pd
    import yfinance as yf
except Exception:
    pd = None
    yf = None


JST = ZoneInfo("Asia/Tokyo")


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


def _normalize_sector_name(sector_name: str | None) -> str:
    s = (sector_name or "").strip()
    if not s:
        return ""

    alias_map = {
        "食品": "食料品",
        "食料": "食料品",
        "建設・資材": "建設業",
        "建設": "建設業",
        "素材・化学": "化学",
        "医薬": "医薬品",
        "情報通信": "情報・通信業",
        "情報・通信": "情報・通信業",
        "電力・ガス": "電気・ガス業",
        "電気ガス": "電気・ガス業",
        "電気・ガス": "電気・ガス業",
        "自動車・輸送機": "輸送用機器",
        "商社・卸売": "卸売業",
        "銀行・金融": "銀行業",
        "電機・精密": "電気機器",
        "小売": "小売業",
        "卸売": "卸売業",
        "不動産": "不動産業",
        "海運": "海運業",
        "空運": "空運業",
        "陸運": "陸運業",
        "倉庫・運輸": "倉庫・運輸関連業",
        "証券・商品先物取引業": "証券、商品先物取引業",
        "石油・石炭": "石油・石炭製品",
        "ガラス・土石": "ガラス・土石製品",
        "その他金融": "その他金融業",
    }

    if s in alias_map:
        return alias_map[s]

    for key, value in alias_map.items():
        if s == key or s in key or key in s:
            return value

    return s


def _get_prev_close_map(target_date: date) -> dict[str, float]:
    """
    対象日より前の直近日次価格を code -> close で作る。
    """
    latest_prev_date = (
        ShihyoDailyPrice.objects
        .filter(date__lt=target_date)
        .order_by("-date")
        .values_list("date", flat=True)
        .first()
    )
    if not latest_prev_date:
        return {}

    rows = ShihyoDailyPrice.objects.filter(date=latest_prev_date).only("code", "close")
    result: dict[str, float] = {}
    for row in rows:
        if row.close is not None:
            result[row.code] = float(row.close)
    return result


def _normalize_index_to_jst(df: "pd.DataFrame") -> "pd.DataFrame":
    """
    yfinance の index を JST に寄せる。
    """
    work = df.copy()
    if work.index.tz is None:
        work.index = work.index.tz_localize(JST)
    else:
        work.index = work.index.tz_convert(JST)
    return work


def _is_etf_like(master: StockMaster) -> bool:
    sector_name = (master.sector_name or "").strip()
    name = (master.name or "").upper()

    if sector_name == "ETF/ETN":
        return True

    keywords = [
        "ETF",
        "ETN",
        "REIT",
        "上場投信",
        "投資法人",
        "NEXT FUNDS",
        "MAXIS",
        "IFREEETF",
    ]
    return any(k in name for k in keywords)


def _infer_prev_close_from_intraday_df(df: "pd.DataFrame", target_date: date) -> Optional[float]:
    """
    1分足データの中から、対象日の前営業日の最終 close を拾って prev_close の補完に使う。
    """
    if df is None or df.empty:
        return None

    try:
        work = _normalize_index_to_jst(df)
        work = work.sort_index()
    except Exception:
        return None

    prior_rows = work[work.index.date < target_date]
    if prior_rows.empty:
        return None

    try:
        last_close = prior_rows.iloc[-1].get("Close")
        return _safe_float(last_close)
    except Exception:
        return None


def _pick_intraday_snapshot(
    df: "pd.DataFrame",
    target_date: date,
    cutoff_time: time,
) -> tuple[Optional[float], Optional[int], Optional[float], Optional[str], dict]:
    """
    1分足 DataFrame から、対象日の cutoff_time までの実績を取り出す。
    戻り値:
      (
        last_price,
        cumulative_volume,
        cumulative_turnover,
        actual_timestamp_iso,
        debug_dict,
      )
    """
    if df is None or df.empty:
        return None, None, None, None, {"reason": "empty_df"}

    try:
        work = _normalize_index_to_jst(df)
        work = work.sort_index()
    except Exception:
        return None, None, None, None, {"reason": "index_normalize_failed"}

    same_day = work[work.index.date == target_date]
    if same_day.empty:
        return None, None, None, None, {"reason": "no_rows_for_target_date"}

    cutoff_dt = datetime.combine(target_date, cutoff_time, tzinfo=JST)
    same_day = same_day[same_day.index <= cutoff_dt]
    if same_day.empty:
        return None, None, None, None, {"reason": "no_rows_before_cutoff"}

    last_row = same_day.iloc[-1]

    last_price = _safe_float(last_row.get("Close"))
    if last_price is None:
        return None, None, None, None, {"reason": "last_price_none"}

    volume_series = same_day.get("Volume")
    close_series = same_day.get("Close")

    cumulative_volume: Optional[int] = None
    cumulative_turnover: Optional[float] = None

    if volume_series is not None:
        try:
            vol = volume_series.fillna(0)
            cumulative_volume = int(vol.sum())
        except Exception:
            cumulative_volume = None

    if volume_series is not None and close_series is not None:
        try:
            vol = volume_series.fillna(0).astype(float)
            cls = close_series.ffill().fillna(0).astype(float)
            cumulative_turnover = float((cls * vol).sum())
        except Exception:
            cumulative_turnover = None

    actual_timestamp_iso = same_day.index[-1].isoformat()

    debug = {
        "bar_count": int(len(same_day)),
        "first_bar": same_day.index[0].isoformat(),
        "last_bar": actual_timestamp_iso,
    }

    return last_price, cumulative_volume, cumulative_turnover, actual_timestamp_iso, debug


class Command(BaseCommand):
    help = "10:00時点の場中価格を取得し、ShihyoIntradayPrice(mode=open_1000) に保存します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--date",
            type=str,
            default="",
            help="対象日 (YYYY-MM-DD)。未指定なら今日",
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
            default=50,
            help="yfinance へ投げる同時取得件数",
        )
        parser.add_argument(
            "--include-etf",
            action="store_true",
            help="ETF/ETN も含めて取得する（未指定なら除外）",
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
        chunk_size = max(1, int(options.get("chunk") or 50))
        limit = max(0, int(options.get("limit") or 0))
        cutoff_time = time(10, 0, 0)
        include_etf = bool(options.get("include_etf"))

        codes_arg = (options.get("codes") or "").strip()
        requested_codes = [c.strip() for c in codes_arg.split(",") if c.strip()] if codes_arg else []

        masters_qs = StockMaster.objects.all().order_by("code")
        if requested_codes:
            masters_qs = masters_qs.filter(code__in=requested_codes)

        masters_all = list(masters_qs)
        if not masters_all:
            self.stdout.write(self.style.WARNING("対象の StockMaster が見つかりませんでした。"))
            return

        masters: list[StockMaster] = []
        skipped_non_jpx = 0
        skipped_etf = 0

        for master in masters_all:
            symbol = _to_symbol(master.code)
            if not symbol:
                skipped_non_jpx += 1
                continue

            if not include_etf and _is_etf_like(master):
                skipped_etf += 1
                continue

            masters.append(master)

        if limit > 0:
            masters = masters[:limit]

        symbol_map: dict[str, StockMaster] = {}
        for master in masters:
            symbol = _to_symbol(master.code)
            if symbol:
                symbol_map[symbol] = master

        symbols = list(symbol_map.keys())
        if not symbols:
            self.stdout.write(self.style.WARNING("取得対象の日本株コードがありませんでした。"))
            return

        prev_close_map = _get_prev_close_map(target_date)

        created_count = 0
        updated_count = 0
        no_price_count = 0
        failed_chunks = 0
        fallback_prev_close_count = 0

        self.stdout.write(
            f"[shihyo_load_intraday_prices] target_date={target_date} "
            f"mode={ShihyoIntradayPrice.MODE_OPEN_1000} symbols={len(symbols)} "
            f"skipped_non_jpx={skipped_non_jpx} skipped_etf={skipped_etf}"
        )

        for part in _chunked(symbols, chunk_size):
            try:
                data = yf.download(
                    tickers=part,
                    period="5d",
                    interval="1m",
                    progress=False,
                    group_by="ticker",
                    auto_adjust=False,
                    prepost=False,
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
                        df = data.copy()

                    last_price, cumulative_volume, cumulative_turnover, actual_timestamp_iso, debug = _pick_intraday_snapshot(
                        df=df,
                        target_date=target_date,
                        cutoff_time=cutoff_time,
                    )

                    if last_price is None or actual_timestamp_iso is None:
                        no_price_count += 1
                        continue

                    prev_close = prev_close_map.get(master.code)
                    if prev_close is None:
                        prev_close = _infer_prev_close_from_intraday_df(df, target_date)
                        if prev_close is not None:
                            fallback_prev_close_count += 1

                    change = None
                    change_pct = None

                    if prev_close is not None and prev_close != 0:
                        change = last_price - prev_close
                        change_pct = (change / prev_close) * 100.0

                    captured_at = datetime.fromisoformat(actual_timestamp_iso)

                    upserts.append(
                        (
                            master,
                            {
                                "date": target_date,
                                "mode": ShihyoIntradayPrice.MODE_OPEN_1000,
                                "captured_at": captured_at,
                                "last": last_price,
                                "prev_close": prev_close,
                                "change": change,
                                "change_pct": change_pct,
                                "volume": cumulative_volume,
                                "turnover": cumulative_turnover,
                                "source": "yfinance_1m",
                                "raw_payload": {
                                    "symbol": symbol,
                                    "target_date": target_date.isoformat(),
                                    "cutoff_time": cutoff_time.isoformat(),
                                    "actual_timestamp": actual_timestamp_iso,
                                    "snapshot_debug": debug,
                                },
                            },
                        )
                    )
                except Exception:
                    no_price_count += 1
                    continue

            with transaction.atomic():
                for master, row in upserts:
                    obj, created = ShihyoIntradayPrice.objects.update_or_create(
                        date=row["date"],
                        mode=row["mode"],
                        code=master.code,
                        defaults={
                            "captured_at": row["captured_at"],
                            "name": master.name or "",
                            "sector_code": master.sector_code or None,
                            "sector_name": _normalize_sector_name(master.sector_name),
                            "last": row["last"],
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
                "[shihyo_load_intraday_prices] done "
                f"created={created_count} updated={updated_count} "
                f"no_price={no_price_count} failed_chunks={failed_chunks} "
                f"fallback_prev_close={fallback_prev_close_count}"
            )
        )