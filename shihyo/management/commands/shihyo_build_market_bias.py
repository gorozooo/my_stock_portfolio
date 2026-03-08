"""
[FILE] shihyo_build_market_bias.py
[PATH] <project_root>/shihyo/management/commands/shihyo_build_market_bias.py

このファイルは何？
- ShihyoDailyPrice を集計して、
  ShihyoMarketBiasSnapshot(mode=close) を保存するコマンドです。
- スクレイピングに依存せず、
  自前の日次価格データから「市場の偏り」を作ります。

このコマンドで作るもの:
- 強い業種 TOP3
- 弱い業種 TOP3
- 値上がり上位の偏り TOP3
- 値下がり上位の偏り TOP3
- 初心者向けの要約文
- tone (risk_on / neutral / risk_off)

使い方の例:
- 直近営業日ベースで作成
  python manage.py shihyo_build_market_bias

- 日付指定
  python manage.py shihyo_build_market_bias --date 2026-03-09
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from statistics import mean
from typing import Optional

from django.core.management.base import BaseCommand, CommandParser

from shihyo.models import ShihyoDailyPrice, ShihyoMarketBiasSnapshot


THEME_PRIORITY = [
    "金利関連",
    "商社・景気敏感",
    "資源・市況",
    "ハイテク",
    "内需",
    "ディフェンシブ",
    "グロース",
    "インフラ関連",
    "その他",
]


def _parse_target_date(s: str) -> Optional[str]:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date().isoformat()


def _sector_to_theme(sector_name: str | None) -> str:
    s = (sector_name or "").strip()

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
    ]

    for keys, theme in mapping:
        if s in keys:
            return theme

    return "その他"


def _pick_latest_date_or_raise(target_date: Optional[str]) -> str:
    if target_date:
        exists = ShihyoDailyPrice.objects.filter(date=target_date).exists()
        if not exists:
            raise ValueError(f"ShihyoDailyPrice に対象日データがありません: {target_date}")
        return target_date

    latest = ShihyoDailyPrice.objects.order_by("-date").values_list("date", flat=True).first()
    if not latest:
        raise ValueError("ShihyoDailyPrice にデータがありません。先に shihyo_load_daily_prices を実行してください。")
    return latest.isoformat()


def _build_sector_scores(rows: list[ShihyoDailyPrice]) -> list[dict]:
    bucket: dict[str, list[ShihyoDailyPrice]] = defaultdict(list)

    for row in rows:
        sector = (row.sector_name or "").strip()
        if not sector:
            continue
        bucket[sector].append(row)

    results: list[dict] = []

    for sector, items in bucket.items():
        valid_pct = [x.change_pct for x in items if x.change_pct is not None]
        valid_turnover = [x.turnover for x in items if x.turnover is not None]
        up_count = sum(1 for x in items if (x.change_pct or 0) > 0)
        down_count = sum(1 for x in items if (x.change_pct or 0) < 0)
        total_count = len(items)

        avg_pct = mean(valid_pct) if valid_pct else 0.0
        up_ratio = (up_count / total_count) if total_count > 0 else 0.0
        turnover_score = 0.0

        if valid_turnover:
            turnover_score = mean(valid_turnover)

        # 初期版のシンプルスコア
        # 平均騰落率を主軸に、上昇比率を補正で足す
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
    return results


def _build_theme_bias(rows: list[ShihyoDailyPrice], top_n: int = 20) -> tuple[list[str], list[str], dict]:
    gainers = sorted(
        [x for x in rows if x.change_pct is not None],
        key=lambda x: x.change_pct,
        reverse=True,
    )[:top_n]

    losers = sorted(
        [x for x in rows if x.change_pct is not None],
        key=lambda x: x.change_pct,
    )[:top_n]

    gain_counter = Counter(_sector_to_theme(x.sector_name) for x in gainers if x.sector_name)
    lose_counter = Counter(_sector_to_theme(x.sector_name) for x in losers if x.sector_name)

    def _ordered_top(counter: Counter) -> list[str]:
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


def _build_summary(strong_sectors: list[str], weak_sectors: list[str], hot_themes: list[str], cold_themes: list[str]) -> tuple[str, str, str]:
    if strong_sectors and weak_sectors:
        summary_text = f"強い業種は {' / '.join(strong_sectors[:2])}、弱い業種は {' / '.join(weak_sectors[:2])} です。"
    elif strong_sectors:
        summary_text = f"強い業種は {' / '.join(strong_sectors[:2])} です。"
    elif weak_sectors:
        summary_text = f"弱い業種は {' / '.join(weak_sectors[:2])} です。"
    else:
        summary_text = "市場の偏りを集計中です。"

    if hot_themes:
        summary_text += f" 値上がり上位は {' / '.join(hot_themes[:2])} に偏っています。"
    if cold_themes:
        summary_text += f" 値下がり上位は {' / '.join(cold_themes[:2])} に偏っています。"

    positive = len(strong_sectors) + len(hot_themes)
    negative = len(weak_sectors) + len(cold_themes)

    if strong_sectors and hot_themes and not weak_sectors:
        summary_title = "やや強い"
        tone = ShihyoMarketBiasSnapshot.TONE_RISK_ON
    elif weak_sectors and cold_themes and not strong_sectors:
        summary_title = "やや弱い"
        tone = ShihyoMarketBiasSnapshot.TONE_RISK_OFF
    else:
        summary_title = "まちまち"
        tone = ShihyoMarketBiasSnapshot.TONE_NEUTRAL

    return summary_title, summary_text, tone


class Command(BaseCommand):
    help = "ShihyoDailyPrice を集計して、ShihyoMarketBiasSnapshot(mode=close) を保存します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--date",
            type=str,
            default="",
            help="対象日 (YYYY-MM-DD)。未指定なら ShihyoDailyPrice の最新日",
        )

    def handle(self, *args, **options):
        target_date = _parse_target_date(options.get("date") or "")
        actual_date = _pick_latest_date_or_raise(target_date)

        qs = ShihyoDailyPrice.objects.filter(date=actual_date).order_by("code")
        rows = list(qs)

        if not rows:
            self.stdout.write(self.style.ERROR(f"対象日の価格データがありません: {actual_date}"))
            return

        sector_scores = _build_sector_scores(rows)
        strong_sectors = [x["sector"] for x in sector_scores[:3]]
        weak_sectors = [x["sector"] for x in sorted(sector_scores, key=lambda x: x["score"])[:3]]

        hot_themes, cold_themes, theme_raw = _build_theme_bias(rows, top_n=20)
        summary_title, summary_text, tone = _build_summary(
            strong_sectors=strong_sectors,
            weak_sectors=weak_sectors,
            hot_themes=hot_themes,
            cold_themes=cold_themes,
        )

        raw_detail = {
            "date": actual_date,
            "mode": ShihyoMarketBiasSnapshot.MODE_CLOSE,
            "sector_scores_top10": sector_scores[:10],
            "sector_scores_bottom10": sorted(sector_scores, key=lambda x: x["score"])[:10],
            "theme_raw": theme_raw,
            "source": "shihyo_daily_price",
        }

        obj, created = ShihyoMarketBiasSnapshot.objects.update_or_create(
            date=actual_date,
            mode=ShihyoMarketBiasSnapshot.MODE_CLOSE,
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
                f"[shihyo_build_market_bias] {action} "
                f"date={actual_date} mode=close "
                f"strong={strong_sectors} weak={weak_sectors} "
                f"hot={hot_themes} cold={cold_themes}"
            )
        )