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
- 日経平均現物の previous_close を「必ず決めにいく」方式に変更
- 優先順位:
  1) Yahoo Finance meta.previousClose
  2) Yahoo Finance の配列から前営業日終値を復元
  3) DBに保存済みの直近 nikkei_spot_previous_close
  4) DBに保存済みの直近 nikkei_spot close
- VIX は Yahoo Finance (^VIX) を優先し、ダメなら Stooq にフォールバック
- MarketIndicatorSnapshot.nikkei_spot_previous_close に正式保存する
"""

from __future__ import annotations

import math
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


class Command(BaseCommand):
    help = "Fetch Nikkei futures, Nikkei spot, USDJPY, VIX and store snapshot."

    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
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

    # =========================
    # 日経平均現物 previous_close の fallback
    # =========================
    def _load_last_saved_nikkei_previous_close(self) -> tuple[Optional[float], str]:
        """
        直近保存済みの previous_close を取得する。
        優先:
          1) 正式カラム nikkei_spot_previous_close
          2) raw_payload["nikkei_spot"]["previous_close"]
        """
        row = (
            MarketIndicatorSnapshot.objects
            .exclude(nikkei_spot_previous_close__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if row and _is_valid_number(row.nikkei_spot_previous_close):
            return float(row.nikkei_spot_previous_close), "db_saved_previous_close"

        rows = MarketIndicatorSnapshot.objects.order_by("-created_at")[:20]
        for x in rows:
            raw = x.raw_payload if isinstance(x.raw_payload, dict) else {}
            nikkei_spot = raw.get("nikkei_spot") or {}
            prev_close = nikkei_spot.get("previous_close")
            try:
                prev_close = float(prev_close)
            except Exception:
                prev_close = None
            if _is_valid_number(prev_close) and prev_close > 0:
                return float(prev_close), "db_raw_previous_close"

        return None, "unavailable"

    def _load_last_saved_nikkei_close(self) -> tuple[Optional[float], str]:
        """
        直近保存済みの日経平均現物 close を取得する。
        previous_close が取れないときの最終fallback。
        """
        rows = MarketIndicatorSnapshot.objects.order_by("-created_at")[:30]
        for x in rows:
            raw = x.raw_payload if isinstance(x.raw_payload, dict) else {}
            nikkei_spot = raw.get("nikkei_spot") or {}
            close_value = nikkei_spot.get("close")
            try:
                close_value = float(close_value)
            except Exception:
                close_value = None
            if _is_valid_number(close_value) and close_value > 0:
                return float(close_value), "db_last_spot_close"

        return None, "unavailable"

    # =========================
    # Yahooレスポンスから previous_close を復元
    # =========================
    def _extract_previous_close_from_chart_result(self, result: dict) -> Optional[float]:
        """
        Yahoo chart API の result から previous close をできるだけ復元する。

        優先:
          1) meta.previousClose
          2) indicators.quote[0].close の末尾から
             「最新closeの1つ前の有効close」
        """
        if not isinstance(result, dict):
            return None

        meta = result.get("meta") or {}
        prev_close = meta.get("previousClose")
        try:
            prev_close = float(prev_close)
        except Exception:
            prev_close = None

        if _is_valid_number(prev_close) and prev_close > 0:
            return float(prev_close)

        indicators = result.get("indicators") or {}
        quote_list = indicators.get("quote") or []
        if not quote_list:
            return None

        quote0 = quote_list[0] or {}
        closes = quote0.get("close") or []
        if not isinstance(closes, list):
            return None

        valid_closes: list[float] = []
        for x in closes:
            try:
                v = float(x)
            except Exception:
                continue
            if _is_valid_number(v) and v > 0:
                valid_closes.append(v)

        if len(valid_closes) >= 2:
            return float(valid_closes[-2])

        return None

    # =========================
    # 日経平均現物
    # =========================
    def _fetch_nikkei_spot_from_yahoo(self) -> Optional[dict]:
        """
        Yahoo Finance の chart API から ^N225 を取得する。
        previous_close が取れるのでこちらを優先する。
        """
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EN225"
        params = {
            "interval": "1d",
            "range": "10d",
            "includePrePost": "false",
            "events": "div,splits",
        }
        data = self._get_json(
            url=url,
            params=params,
            referer="https://finance.yahoo.com/quote/%5EN225/",
        )
        if not data:
            return None

        try:
            result = (((data or {}).get("chart") or {}).get("result") or [None])[0]
            if not result:
                return None

            meta = result.get("meta") or {}
            close_value = meta.get("regularMarketPrice")
            open_value = meta.get("regularMarketOpen")

            close_value = float(close_value) if close_value is not None else float("nan")
            open_value = float(open_value) if open_value is not None else float("nan")

            if not _is_valid_number(close_value) or close_value <= 0:
                return None

            previous_close = self._extract_previous_close_from_chart_result(result)
            calc = _calc_from_prev_close(close_value, previous_close)

            quote = Quote(
                symbol="^N225",
                date="",
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
                "previous_close": previous_close if _is_valid_number(previous_close) else None,
                "previous_close_source": (
                    "yahoo_meta_or_chart"
                    if _is_valid_number(previous_close)
                    else "unavailable"
                ),
            }
        except Exception:
            return None

    def _fetch_nikkei_spot_from_stooq(self, client: StooqClient) -> Optional[dict]:
        """
        Stooq から日経平均現物を取得する。
        previous_close は別途 fallback で決める。
        """
        candidates = ["^nkx", "nkx"]

        for symbol in candidates:
            try:
                q = client.fetch_latest(symbol)
                if _is_valid_number(q.close) and q.close > 0:
                    return {
                        "source": "stooq",
                        "symbol": symbol,
                        "quote": q,
                    }
            except Exception:
                continue

        return None

    def _resolve_nikkei_previous_close(
        self,
        close_value: Optional[float],
        yahoo_result: Optional[dict],
    ) -> tuple[Optional[float], str]:
        """
        previous_close を必ず決めにいく。
        優先:
          1) Yahoo 直取得
          2) DB保存済み previous_close
          3) DB保存済み last spot close
        """
        if yahoo_result:
            prev_close = yahoo_result.get("previous_close")
            if _is_valid_number(prev_close) and float(prev_close) > 0:
                return float(prev_close), str(yahoo_result.get("previous_close_source") or "yahoo")

        prev_close, source = self._load_last_saved_nikkei_previous_close()
        if _is_valid_number(prev_close) and float(prev_close) > 0:
            return float(prev_close), source

        prev_close, source = self._load_last_saved_nikkei_close()
        if _is_valid_number(prev_close) and float(prev_close) > 0:
            return float(prev_close), source

        return None, "unavailable"

    def _fetch_nikkei_spot(
        self,
        client: StooqClient,
    ) -> tuple[Optional[Quote], dict, str, Optional[float], str]:
        """
        日経平均現物を取得する。
        戻り値:
          (quote or None, calc_dict, source_name, previous_close, previous_close_source)
        """
        yahoo_result = self._fetch_nikkei_spot_from_yahoo()
        if yahoo_result:
            quote = yahoo_result["quote"]
            close_value = quote.close
            previous_close, prev_source = self._resolve_nikkei_previous_close(close_value, yahoo_result)
            calc = _calc_from_prev_close(close_value, previous_close)
            return quote, calc, str(yahoo_result["source"]), previous_close, prev_source

        stooq_result = self._fetch_nikkei_spot_from_stooq(client)
        if stooq_result:
            quote = stooq_result["quote"]
            close_value = quote.close
            previous_close, prev_source = self._resolve_nikkei_previous_close(close_value, None)
            calc = _calc_from_prev_close(close_value, previous_close)
            return quote, calc, str(stooq_result["source"]), previous_close, prev_source

        previous_close, prev_source = self._resolve_nikkei_previous_close(None, None)
        return None, {"change": 0.0, "pct": 0.0}, "unavailable", previous_close, prev_source

    # =========================
    # VIX
    # =========================
    def _fetch_vix_from_yahoo(self) -> Optional[dict]:
        """
        Yahoo Finance の chart API から ^VIX を取得する。
        朝時点はこちらのほうが新しいことが多いので優先で使う。
        """
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
        params = {
            "interval": "1d",
            "range": "10d",
            "includePrePost": "false",
            "events": "div,splits",
        }
        data = self._get_json(
            url=url,
            params=params,
            referer="https://finance.yahoo.com/quote/%5EVIX/",
        )
        if not data:
            return None

        try:
            result = (((data or {}).get("chart") or {}).get("result") or [None])[0]
            if not result:
                return None

            meta = result.get("meta") or {}
            close_value = meta.get("regularMarketPrice")
            open_value = meta.get("regularMarketOpen")
            previous_close = self._extract_previous_close_from_chart_result(result)

            close_value = float(close_value) if close_value is not None else float("nan")
            open_value = float(open_value) if open_value is not None else float("nan")

            if not _is_valid_number(close_value) or close_value <= 0:
                return None

            calc = _calc_from_prev_close(close_value, previous_close)

            quote = Quote(
                symbol="^VIX",
                date="",
                time="",
                open=open_value,
                high=float("nan"),
                low=float("nan"),
                close=close_value,
                volume=float("nan"),
            )

            return {
                "source": "yahoo_finance",
                "symbol": "^VIX",
                "quote": quote,
                "calc": calc,
            }
        except Exception:
            return None

    def _fetch_vix(self, client: StooqClient) -> tuple[Optional[Quote], dict, str]:
        """
        VIX は Yahoo Finance 優先、ダメなら Stooq にフォールバック。
        """
        yahoo_result = self._fetch_vix_from_yahoo()
        if yahoo_result:
            return yahoo_result["quote"], yahoo_result["calc"], str(yahoo_result["source"])

        try:
            q = client.fetch_latest("vi.c")
            calc = client.calc_change(q.close, q.open)
            if _is_valid_number(q.close) and q.close > 0:
                return q, calc, "stooq"
        except Exception:
            pass

        return None, {"change": 0.0, "pct": 0.0}, "unavailable"

    # =========================
    # 市場の偏り
    # =========================
    def _load_market_bias_snapshot(self) -> dict[str, Any]:
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

    # =========================
    # メイン
    # =========================
    def handle(self, *args, **options):
        client = StooqClient()

        symbols = {
            "nikkei_futures": "ny.f",
            "fx": "usdjpy",
        }

        raw = {}
        err = ""

        try:
            q_nf = client.fetch_latest(symbols["nikkei_futures"])
            q_f = client.fetch_latest(symbols["fx"])
            q_v, v, vix_source = self._fetch_vix(client)

            if q_v is None or not _is_valid_number(q_v.close):
                raise ValueError("VIX unavailable")

            q_ns, ns, nikkei_spot_source, nikkei_spot_prev_close, nikkei_spot_prev_source = self._fetch_nikkei_spot(client)

            if q_ns is None or not _is_valid_number(q_ns.close):
                raise ValueError("nikkei_spot unavailable")

            nf = client.calc_change(q_nf.close, q_nf.open)
            f = client.calc_change(q_f.close, q_f.open)

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
                },
                "nikkei_spot": {
                    **q_ns.__dict__,
                    "calc": ns,
                    "source": nikkei_spot_source,
                    "previous_close": nikkei_spot_prev_close,
                    "previous_close_source": nikkei_spot_prev_source,
                    "available": True,
                },
                "fx": {
                    **q_f.__dict__,
                    "calc": f,
                },
                "vix": {
                    **q_v.__dict__,
                    "calc": v,
                    "source": vix_source,
                },
                "market_bias": market_bias,
                "fetched_at": timezone.now().isoformat(),
            }
            raw = _json_safe(raw)

            snapshot_source = "mixed"
            if nikkei_spot_source == "stooq" and vix_source == "stooq":
                snapshot_source = "stooq"

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
                nikkei_spot_previous_close=(
                    float(nikkei_spot_prev_close)
                    if _is_valid_number(nikkei_spot_prev_close)
                    else None
                ),
                nikkei_label=judged.nikkei_label,
                fx_label=judged.fx_label,
                vix_label=judged.vix_label,
                action_title=judged.action_title,
                action_lines=judged.action_lines,
                source=snapshot_source,
                raw_payload=raw,
                error="",
            )

            messages = ["Saved snapshot OK"]
            messages.append(f"nikkei_spot={nikkei_spot_source}")
            messages.append(f"nikkei_prev={nikkei_spot_prev_source}")
            messages.append(f"vix={vix_source}")

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