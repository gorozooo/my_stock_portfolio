"""
[FILE] shihyo_fetch.py
[PATH] <project_root>/shihyo/management/commands/shihyo_fetch.py

このファイルは何？
- 指標（先物・ドル円・VIX・日経平均現物）を取得してDBへ保存するコマンドです。
- 日経平均の現物値は raw_payload["nikkei_spot"] に保存します。
- 市場の偏りはスクレイピングではなく、
  shihyo.ShihyoMarketBiasSnapshot(mode=close) の最新データを読み込んで
  raw_payload["market_bias"] に保存します。

今回の修正ポイント：
- 日経平均の previous_close 解決ロジックを再修正
- previous_close は「過去 snapshot の previous_close」を引き継がない
- previous_close は「異なる close」ではなく、
  「現在の nikkei_spot.date より前の直近 market date の close」を使う
- raw_payload["nikkei_spot"]["calc"] も previous_close 基準で再計算する
"""

from __future__ import annotations

import math
from datetime import date as date_cls
from typing import Any, Optional

import requests
from django.core.management.base import BaseCommand
from django.utils import timezone

from shihyo.models import MarketIndicatorSnapshot, ShihyoMarketBiasSnapshot
from shihyo.services.judge import judge
from shihyo.services.stooq_client import Quote, StooqClient


