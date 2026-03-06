"""
[FILE] shihyo_fetch.py
[PATH] <project_root>/shihyo/management/commands/shihyo_fetch.py

このファイルは何？
- 指標（先物・ドル円・VIX・日経平均現物）を取得してDBへ保存するコマンドです。
- 日経平均の現物値は raw_payload["nikkei_spot"] に保存します。
- DBモデルは変えずに、AI予想で使えるようにしています。

今回の正式版のポイント：
- 日経平均現物は Stooq の ^nkx を優先して取得
- 取れないときは nkx を試す
- さらに取れないときだけ Yahoo Finance ^N225 を試す
- それでも取れない場合は、日経平均現物は None のまま保存
- 「取れなかったら先物で代用」はしない
"""

from __future__ import annotations

import math
from typing import Any, Optional

import requests
from django.core.management.base import BaseCommand
from django.utils import timezone

from shihyo.models import MarketIndicatorSnapshot
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
            "User-Agent": "Mozilla/5.0",
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

            close_value = float(close_value) if close_value is not None else float("nan")
            prev_close_value = float(prev_close_value) if prev_close_value is not None else float("nan")
            open_value = float(open_value) if open_value is not None else float("nan")

            if not _is_valid_number(close_value) or close_value <= 0:
                return None

            calc = _calc_from_prev_close(close_value, prev_close_value)

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
                "previous_close": prev_close_value if _is_valid_number(prev_close_value) else None,
            }
        except Exception:
            return None

    def _fetch_nikkei_spot(self, client: StooqClient) -> tuple[Optional[Quote], dict, str]:
        """
        日経平均現物を取得する。
        戻り値:
          (quote or None, calc_dict, source_name)
        """
        stooq_result = self._fetch_nikkei_spot_from_stooq(client)
        if stooq_result:
            return stooq_result["quote"], stooq_result["calc"], stooq_result["source"]

        yahoo_result = self._fetch_nikkei_spot_from_yahoo()
        if yahoo_result:
            return yahoo_result["quote"], yahoo_result["calc"], yahoo_result["source"]

        return None, {"change": 0.0, "pct": 0.0}, "unavailable"

    def handle(self, *args, **options):
        client = StooqClient()

        # Stooq symbols
        symbols = {
            "nikkei_futures": "ny.f",  # Nikkei 225 futures
            "fx": "usdjpy",            # USDJPY
            "vix": "vi.c",             # VIX
        }

        raw = {}
        err = ""

        try:
            q_nf = client.fetch_latest(symbols["nikkei_futures"])
            q_f = client.fetch_latest(symbols["fx"])
            q_v = client.fetch_latest(symbols["vix"])

            q_ns, ns, nikkei_spot_source = self._fetch_nikkei_spot(client)

            nf = client.calc_change(q_nf.close, q_nf.open)
            f = client.calc_change(q_f.close, q_f.open)
            v = client.calc_change(q_v.close, q_v.open)

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
                    **(q_ns.__dict__ if q_ns else {
                        "symbol": "NIKKEI_SPOT",
                        "date": "",
                        "time": "",
                        "open": None,
                        "high": None,
                        "low": None,
                        "close": None,
                        "volume": None,
                    }),
                    "calc": ns,
                    "source": nikkei_spot_source,
                    "available": bool(q_ns and _is_valid_number(q_ns.close) and q_ns.close > 0),
                },
                "fx": {
                    **q_f.__dict__,
                    "calc": f,
                },
                "vix": {
                    **q_v.__dict__,
                    "calc": v,
                },
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
                nikkei_label=judged.nikkei_label,
                fx_label=judged.fx_label,
                vix_label=judged.vix_label,
                action_title=judged.action_title,
                action_lines=judged.action_lines,
                raw_payload=raw,
                error="",
            )

            if q_ns and _is_valid_number(q_ns.close) and q_ns.close > 0:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Saved snapshot OK (nikkei_spot source={nikkei_spot_source}, close={q_ns.close})"
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "Saved snapshot OK (nikkei_spot unavailable; AI prediction will wait for spot data)"
                    )
                )

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