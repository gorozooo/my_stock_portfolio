# aiapp/services/home_news_trends.py
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone as _tz
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from django.core.cache import cache
from django.utils import timezone

from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET


logger = logging.getLogger(__name__)

CACHE_KEY = "aiapp:home:news_trends:v1"
CACHE_TTL_SEC = 300  # 5分


# =========================
# Sources (RSS)
# =========================
NEWS_RSS_SOURCES: Tuple[Tuple[str, str], ...] = (
    ("NHK 経済", "https://www.nhk.or.jp/rss/news/cat5.xml"),
    ("東洋経済", "https://toyokeizai.net/list/feed/header"),
    ("livedoor 経済", "http://news.livedoor.com/topics/rss/eco.xml"),
)

TRENDS_RSS_URL = "https://trends.google.co.jp/trending/rss?geo=JP"


# =========================
# Parsing helpers
# =========================
def _now_iso() -> str:
    return timezone.now().isoformat()


def _norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _safe_host(u: str) -> str:
    try:
        return urlparse(u).netloc or ""
    except Exception:
        return ""


def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()

    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        return dt.astimezone(_tz.utc)
    except Exception:
        pass

    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        return dt.astimezone(_tz.utc)
    except Exception:
        return None


def _xml_text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return _norm_space("".join(el.itertext()))


@dataclass
class FeedItem:
    source: str
    title: str
    link: str
    published_at: Optional[datetime]
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "title": self.title,
            "link": self.link,
            "host": _safe_host(self.link),
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "summary": self.summary,
        }


# =========================
# Fetch / Parse
# =========================
def _fetch_xml_bytes(url: str, timeout_sec: int = 6) -> Optional[bytes]:
    try:
        r = requests.get(
            url,
            timeout=timeout_sec,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; MyStockPortfolioBot/1.0)"
            },
        )
        if r.status_code != 200:
            return None
        return r.content
    except Exception:
        return None


def _parse_rss_items(xml_bytes: bytes, source_name: str, limit: int = 12) -> List[FeedItem]:
    out: List[FeedItem] = []
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return out

    if root.tag.endswith("feed"):  # Atom
        for entry in root.findall(".//{*}entry"):
            title = _xml_text(entry.find("{*}title"))
            link = ""
            for ln in entry.findall("{*}link"):
                href = ln.attrib.get("href")
                if href:
                    link = href
                    break
            pub = _xml_text(entry.find("{*}updated")) or _xml_text(entry.find("{*}published"))
            published_at = _parse_dt(pub)
            summary = _xml_text(entry.find("{*}summary")) or _xml_text(entry.find("{*}content"))
            if title and link:
                out.append(FeedItem(source=source_name, title=title, link=link, published_at=published_at, summary=summary))
            if len(out) >= limit:
                break
        return out

    for item in root.findall(".//channel/item"):
        title = _xml_text(item.find("title"))
        link = _xml_text(item.find("link"))
        pub = _xml_text(item.find("pubDate")) or _xml_text(item.find("{*}date"))
        published_at = _parse_dt(pub)
        desc = _xml_text(item.find("description"))
        if title and link:
            out.append(FeedItem(source=source_name, title=title, link=link, published_at=published_at, summary=desc))
        if len(out) >= limit:
            break

    return out


def _dedupe(items: List[FeedItem]) -> List[FeedItem]:
    seen = set()
    out = []
    for it in items:
        if it.link and it.link not in seen:
            seen.add(it.link)
            out.append(it)
    return out


def _sort_recent(items: List[FeedItem]) -> List[FeedItem]:
    return sorted(items, key=lambda x: x.published_at or datetime(1970, 1, 1, tzinfo=_tz.utc), reverse=True)


# =========================
# Sector guess
# =========================
SECTOR_RULES: Tuple[Tuple[str, str], ...] = (
    (r"(半導体|AI|データセンター|GPU|NVIDIA|TSMC|ASML)", "半導体"),
    (r"(銀行|金利|利上げ|利下げ|国債|財務省|日銀|FRB)", "金融"),
    (r"(地政学|中東|ウクライナ|台湾|制裁|関税)", "地政学"),
    (r"(決算|上方修正|下方修正|増配|自社株買い)", "決算/イベント"),
)


def guess_sector(title: str) -> str:
    for pat, sec in SECTOR_RULES:
        if re.search(pat, title or "", flags=re.IGNORECASE):
            return sec
    return "その他"


def build_sector_ranking(items: List[FeedItem], top_n: int = 6) -> List[Dict[str, Any]]:
    freq: Dict[str, int] = {}
    for it in items:
        s = guess_sector(it.title)
        freq[s] = freq.get(s, 0) + 1
    return [{"sector": k, "count": v} for k, v in sorted(freq.items(), key=lambda x: x[1], reverse=True)[:top_n]]


# =========================
# Macro text (★追加)
# =========================
def build_macro_text(sectors: List[Dict[str, Any]]) -> str:
    if not sectors:
        return "本日は様子見ムード。材料出待ち。"

    names = [s["sector"] for s in sectors[:3]]
    txt = "・".join(names)

    if "地政学" in names:
        return f"地政学リスク意識。{txt}が相場の主語。"
    if "金融" in names:
        return f"金利・金融系が主役。{txt}に集中。"
    if "半導体" in names:
        return f"半導体中心の展開。{txt}を監視。"
    if "決算/イベント" in names:
        return f"イベント多め。{txt}は値動き注意。"

    return f"本日の注目：{txt}"


# =========================
# Main snapshot builder
# =========================
def _collect_news(max_total: int = 18) -> List[FeedItem]:
    items: List[FeedItem] = []
    for name, url in NEWS_RSS_SOURCES:
        xmlb = _fetch_xml_bytes(url)
        if xmlb:
            items.extend(_parse_rss_items(xmlb, name))
    return _sort_recent(_dedupe(items))[:max_total]


def _collect_trends(max_total: int = 10) -> List[FeedItem]:
    xmlb = _fetch_xml_bytes(TRENDS_RSS_URL)
    if not xmlb:
        return []
    return _sort_recent(_dedupe(_parse_rss_items(xmlb, "Google Trends (JP)", max_total)))[:max_total]


def _build_snapshot_no_cache() -> Dict[str, Any]:
    news_items = _collect_news()
    trends_items = _collect_trends()

    sectors = build_sector_ranking(news_items, top_n=6)
    macro_text = build_macro_text(sectors)

    return {
        "status": "ok",
        "as_of": _now_iso(),
        "ttl_sec": CACHE_TTL_SEC,
        "macro_text": macro_text,  # ★ 追加
        "items": [it.to_dict() for it in news_items],
        "trends": [it.to_dict() for it in trends_items],
        "sectors": sectors,
    }


def get_news_trends_snapshot(force_refresh: bool = False) -> Dict[str, Any]:
    if not force_refresh:
        cached = cache.get(CACHE_KEY)
        if isinstance(cached, dict):
            return cached

    snap = _build_snapshot_no_cache()
    cache.set(CACHE_KEY, snap, CACHE_TTL_SEC)
    return snap