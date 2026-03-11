"""
[FILE] shihyo_build_preopen_features.py
[PATH] <project_root>/shihyo/management/commands/shihyo_build_preopen_features.py

このファイルは何？
- 朝7:00モデル用の学習特徴量1行を ShihyoPreopenFeatureSnapshot に保存するコマンドです。
- 1行 = 1営業日の朝7:00時点で見えていた情報だけ、を保存します。
- 未来情報を混ぜないため、MarketIndicatorSnapshot は
  「対象日の 08:59 JST 以前」に作られたものだけを使います。
- 前営業日の市場の偏り(close)も一緒に保存します。
- MVP版として、まずは主要特徴量だけを保存します。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from urllib.parse import quote

import requests
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from shihyo.models import (
    MarketIndicatorSnapshot,
    ShihyoMarketBiasSnapshot,
    ShihyoPreopenFeatureSnapshot,
)


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_valid_number(value: Optional[float]) -> bool:
    if value is None:
        return False
    try:
        x = float(value)
    except (TypeError, ValueError):
        return False
    return not (x != x or x in (float("inf"), float("-inf")))


def _calc_pct(last_value: Optional[float], prev_close_value: Optional[float]) -> Optional[float]:
    if not _is_valid_number(last_value) or not _is_valid_number(prev_close_value):
        return None
    last_f = float(last_value)
    prev_f = float(prev_close_value)
    if prev_f == 0:
        return None
    return ((last_f / prev_f) - 1.0) * 100.0


def _make_local_aware(target_date: date, hh: int, mm: int, ss: int = 0) -> datetime:
    naive = datetime.combine(target_date, time(hh, mm, ss))
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _parse_date(value: str) -> date:
    y, m, d = [int(x) for x in value.split("-")]
    return date(y, m, d)


def _second_friday(target_date: date) -> date:
    first_day = target_date.replace(day=1)
    weekday_of_first = first_day.weekday()  # Monday=0 ... Sunday=6
    days_until_friday = (4 - weekday_of_first) % 7
    first_friday = first_day + timedelta(days=days_until_friday)
    second_friday = first_friday + timedelta(days=7)
    return second_friday


def _is_sq_week(target_date: date) -> bool:
    sq_day = _second_friday(target_date)
    week_start = sq_day - timedelta(days=sq_day.weekday())  # Monday
    week_end = week_start + timedelta(days=6)
    return week_start <= target_date <= week_end


class Command(BaseCommand):
    help = "朝7:00モデル用の特徴量1行を ShihyoPreopenFeatureSnapshot に保存します。"

    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--trade-date",
            type=str,
            default="",
            help="対象営業日(YYYY-MM-DD)。未指定ならJSTの今日。",
        )
        parser.add_argument(
            "--feature-version",
            type=str,
            default="preopen_feature_v1",
            help="保存する特徴量バージョン名。",
        )

    # =========================
    # 共通HTTP
    # =========================
    def _get_json(self, url: str, params: dict, referer: str) -> Optional[dict]:
        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Referer": referer,
        }
        try:
            r = requests.get(url, params=params, headers=headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    def _fetch_yahoo_daily_change(self, symbol: str) -> dict[str, Optional[float]]:
        """
        Yahoo Finance 日足から last / prev_close / pct_1d を取る。
        取れない場合は全部 None。
        """
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
        data = self._get_json(
            url=url,
            params={
                "interval": "1d",
                "range": "10d",
                "includePrePost": "false",
                "events": "div,splits",
            },
            referer=f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}/",
        )
        if not data:
            return {"last": None, "prev_close": None, "pct_1d": None}

        try:
            result = (((data or {}).get("chart") or {}).get("result") or [None])[0]
            if not result:
                return {"last": None, "prev_close": None, "pct_1d": None}

            meta = result.get("meta") or {}
            last_value = _safe_float(meta.get("regularMarketPrice"))
            prev_close = _safe_float(meta.get("previousClose"))

            if not _is_valid_number(last_value):
                indicators = result.get("indicators") or {}
                quote_list = indicators.get("quote") or []
                quote0 = quote_list[0] if quote_list else {}
                closes = quote0.get("close") or []

                valid_closes: list[float] = []
                for x in closes:
                    v = _safe_float(x)
                    if _is_valid_number(v):
                        valid_closes.append(float(v))

                if valid_closes:
                    last_value = valid_closes[-1]
                    if len(valid_closes) >= 2 and not _is_valid_number(prev_close):
                        prev_close = valid_closes[-2]

            pct_1d = _calc_pct(last_value, prev_close)
            return {
                "last": last_value if _is_valid_number(last_value) else None,
                "prev_close": prev_close if _is_valid_number(prev_close) else None,
                "pct_1d": pct_1d if _is_valid_number(pct_1d) else None,
            }
        except Exception:
            return {"last": None, "prev_close": None, "pct_1d": None}

    # =========================
    # ソースデータ取得
    # =========================
    def _pick_market_snapshot_for_preopen(self, trade_date: date) -> Optional[MarketIndicatorSnapshot]:
        """
        対象日の preopen 特徴量に使う MarketIndicatorSnapshot を選ぶ。
        未来情報を避けるため、
        「対象日の 08:59:59 JST 以前」の最新スナップショットのみを候補にする。
        """
        preopen_cutoff = _make_local_aware(trade_date, 8, 59, 59)

        return (
            MarketIndicatorSnapshot.objects
            .filter(created_at__lte=preopen_cutoff)
            .order_by("-created_at")
            .first()
        )

    def _pick_previous_close_bias(self, trade_date: date) -> Optional[ShihyoMarketBiasSnapshot]:
        """
        前営業日の close 市場バイアスを使う。
        trade_date より前の日付の最新 close を返す。
        """
        return (
            ShihyoMarketBiasSnapshot.objects
            .filter(mode=ShihyoMarketBiasSnapshot.MODE_CLOSE, date__lt=trade_date)
            .order_by("-date", "-updated_at")
            .first()
        )

    # =========================
    # legacy risk score
    # =========================
    def _build_legacy_risk_score(self, latest: MarketIndicatorSnapshot) -> float:
        nikkei_pct = _safe_float(latest.nikkei_futures_change_pct, 0.0) or 0.0
        fx_pct = _safe_float(latest.usdjpy_change_pct, 0.0) or 0.0
        vix_last = _safe_float(latest.vix_last, 0.0) or 0.0
        vix_pct = _safe_float(latest.vix_change_pct, 0.0) or 0.0

        score = 50.0
        score += max(0.0, -nikkei_pct) * 10.0
        score -= max(0.0, nikkei_pct) * 8.0

        score += max(0.0, -fx_pct) * 20.0
        score -= max(0.0, fx_pct) * 12.0

        if vix_last > 15:
            score += (vix_last - 15.0) * 1.7

        score += max(0.0, vix_pct) * 0.6
        score -= max(0.0, -vix_pct) * 0.35

        action_title = latest.action_title or ""
        if "守る" in action_title:
            score += 8.0
        elif "様子見" in action_title:
            score += 0.0
        else:
            score -= 8.0

        score = max(0.0, min(100.0, score))
        return round(score, 2)

    def handle(self, *args, **options):
        trade_date = _parse_date(options["trade_date"]) if options.get("trade_date") else timezone.localdate()
        feature_version = str(options.get("feature_version") or "preopen_feature_v1").strip()

        snapshot = self._pick_market_snapshot_for_preopen(trade_date)
        if not snapshot:
            self.stdout.write(
                self.style.ERROR(
                    f"preopen用に使える MarketIndicatorSnapshot がありません。"
                    f" 先に `python manage.py shihyo_fetch` を実行してください。"
                )
            )
            return

        close_bias = self._pick_previous_close_bias(trade_date)

        raw_payload = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}
        nikkei_spot = raw_payload.get("nikkei_spot") or {}

        prev_close_n225 = _safe_float(nikkei_spot.get("close"))
        prev_open_n225 = _safe_float(nikkei_spot.get("open"))
        prev_high_n225 = _safe_float(nikkei_spot.get("high"))
        prev_low_n225 = _safe_float(nikkei_spot.get("low"))

        nikkei_futures_last = _safe_float(snapshot.nikkei_futures_last)
        nikkei_futures_pct_vs_prev_close_n225 = _calc_pct(nikkei_futures_last, prev_close_n225)
        nikkei_futures_gap_pts = None
        if _is_valid_number(nikkei_futures_last) and _is_valid_number(prev_close_n225):
            nikkei_futures_gap_pts = float(nikkei_futures_last) - float(prev_close_n225)

        # --- 外部環境の日足（安定して前日比を取りやすいもの） ---
        sp500 = self._fetch_yahoo_daily_change("^GSPC")
        nasdaq100 = self._fetch_yahoo_daily_change("^NDX")
        sox = self._fetch_yahoo_daily_change("^SOX")

        # --- カレンダー特徴 ---
        weekday = trade_date.weekday()
        month = trade_date.month
        is_sq_week = _is_sq_week(trade_date)

        is_major_holiday_adjacent = False
        close_bias_date = close_bias.date if close_bias else None
        if close_bias_date:
            gap_days = (trade_date - close_bias_date).days
            if gap_days >= 4:
                is_major_holiday_adjacent = True

        # --- 市場の偏り(close) ---
        prev_market_bias_tone = close_bias.tone if close_bias else ""
        strong = list(close_bias.strong_sectors or [])[:3] if close_bias else []
        weak = list(close_bias.weak_sectors or [])[:3] if close_bias else []

        # --- legacy risk ---
        risk_score_legacy = self._build_legacy_risk_score(snapshot)

        asof_at = _make_local_aware(trade_date, 7, 0, 0)

        payload = {
            "asof_at": asof_at,
            "market": "JP",

            "prev_close_n225": prev_close_n225,
            "prev_open_n225": prev_open_n225,
            "prev_high_n225": prev_high_n225,
            "prev_low_n225": prev_low_n225,

            "nikkei_futures_last": nikkei_futures_last,
            "nikkei_futures_pct_vs_prev_close_n225": nikkei_futures_pct_vs_prev_close_n225,
            "nikkei_futures_gap_pts": nikkei_futures_gap_pts,

            "usdjpy_last": _safe_float(snapshot.usdjpy_last),
            "usdjpy_pct_1d": _safe_float(snapshot.usdjpy_change_pct),

            "vix_last": _safe_float(snapshot.vix_last),
            "vix_pct_1d": _safe_float(snapshot.vix_change_pct),

            "sp500_pct_1d": sp500["pct_1d"],
            "nasdaq100_pct_1d": nasdaq100["pct_1d"],
            "sox_pct_1d": sox["pct_1d"],

            "prev_market_bias_tone": prev_market_bias_tone,
            "prev_strong_sector_1": str(strong[0]) if len(strong) >= 1 else "",
            "prev_strong_sector_2": str(strong[1]) if len(strong) >= 2 else "",
            "prev_strong_sector_3": str(strong[2]) if len(strong) >= 3 else "",
            "prev_weak_sector_1": str(weak[0]) if len(weak) >= 1 else "",
            "prev_weak_sector_2": str(weak[1]) if len(weak) >= 2 else "",
            "prev_weak_sector_3": str(weak[2]) if len(weak) >= 3 else "",

            "weekday": weekday,
            "month": month,
            "is_sq_week": is_sq_week,
            "is_major_holiday_adjacent": is_major_holiday_adjacent,

            "risk_score_legacy": risk_score_legacy,
            "risk_title_legacy": snapshot.action_title or "",
            "nikkei_label_legacy": snapshot.nikkei_label or "",
            "fx_label_legacy": snapshot.fx_label or "",
            "vix_label_legacy": snapshot.vix_label or "",

            "feature_version": feature_version,
            "source_payload": {
                "trade_date": trade_date.isoformat(),
                "market_indicator_snapshot_id": snapshot.id,
                "market_indicator_snapshot_created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
                "close_bias_snapshot_id": close_bias.id if close_bias else None,
                "close_bias_date": close_bias.date.isoformat() if close_bias else None,
                "sp500_daily": sp500,
                "nasdaq100_daily": nasdaq100,
                "sox_daily": sox,
                "build_mode": "preopen_feature_mvp_v1",
            },
        }

        obj, created = ShihyoPreopenFeatureSnapshot.objects.update_or_create(
            trade_date=trade_date,
            slot=ShihyoPreopenFeatureSnapshot.SLOT_PREOPEN_0700,
            defaults=payload,
        )

        action = "created" if created else "updated"

        self.stdout.write(
            self.style.SUCCESS(
                f"[shihyo_build_preopen_features] {action} "
                f"trade_date={obj.trade_date} "
                f"slot={obj.slot} "
                f"prev_close_n225={obj.prev_close_n225} "
                f"futures_gap_pts={obj.nikkei_futures_gap_pts} "
                f"bias_tone={obj.prev_market_bias_tone or '-'} "
                f"feature_version={obj.feature_version}"
            )
        )