def _json_safe(x: Any) -> Any:
    """
    JSONField(SQLite)が受け付けない値（NaN/Infinity）を None に変換して安全化する。
    dict/list/tuple も再帰的に処理する。
    """
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    if isinstance(x, dict):
        return {str(k): _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    return x


def _is_valid_number(value: Optional[float]) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return False
    return True


def _calc_from_prev_close(close_value: Optional[float], prev_close_value: Optional[float]) -> dict[str, float]:
    """
    close と previous close から change / pct を作る。
    欠損時は 0 を返す。
    """
    if not _is_valid_number(close_value) or not _is_valid_number(prev_close_value):
        return {"change": 0.0, "pct": 0.0}

    close_f = float(close_value)
    prev_f = float(prev_close_value)

    if prev_f == 0:
        return {"change": 0.0, "pct": 0.0}

    change = close_f - prev_f
    pct = (change / prev_f) * 100.0
    return {"change": float(change), "pct": float(pct)}


def _parse_spot_date(value: Any) -> Optional[date_cls]:
    """
    nikkei_spot.date を date に寄せる。
    想定:
    - YYYY-MM-DD
    - YYYY/MM/DD
    - YYYYMMDD
    """
    if value is None:
        return None

    s = str(value).strip()
    if not s:
        return None

    try:
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return date_cls.fromisoformat(s[:10])
    except Exception:
        pass

    try:
        if len(s) >= 10 and s[4] == "/" and s[7] == "/":
            return date_cls.fromisoformat(s[:10].replace("/", "-"))
    except Exception:
        pass

    try:
        if len(s) >= 8 and s[:8].isdigit():
            y = int(s[:4])
            m = int(s[4:6])
            d = int(s[6:8])
            return date_cls(y, m, d)
    except Exception:
        pass

    return None


class Command(BaseCommand):
    help = "Fetch Nikkei futures, Nikkei spot, USDJPY, VIX and store snapshot."

    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )

    def _fetch_nikkei_spot_from_stooq(self, client: StooqClient) -> Optional[dict]:
        """
        Stooq から日経平均現物を取得する。
        優先順:
          1. ^nkx
          2. nkx
        """
        candidates = ["^nkx", "nkx"]

        for symbol in candidates:
            try:
                q = client.fetch_latest(symbol)
                calc = client.calc_change(q.close, q.open)

                if _is_valid_number(q.close) and q.close > 0:
                    return {
                        "source": "stooq",
                        "symbol": symbol,
                        "quote": q,
                        "calc": calc,
                        "previous_close": None,
                    }
            except Exception:
                continue

        return None

    def _fetch_nikkei_spot_from_yahoo(self) -> Optional[dict]:
        """
        Yahoo Finance の chart API から ^N225 を取得する。
        非公式系のため、最後のフォールバックとして使う。
        """
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EN225"
        params = {
            "interval": "1d",
            "range": "5d",
            "includePrePost": "false",
            "events": "div,splits",
        }
        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://finance.yahoo.com/quote/%5EN225/",
        }

        try:
            r = requests.get(url, params=params, headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()

            result = (((data or {}).get("chart") or {}).get("result") or [None])[0]
            if not result:
                return None

            meta = result.get("meta") or {}
            close_value = meta.get("regularMarketPrice")
            prev_close_value = meta.get("previousClose")
            open_value = meta.get("regularMarketOpen")
            market_date = meta.get("regularMarketTime")

            close_value = float(close_value) if close_value is not None else float("nan")
            prev_close_value = float(prev_close_value) if prev_close_value is not None else float("nan")
            open_value = float(open_value) if open_value is not None else float("nan")

            if not _is_valid_number(close_value) or close_value <= 0:
                return None

            calc = _calc_from_prev_close(close_value, prev_close_value)

            quote_date = ""
            if market_date is not None:
                try:
                    dt_local = timezone.datetime.fromtimestamp(int(market_date), tz=timezone.utc)
                    quote_date = dt_local.astimezone(timezone.get_fixed_timezone(9 * 60)).date().isoformat()
                except Exception:
                    quote_date = ""

            quote = Quote(
                symbol="^N225",
                date=quote_date,
                time="",
                open=open_value,
                high=float("nan"),
                low=float("nan"),
                close=close_value,
                volume=float("nan"),
            )

            return {
                "source": "yahoo_finance",
                "symbol": "^N225",
                "quote": quote,
                "calc": calc,
                "previous_close": prev_close_value if _is_valid_number(prev_close_value) else None,
            }
        except Exception:
            return None

    def _fetch_nikkei_spot(self, client: StooqClient) -> tuple[Optional[Quote], dict, str, Optional[float]]:
        """
        日経平均現物を取得する。
        戻り値:
          (quote or None, calc_dict, source_name, api_previous_close)
        """
        stooq_result = self._fetch_nikkei_spot_from_stooq(client)
        if stooq_result:
            return (
                stooq_result["quote"],
                stooq_result["calc"],
                stooq_result["source"],
                stooq_result["previous_close"],
            )

        yahoo_result = self._fetch_nikkei_spot_from_yahoo()
        if yahoo_result:
            return (
                yahoo_result["quote"],
                yahoo_result["calc"],
                yahoo_result["source"],
                yahoo_result["previous_close"],
            )

        return None, {"change": 0.0, "pct": 0.0}, "unavailable", None

    def _extract_saved_nikkei_spot_info(
        self,
        snapshot: MarketIndicatorSnapshot,
    ) -> tuple[Optional[float], Optional[date_cls]]:
        raw_payload = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}
        nikkei_spot = raw_payload.get("nikkei_spot") or {}

        close_value = nikkei_spot.get("close")
        close_value = float(close_value) if close_value is not None else float("nan")
        if not (_is_valid_number(close_value) and close_value > 0):
            close_value = None
        else:
            close_value = float(close_value)

        spot_date = _parse_spot_date(nikkei_spot.get("date"))

        return close_value, spot_date

    def _find_previous_session_close_from_db(
        self,
        current_spot_date: Optional[date_cls],
    ) -> tuple[Optional[float], str]:
        """
        DB内の過去 snapshot を新しい順に見て、
        nikkei_spot.date が現在の nikkei_spot.date より前の最初の行の close を使う。

        これにより、
        - 同じ営業日の場中値を previous_close に誤採用しない
        - 前回保存した previous_close の連鎖を引き継がない
        """
        if current_spot_date is None:
            return None, "unavailable"

        qs = MarketIndicatorSnapshot.objects.order_by("-created_at")
        for row in qs:
            saved_close, saved_spot_date = self._extract_saved_nikkei_spot_info(row)

            if not (_is_valid_number(saved_close) and saved_close and saved_close > 0):
                continue
            if saved_spot_date is None:
                continue

            if saved_spot_date < current_spot_date:
                return float(saved_close), "db_last_prior_spot_date_close"

        return None, "unavailable"

    def _resolve_nikkei_previous_close(
        self,
        current_spot_date: Optional[date_cls],
        api_previous_close: Optional[float],
    ) -> tuple[Optional[float], str]:
        """
        previous_close の解決順:
          1) API が previousClose を返したらそれを使う
          2) 無ければ DB の過去 snapshot から
             「現在の nikkei_spot.date より前」の直近 close を使う
        """
        if _is_valid_number(api_previous_close) and float(api_previous_close) > 0:
            return float(api_previous_close), "api_previous_close"

        db_prev_close, db_source = self._find_previous_session_close_from_db(current_spot_date)
        if _is_valid_number(db_prev_close) and float(db_prev_close) > 0:
            return float(db_prev_close), db_source

        return None, "unavailable"

    def _load_market_bias_snapshot(self) -> dict[str, Any]:
        """
        自前集計済みの市場の偏りスナップショット（mode=close）を読む。
        なければ準備中表示用の dict を返す。
        """
        latest = (
            ShihyoMarketBiasSnapshot.objects
            .filter(mode=ShihyoMarketBiasSnapshot.MODE_CLOSE)
            .order_by("-date", "-updated_at")
            .first()
        )

        if not latest:
            return {
                "available": False,
                "summary_title": "準備中",
                "summary_text": "日本株市場で、どこに資金が入っているか / どこが売られているか を集計中です。",
                "tone": "neutral",
                "strong_sectors": [],
                "weak_sectors": [],
                "hot_themes": [],
                "cold_themes": [],
                "snapshot_date": None,
                "source": "shihyo_market_bias_snapshot",
            }

        return {
            "available": True,
            "summary_title": latest.summary_title or "市場の偏り",
            "summary_text": latest.summary_text or "",
            "tone": latest.tone or "neutral",
            "strong_sectors": list(latest.strong_sectors or []),
            "weak_sectors": list(latest.weak_sectors or []),
            "hot_themes": list(latest.hot_themes or []),
            "cold_themes": list(latest.cold_themes or []),
            "snapshot_date": latest.date.isoformat() if latest.date else None,
            "source": "shihyo_market_bias_snapshot",
        }

    def handle(self, *args, **options):
        client = StooqClient()

        symbols = {
            "nikkei_futures": "ny.f",
            "fx": "usdjpy",
            "vix": "vi.c",
        }

        raw = {}
        err = ""

        try:
            q_nf = client.fetch_latest(symbols["nikkei_futures"])
            q_f = client.fetch_latest(symbols["fx"])
            q_v = client.fetch_latest(symbols["vix"])

            q_ns, ns, nikkei_spot_source, nikkei_spot_api_prev_close = self._fetch_nikkei_spot(client)

            current_spot_date = _parse_spot_date(q_ns.date) if q_ns else None

            nikkei_prev_close, nikkei_prev_source = self._resolve_nikkei_previous_close(
                current_spot_date=current_spot_date,
                api_previous_close=nikkei_spot_api_prev_close,
            )

            # 日経現物の change/pct は open 基準ではなく previous_close 基準に揃える
            if q_ns and _is_valid_number(q_ns.close) and q_ns.close > 0 and _is_valid_number(nikkei_prev_close):
                ns = _calc_from_prev_close(q_ns.close, nikkei_prev_close)

            nf = client.calc_change(q_nf.close, q_nf.open)
            f = client.calc_change(q_f.close, q_f.open)
            v = client.calc_change(q_v.close, q_v.open)

            market_bias = self._load_market_bias_snapshot()

            judged = judge(
                nikkei_change_pct=nf["pct"],
                fx_change_pct=f["pct"],
                vix_last=q_v.close,
                vix_change_pct=v["pct"],
            )

            raw = {
                "nikkei_futures": {
                    **q_nf.__dict__,
                    "calc": nf,
                    "source": "stooq",
                },
                "nikkei_spot": {
                    **(
                        q_ns.__dict__
                        if q_ns
                        else {
                            "symbol": "NIKKEI_SPOT",
                            "date": "",
                            "time": "",
                            "open": None,
                            "high": None,
                            "low": None,
                            "close": None,
                            "volume": None,
                        }
                    ),
                    "calc": ns,
                    "source": nikkei_spot_source,
                    "available": bool(q_ns and _is_valid_number(q_ns.close) and q_ns.close > 0),
                    "previous_close": nikkei_prev_close if _is_valid_number(nikkei_prev_close) else None,
                    "previous_close_source": nikkei_prev_source,
                },
                "fx": {
                    **q_f.__dict__,
                    "calc": f,
                    "source": "stooq",
                },
                "vix": {
                    **q_v.__dict__,
                    "calc": v,
                    "source": "stooq",
                },
                "market_bias": market_bias,
                "fetched_at": timezone.now().isoformat(),
            }
            raw = _json_safe(raw)

            MarketIndicatorSnapshot.objects.create(
                nikkei_futures_last=q_nf.close,
                nikkei_futures_change=nf["change"],
                nikkei_futures_change_pct=nf["pct"],
                usdjpy_last=q_f.close,
                usdjpy_change=f["change"],
                usdjpy_change_pct=f["pct"],
                vix_last=q_v.close,
                vix_change=v["change"],
                vix_change_pct=v["pct"],
                nikkei_spot_previous_close=nikkei_prev_close if _is_valid_number(nikkei_prev_close) else None,
                nikkei_label=judged.nikkei_label,
                fx_label=judged.fx_label,
                vix_label=judged.vix_label,
                action_title=judged.action_title,
                action_lines=judged.action_lines,
                raw_payload=raw,
                error="",
            )

            messages = ["Saved snapshot OK"]

            if q_ns and _is_valid_number(q_ns.close) and q_ns.close > 0:
                messages.append(f"nikkei_spot={nikkei_spot_source}")
            else:
                messages.append("nikkei_spot=unavailable")

            if _is_valid_number(nikkei_prev_close):
                messages.append(f"nikkei_prev={nikkei_prev_source}")
            else:
                messages.append("nikkei_prev=unavailable")

            messages.append("vix=stooq")

            if market_bias.get("available"):
                messages.append(f"market_bias=available({market_bias.get('snapshot_date')})")
            else:
                messages.append("market_bias=unavailable")

            self.stdout.write(self.style.SUCCESS(" / ".join(messages)))

        except Exception as e:
            err = str(e)

            safe_raw = _json_safe({
                "fetched_at": timezone.now().isoformat(),
                "error": err,
                "raw": raw,
            })

            MarketIndicatorSnapshot.objects.create(
                action_title="🔴 取得失敗（前回の判断を優先）",
                action_lines=[
                    "データ取得に失敗したため、新しい判断を作れませんでした",
                    "通信が戻ったら自動で更新されます",
                ],
                raw_payload=safe_raw,
                error=err,
            )

            self.stdout.write(self.style.ERROR(f"Saved snapshot ERROR: {err}"))