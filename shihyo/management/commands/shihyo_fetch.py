"""
[FILE] shihyo_fetch.py
[PATH] <project_root>/shihyo/management/commands/shihyo_fetch.py

このファイルは何？
- 指標（先物・ドル円・VIX・日経平均現物）を取得してDBへ保存するコマンドです。
- 日経平均の現物値は raw_payload["nikkei_spot"] に保存します。
- さらに raw_payload["market_bias"] に、
  1) 強い業種TOP3
  2) 弱い業種TOP3
  3) 値上がり上位の偏り
  4) 値下がり上位の偏り
  を保存します。

今回の market_bias 取得方針：
- 業種強弱は株探の TOPIX ページにある「東証【業種別】騰落ランキング」から取得
- 値上がり上位 / 値下がり上位は株探英語版のランキングページから取得
- 値下がり上位 / 値上がり上位は銘柄名ベースで保存し、初心者にも読みやすくする
- 取得失敗時は market_bias を unavailable で保存する
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup
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

    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )

    def _http_get(self, url: str, params: Optional[dict] = None) -> str:
        r = requests.get(
            url,
            params=params,
            timeout=12,
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                "Referer": "https://kabutan.jp/",
            },
        )
        r.raise_for_status()
        return r.text

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

    def _extract_sector_rows(self, section_text: str) -> list[dict]:
        rows: list[dict] = []
        for raw_line in section_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if "業種名" in line or "騰落率" in line or "銘柄数" in line:
                continue

            # 例: 銀行業 +11.79 +2.02% 596.68 69
            m = re.match(
                r"^(?P<name>.+?)\s+(?P<diff>[+\-]?\d+(?:\.\d+)?)\s+(?P<pct>[+\-]?\d+(?:\.\d+)?)%\s+",
                line,
            )
            if m:
                name = m.group("name").strip()
                pct = float(m.group("pct"))
                rows.append({"name": name, "pct": pct})
        return rows

    def _fetch_sector_bias_from_kabutan(self) -> dict:
        """
        株探 TOPIX ページの「東証【業種別】騰落ランキング」から
        上位/下位業種を取得する。
        """
        try:
            html = self._http_get("https://kabutan.jp/stock/chart", params={"code": "0010"})
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text("\n", strip=True)

            if "東証【業種別】騰落ランキング" not in text:
                return {"available": False, "strong": [], "weak": []}

            after_title = text.split("東証【業種別】騰落ランキング", 1)[1]
            upper_text = after_title.split("騰落率上位10", 1)
            if len(upper_text) < 2:
                return {"available": False, "strong": [], "weak": []}

            remainder = upper_text[1]
            upper_block, lower_rest = remainder.split("騰落率下位10", 1)
            lower_block = lower_rest.split("こちらは株探プレミアム", 1)[0]

            strong_rows = self._extract_sector_rows(upper_block)
            weak_rows = self._extract_sector_rows(lower_block)

            return {
                "available": bool(strong_rows or weak_rows),
                "strong": strong_rows[:3],
                "weak": weak_rows[:3],
            }
        except Exception:
            return {"available": False, "strong": [], "weak": []}

    def _extract_rank_names_from_kabutan_en(self, text: str) -> list[str]:
        """
        Kabutan英語版ランキングページのテキストから
        銘柄名を抽出する。
        ルール:
          ある行の次行に 'TSE' を含む場合、その行を銘柄名とみなす。
        """
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        names: list[str] = []

        for i in range(len(lines) - 1):
            current_line = lines[i]
            next_line = lines[i + 1]

            if "TSE" in next_line and current_line not in names:
                if current_line in {
                    "All",
                    "Stock",
                    "Price",
                    "Market Cap",
                    "Change",
                    "Volume",
                    "PER",
                    "PBR",
                    "Yield",
                    "Liquidity",
                    "Low",
                    "Mid",
                    "High",
                    "Slightly High",
                }:
                    continue
                names.append(current_line)

        return names

    def _fetch_top_movers_from_kabutan_en(self) -> dict:
        """
        株探英語版のランキングページから
        値上がり上位 / 値下がり上位の銘柄名を取る。
        """
        base = "https://en.kabutan.com/jp/trends"
        result = {
            "available": False,
            "gainers": [],
            "losers": [],
        }

        try:
            gainers_html = self._http_get(f"{base}/price_increase", params={"capitalization": "5", "market": "all", "page": "1"})
            gainers_text = BeautifulSoup(gainers_html, "html.parser").get_text("\n", strip=True)
            gainers = self._extract_rank_names_from_kabutan_en(gainers_text)[:3]

            losers_html = self._http_get(f"{base}/price_decrease", params={"capitalization": "5", "market": "all", "page": "1"})
            losers_text = BeautifulSoup(losers_html, "html.parser").get_text("\n", strip=True)
            losers = self._extract_rank_names_from_kabutan_en(losers_text)[:3]

            result["gainers"] = gainers
            result["losers"] = losers
            result["available"] = bool(gainers or losers)
            return result
        except Exception:
            return result

    def _build_market_bias(self) -> dict:
        """
        業種強弱 + 値上がり/値下がり上位の偏り を作る。
        """
        sector_bias = self._fetch_sector_bias_from_kabutan()
        mover_bias = self._fetch_top_movers_from_kabutan_en()

        strong = [row["name"] for row in sector_bias.get("strong", [])]
        weak = [row["name"] for row in sector_bias.get("weak", [])]
        gainers = mover_bias.get("gainers", [])[:3]
        losers = mover_bias.get("losers", [])[:3]

        available = bool(strong or weak or gainers or losers)

        if not available:
            return {
                "available": False,
                "summary_title": "準備中",
                "summary_text": "日本株市場で、どこに資金が入っているか / どこが売られているか を集計中です。",
                "summary_badge_class": "market-bias-badge market-bias-badge-wait",
                "tone": "neutral",
                "strong_sectors": [],
                "weak_sectors": [],
                "hot_themes": [],
                "cold_themes": [],
            }

        strong_pct_total = sum(row["pct"] for row in sector_bias.get("strong", []))
        weak_pct_total = sum(row["pct"] for row in sector_bias.get("weak", []))

        if strong_pct_total > abs(weak_pct_total) * 1.2:
            summary_title = "やや強い"
            badge_class = "market-bias-badge market-bias-badge-on"
            tone = "risk_on"
        elif abs(weak_pct_total) > max(strong_pct_total, 0.01) * 1.2:
            summary_title = "やや弱い"
            badge_class = "market-bias-badge market-bias-badge-off"
            tone = "risk_off"
        else:
            summary_title = "まちまち"
            badge_class = "market-bias-badge market-bias-badge-wait"
            tone = "neutral"

        parts: list[str] = []
        if strong:
            parts.append(f"強い業種は {' / '.join(strong[:2])}")
        if weak:
            parts.append(f"弱い業種は {' / '.join(weak[:2])}")
        if gainers:
            parts.append(f"上昇上位は {' / '.join(gainers[:2])}")
        if losers:
            parts.append(f"下落上位は {' / '.join(losers[:2])}")

        summary_text = "、".join(parts) if parts else "日本株の偏りを集計中です。"

        return {
            "available": True,
            "summary_title": summary_title,
            "summary_text": summary_text,
            "summary_badge_class": badge_class,
            "tone": tone,
            "strong_sectors": strong[:3],
            "weak_sectors": weak[:3],
            "hot_themes": gainers[:3],
            "cold_themes": losers[:3],
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

            q_ns, ns, nikkei_spot_source = self._fetch_nikkei_spot(client)

            nf = client.calc_change(q_nf.close, q_nf.open)
            f = client.calc_change(q_f.close, q_f.open)
            v = client.calc_change(q_v.close, q_v.open)

            market_bias = self._build_market_bias()

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

            if market_bias.get("available"):
                messages.append("market_bias=available")
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