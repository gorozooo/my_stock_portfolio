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
# なるべく “公式RSS” を優先。落ちてもHomeが落ちない設計。
NEWS_RSS_SOURCES: Tuple[Tuple[str, str], ...] = (
    # NHK 経済
    ("NHK 経済", "https://www.nhk.or.jp/rss/news/cat5.xml"),
    # 東洋経済オンライン（ヘッダーRSS）
    ("東洋経済", "https://toyokeizai.net/list/feed/header"),
    # livedoor（IT・経済）
    ("livedoor 経済", "http://news.livedoor.com/topics/rss/eco.xml"),
)

# Google Trends（日本）※URLが変わることがあるのでここだけ単独にしておく
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
    """
    RSSの pubDate / published / updated にありがちな形式を雑に吸収。
    """
    if not s:
        return None
    s = s.strip()
    # RFC2822
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        return dt.astimezone(_tz.utc)
    except Exception:
        pass

    # ISO8601 っぽい
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


def _fetch_xml(url: str, timeout_sec: int = 6) -> Optional[str]:
    try:
        r = requests.get(
            url,
            timeout=timeout_sec,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; MyStockPortfolioBot/1.0; +https://example.invalid)"
            },
        )
        if r.status_code != 200:
            return None
        r.encoding = r.encoding or "utf-8"
        return r.text
    except Exception:
        return None


def _parse_rss_items(xml_text: str, source_name: str, limit: int = 12) -> List[FeedItem]:
    """
    RSS2.0 / Atom を “それっぽく” パース
    """
    out: List[FeedItem] = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return out

    # Atom: <feed><entry>...
    if root.tag.endswith("feed"):
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

    # RSS2.0: <rss><channel><item>...
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
        k = (it.link or "").strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _sort_recent(items: List[FeedItem]) -> List[FeedItem]:
    def key(it: FeedItem):
        return it.published_at or datetime(1970, 1, 1, tzinfo=_tz.utc)
    return sorted(items, key=key, reverse=True)


# =========================
# Sector guess (lightweight)
# =========================
# ここは “雑に当てる” 用（後で33業種コードへ寄せる）
SECTOR_RULES: Tuple[Tuple[str, str], ...] = (
    (r"(半導体|AI|データセンター|GPU|NVIDIA|TSMC|ASML)", "電気機器/半導体"),
    (r"(銀行|金利|利上げ|利下げ|国債|財務省|日銀|FRB)", "銀行/金融"),
    (r"(保険)", "保険"),
    (r"(商社|卸)", "商社/卸売"),
    (r"(原油|OPEC|天然ガス|LNG|エネルギー|電力|ガス)", "エネルギー/電力・ガス"),
    (r"(自動車|EV|トヨタ|ホンダ|日産|BYD|テスラ)", "輸送用機器/自動車"),
    (r"(海運|航空|物流|港湾|運賃|コンテナ)", "運輸/物流"),
    (r"(不動産|REIT|住宅|マンション|地価)", "不動産"),
    (r"(医薬|製薬|治験|ワクチン|医療)", "医薬品/ヘルスケア"),
    (r"(食品|外食|小売|コンビニ)", "食品/小売"),
    (r"(通信|5G|NTT|KDDI|ソフトバンク)", "情報通信/通信"),
    (r"(地政学|中東|ウクライナ|台湾|制裁|関税)", "地政学/マクロ"),
    (r"(決算|上方修正|下方修正|増配|自社株買い)", "企業業績/イベント"),
)


def guess_sector(title: str) -> str:
    t = title or ""
    for pat, sec in SECTOR_RULES:
        try:
            if re.search(pat, t, flags=re.IGNORECASE):
                return sec
        except Exception:
            continue
    return "その他"


def build_sector_ranking(items: List[FeedItem], top_n: int = 6) -> List[Dict[str, Any]]:
    freq: Dict[str, int] = {}
    for it in items:
        s = guess_sector(it.title)
        freq[s] = freq.get(s, 0) + 1

    ranked = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:top_n]
    return [{"sector": k, "count": v} for k, v in ranked]


# =========================
# Main snapshot builder
# =========================
def _collect_news(max_total: int = 18) -> List[FeedItem]:
    all_items: List[FeedItem] = []
    for name, url in NEWS_RSS_SOURCES:
        xml = _fetch_xml(url)
        if not xml:
            continue
        items = _parse_rss_items(xml, source_name=name, limit=12)
        all_items.extend(items)

    all_items = _dedupe(all_items)
    all_items = _sort_recent(all_items)
    return all_items[:max_total]


def _collect_trends(max_total: int = 10) -> List[FeedItem]:
    xml = _fetch_xml(TRENDS_RSS_URL)
    if not xml:
        return []
    items = _parse_rss_items(xml, source_name="Google Trends (JP)", limit=max_total)
    # Trendsは published が無い場合があるので、並びはそのままでもOKだが一応 recent sort
    items = _dedupe(items)
    items = _sort_recent(items)
    return items[:max_total]


def _build_snapshot_no_cache() -> Dict[str, Any]:
    news_items = _collect_news()
    trends_items = _collect_trends()

    # sectors: newsから推定（トレンドも混ぜたいなら後で）
    sectors = build_sector_ranking(news_items, top_n=6)

    return {
        "status": "ok",
        "as_of": _now_iso(),
        "ttl_sec": CACHE_TTL_SEC,
        "items": [it.to_dict() for it in news_items],       # ニュース
        "trends": [it.to_dict() for it in trends_items],    # ネット/X相当の“話題”はここで代替
        "sectors": sectors,                                 # 注目セクター（ざっくり）
        "sources": {
            "news": [{"name": n, "url": u} for (n, u) in NEWS_RSS_SOURCES],
            "trends": [{"name": "Google Trends (JP)", "url": TRENDS_RSS_URL}],
        },
    }


def get_news_trends_snapshot(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Home用：NEWS & TRENDS（5分TTL）
    - force_refresh=True ならキャッシュ無視
    """
    if not force_refresh:
        cached = cache.get(CACHE_KEY)
        if isinstance(cached, dict):
            return cached

    snap = _build_snapshot_no_cache()
    cache.set(CACHE_KEY, snap, CACHE_TTL_SEC)
    return snap