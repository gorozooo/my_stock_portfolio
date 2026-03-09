"""
[FILE] shihyo_build_market_bias_open_1000.py
[PATH] <project_root>/shihyo/management/commands/shihyo_build_market_bias_open_1000.py

このファイルは何？
- ShihyoIntradayPrice(mode=open_1000) を集計して、
  ShihyoMarketBiasSnapshot(mode=open_1000) を保存するコマンドです。
- 朝7:00予報ではなく、10:00時点の実績ベースで
  「市場の偏り」を作ります。

今回の改善ポイント:
- open_1000 側の業種名を 33業種寄りにそろえる
- 17業種寄りの表記（例: エネルギー資源）を 33業種側へ寄せる
- ETF/ETN は除外
- 強い業種 / 弱い業種 / 値上がり上位の偏り / 値下がり上位の偏り を作る
- 朝予報(preopen) があれば比較材料として raw_detail に入れる

使い方:
  python manage.py shihyo_build_market_bias_open_1000
  python manage.py shihyo_build_market_bias_open_1000 --date 2026-03-09
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from statistics import mean
from typing import Optional

from django.core.management.base import BaseCommand, CommandParser

from shihyo.models import ShihyoIntradayPrice, ShihyoMarketBiasSnapshot


MIN_SECTOR_COUNT = 2

THEME_PRIORITY = [
    "金利関連",
    "商社・景気敏感",
    "資源・市況",
    "ハイテク",
    "内需",
    "ディフェンシブ",
    "グロース",
    "インフラ関連",
    "素材",
    "その他",
]


def _parse_target_date(s: str) -> Optional[str]:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date().isoformat()


def _normalize_sector_name(sector_name: str | None) -> str:
    s = (sector_name or "").strip()
    if not s:
        return ""

    # 17業種 / 表記ゆれ / 短縮名 を 33業種寄りへ寄せる
    alias_map = {
        "食品": "食料品",
        "食料": "食料品",
        "エネルギー資源": "石油・石炭製品",
        "建設・資材": "建設業",
        "建設": "建設業",
        "素材・化学": "化学",
        "素材": "化学",
        "医薬": "医薬品",
        "情報通信": "情報・通信業",
        "情報・通信": "情報・通信業",
        "情報通信・サービスその他": "情報・通信業",
        "電力・ガス": "電気・ガス業",
        "電力ガス": "電気・ガス業",
        "電気ガス": "電気・ガス業",
        "電気・ガス": "電気・ガス業",
        "自動車・輸送機": "輸送用機器",
        "電機・精密": "電気機器",
        "商社・卸売": "卸売業",
        "銀行・金融": "銀行業",
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


def _is_excluded_sector(sector_name: str | None) -> bool:
    s = _normalize_sector_name(sector_name)

    if not s:
        return True

    excluded_keywords = [
        "ETF/ETN",
        "ETN",
        "ETF",
        "REIT",
        "投信",
        "指数",
    ]
    return any(k in s for k in excluded_keywords)


def _sector_to_theme(sector_name: str | None) -> str:
    s = _normalize_sector_name(sector_name)

    if not s:
        return "その他"

    mapping = [
        (["銀行業", "保険業", "証券、商品先物取引業", "その他金融業"], "金利関連"),
        (["卸売業", "鉄鋼", "非鉄金属", "輸送用機器", "機械"], "商社・景気敏感"),
        (["海運業", "鉱業", "石油・石炭製品"], "資源・市況"),
        (["電気機器", "精密機器", "情報・通信業"], "ハイテク"),
        (["小売業", "食料品", "不動産業", "陸運業", "空運業"], "内需"),
        (["医薬品", "電気・ガス業", "水産・農林業", "パルプ・紙"], "ディフェンシブ"),
        (["サービス業"], "グロース"),
        (["建設業", "ガラス・土石製品", "倉庫・運輸関連業"], "インフラ関連"),
        (["化学", "ゴム製品", "金属製品"], "素材"),
    ]

    for keys, theme in mapping:
        if s in keys:
            return theme

    return "その他"


def _pick_latest_date_or_raise(target_date: Optional[str]) -> str:
    if target_date:
        exists = ShihyoIntradayPrice.objects.filter(
            date=target_date,
            mode=ShihyoIntradayPrice.MODE_OPEN_1000,
        ).exists()
        if not exists:
            raise ValueError(
                f"ShihyoIntradayPrice(open_1000) に対象日データがありません: {target_date}"
            )
        return target_date

    latest = (
        ShihyoIntradayPrice.objects
        .filter(mode=ShihyoIntradayPrice.MODE_OPEN_1000)
        .order_by("-date")
        .values_list("date", flat=True)
        .first()
    )
    if not latest:
        raise ValueError(
            "ShihyoIntradayPrice(open_1000) にデータがありません。"
            "先に shihyo_load_intraday_prices を実行してください。"
        )
    return latest.isoformat()


def _build_sector_scores(rows: list[ShihyoIntradayPrice]) -> tuple[list[dict], dict]:
    bucket: dict[str, list[ShihyoIntradayPrice]] = defaultdict(list)
    excluded_rows = 0

    for row in rows:
        sector = _normalize_sector_name(row.sector_name)
        if _is_excluded_sector(sector):
            excluded_rows += 1
            continue
        if row.change_pct is None:
            continue
        bucket[sector].append(row)

    results: list[dict] = []
    skipped_low_count: list[str] = []

    for sector, items in bucket.items():
        total_count = len(items)
        if total_count < MIN_SECTOR_COUNT:
            skipped_low_count.append(sector)
            continue

        valid_pct = [x.change_pct for x in items if x.change_pct is not None]
        valid_turnover = [x.turnover for x in items if x.turnover is not None]
        up_count = sum(1 for x in items if (x.change_pct or 0) > 0)
        down_count = sum(1 for x in items if (x.change_pct or 0) < 0)

        avg_pct = mean(valid_pct) if valid_pct else 0.0
        up_ratio = (up_count / total_count) if total_count > 0 else 0.0
        turnover_score = mean(valid_turnover) if valid_turnover else 0.0

        score = (avg_pct * 0.7) + ((up_ratio - 0.5) * 8.0)

        results.append(
            {
                "sector": sector,
                "score": float(score),
                "avg_pct": float(avg_pct),
                "up_ratio": float(up_ratio),
                "up_count": up_count,
                "down_count": down_count,
                "count": total_count,
                "turnover_avg": float(turnover_score),
            }
        )

    results.sort(key=lambda x: x["score"], reverse=True)

    debug = {
        "excluded_rows": excluded_rows,
        "skipped_low_count_sectors": skipped_low_count,
        "min_sector_count": MIN_SECTOR_COUNT,
    }
    return results, debug


def _build_theme_bias(rows: list[ShihyoIntradayPrice], top_n: int = 20) -> tuple[list[str], list[str], dict]:
    valid_rows = [
        x for x in rows
        if x.change_pct is not None and not _is_excluded_sector(x.sector_name)
    ]

    gainers = sorted(valid_rows, key=lambda x: x.change_pct, reverse=True)[:top_n]
    losers = sorted(valid_rows, key=lambda x: x.change_pct)[:top_n]

    gain_counter = Counter(
        _sector_to_theme(x.sector_name)
        for x in gainers
        if _sector_to_theme(x.sector_name) != "その他"
    )
    lose_counter = Counter(
        _sector_to_theme(x.sector_name)
        for x in losers
        if _sector_to_theme(x.sector_name) != "その他"
    )

    def _ordered_top(counter: Counter) -> list[str]:
        if not counter:
            return []

        items = sorted(
            counter.items(),
            key=lambda kv: (-kv[1], THEME_PRIORITY.index(kv[0]) if kv[0] in THEME_PRIORITY else 999),
        )
        return [name for name, _ in items[:3]]

    hot_themes = _ordered_top(gain_counter)
    cold_themes = _ordered_top(lose_counter)

    raw = {
        "gainers_top_codes": [x.code for x in gainers[:10]],
        "gainers_top_names": [x.name for x in gainers[:10]],
        "losers_top_codes": [x.code for x in losers[:10]],
        "losers_top_names": [x.name for x in losers[:10]],
        "gainer_theme_count": dict(gain_counter),
        "loser_theme_count": dict(lose_counter),
    }
    return hot_themes, cold_themes, raw


def _pick_top_unique(items: list[str], limit: int = 3) -> list[str]:
    result: list[str] = []
    seen = set()
    for item in items:
        x = str(item).strip()
        if not x or x in seen:
            continue
        seen.add(x)
        result.append(x)
        if len(result) >= limit:
            break
    return result


def _build_summary(
    strong_sectors: list[str],
    weak_sectors: list[str],
    hot_themes: list[str],
    cold_themes: list[str],
    preopen_snapshot: ShihyoMarketBiasSnapshot | None,
) -> tuple[str, str, str]:
    base_text_parts: list[str] = []

    if strong_sectors and weak_sectors:
        base_text_parts.append(
            f"10:00時点で強い業種は {' / '.join(strong_sectors[:2])}、"
            f"弱い業種は {' / '.join(weak_sectors[:2])} です。"
        )
    elif strong_sectors:
        base_text_parts.append(f"10:00時点で強い業種は {' / '.join(strong_sectors[:2])} です。")
    elif weak_sectors:
        base_text_parts.append(f"10:00時点で弱い業種は {' / '.join(weak_sectors[:2])} です。")
    else:
        base_text_parts.append("10:00時点の市場の偏りを集計中です。")

    if hot_themes:
        base_text_parts.append(f"値上がり上位は {' / '.join(hot_themes[:2])} に偏っています。")
    if cold_themes:
        base_text_parts.append(f"値下がり上位は {' / '.join(cold_themes[:2])} に偏っています。")

    summary_title = "まちまち"
    tone = ShihyoMarketBiasSnapshot.TONE_NEUTRAL

    if strong_sectors and hot_themes and not weak_sectors:
        summary_title = "やや強い"
        tone = ShihyoMarketBiasSnapshot.TONE_RISK_ON
    elif weak_sectors and cold_themes and not strong_sectors:
        summary_title = "やや弱い"
        tone = ShihyoMarketBiasSnapshot.TONE_RISK_OFF

    if preopen_snapshot:
        preopen_strong = [_normalize_sector_name(x) for x in list(preopen_snapshot.strong_sectors or [])]
        preopen_weak = [_normalize_sector_name(x) for x in list(preopen_snapshot.weak_sectors or [])]

        if strong_sectors and preopen_strong and strong_sectors[0] == preopen_strong[0]:
            base_text_parts.append("朝予報の主力業種はおおむね一致しています。")
        elif weak_sectors and preopen_weak and weak_sectors[0] == preopen_weak[0]:
            base_text_parts.append("朝の警戒方向はおおむね一致しています。")
        else:
            base_text_parts.append("朝予報とは少し違う偏り方です。")

    summary_text = " ".join(base_text_parts)
    return summary_title, summary_text, tone


class Command(BaseCommand):
    help = "ShihyoIntradayPrice(open_1000) を集計して、ShihyoMarketBiasSnapshot(open_1000) を保存します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--date",
            type=str,
            default="",
            help="対象日 (YYYY-MM-DD)。未指定なら ShihyoIntradayPrice(open_1000) の最新日",
        )

    def handle(self, *args, **options):
        target_date = _parse_target_date(options.get("date") or "")
        actual_date = _pick_latest_date_or_raise(target_date)

        qs = (
            ShihyoIntradayPrice.objects
            .filter(date=actual_date, mode=ShihyoIntradayPrice.MODE_OPEN_1000)
            .order_by("code")
        )
        rows = list(qs)

        if not rows:
            self.stdout.write(
                self.style.ERROR(f"対象日の場中価格データがありません: {actual_date}")
            )
            return

        sector_scores, sector_debug = _build_sector_scores(rows)

        strong_sectors = _pick_top_unique([x["sector"] for x in sector_scores], limit=3)
        weak_sectors = _pick_top_unique(
            [x["sector"] for x in sorted(sector_scores, key=lambda x: x["score"])],
            limit=3,
        )

        hot_themes, cold_themes, theme_raw = _build_theme_bias(rows, top_n=20)

        preopen_snapshot = (
            ShihyoMarketBiasSnapshot.objects
            .filter(date=actual_date, mode=ShihyoMarketBiasSnapshot.MODE_PREOPEN)
            .order_by("-updated_at")
            .first()
        )

        summary_title, summary_text, tone = _build_summary(
            strong_sectors=strong_sectors,
            weak_sectors=weak_sectors,
            hot_themes=hot_themes,
            cold_themes=cold_themes,
            preopen_snapshot=preopen_snapshot,
        )

        raw_detail = {
            "date": actual_date,
            "mode": ShihyoMarketBiasSnapshot.MODE_OPEN_1000,
            "sector_scores_top10": sector_scores[:10],
            "sector_scores_bottom10": sorted(sector_scores, key=lambda x: x["score"])[:10],
            "sector_debug": sector_debug,
            "theme_raw": theme_raw,
            "preopen_exists": bool(preopen_snapshot),
            "preopen_summary_title": preopen_snapshot.summary_title if preopen_snapshot else "",
            "source": "shihyo_intraday_price_open_1000",
        }

        obj, created = ShihyoMarketBiasSnapshot.objects.update_or_create(
            date=actual_date,
            mode=ShihyoMarketBiasSnapshot.MODE_OPEN_1000,
            defaults={
                "summary_title": summary_title,
                "summary_text": summary_text,
                "tone": tone,
                "strong_sectors": strong_sectors,
                "weak_sectors": weak_sectors,
                "hot_themes": hot_themes,
                "cold_themes": cold_themes,
                "raw_detail": raw_detail,
            },
        )

        action = "created" if created else "updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"[shihyo_build_market_bias_open_1000] {action} "
                f"date={actual_date} mode=open_1000 "
                f"strong={strong_sectors} weak={weak_sectors} "
                f"hot={hot_themes} cold={cold_themes}"
            )
        )