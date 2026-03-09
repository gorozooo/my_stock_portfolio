"""
[FILE] shihyo_build_market_bias_preopen.py
[PATH] <project_root>/shihyo/management/commands/shihyo_build_market_bias_preopen.py

このファイルは何？
- 引け後版の市場の偏り(close)と、最新の指標スナップショットを使って
  朝7:00用の市場の偏り(preopen)を作るコマンドです。
- 目的は「昨日どうだったか」ではなく、
  「今日の寄り前に、どこへ資金が向かいそうか」を作ることです。

今回の修正ポイント：
- preopen 保存日付を JST 基準に修正
- これで朝7時に走ったとき、前日付ではなく「今日の preopen」として保存される
- preopen 側の業種名も close / open_1000 と同じ 33業種寄りへそろえる
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from shihyo.models import MarketIndicatorSnapshot, ShihyoMarketBiasSnapshot


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    result: list[str] = []
    for item in items:
        x = str(item).strip()
        if x and x not in seen:
            seen.add(x)
            result.append(x)
    return result


def _normalize_sector_name(sector_name: str | None) -> str:
    s = (sector_name or "").strip()
    if not s:
        return ""

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


def _normalize_sector_list(items: list[str]) -> list[str]:
    normalized = [_normalize_sector_name(x) for x in items]
    normalized = [x for x in normalized if x]
    return _dedupe_keep_order(normalized)


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


def _theme_to_sectors(themes: list[str]) -> list[str]:
    mapping = {
        "金利関連": ["銀行業", "保険業", "証券、商品先物取引業"],
        "商社・景気敏感": ["卸売業", "輸送用機器", "機械"],
        "資源・市況": ["海運業", "鉱業", "石油・石炭製品"],
        "ハイテク": ["電気機器", "精密機器", "情報・通信業"],
        "内需": ["小売業", "食料品", "不動産業"],
        "ディフェンシブ": ["医薬品", "電気・ガス業", "水産・農林業"],
        "グロース": ["サービス業", "情報・通信業"],
        "インフラ関連": ["建設業", "ガラス・土石製品", "倉庫・運輸関連業"],
        "素材": ["化学", "ゴム製品", "金属製品"],
        "その他": [],
    }

    result: list[str] = []
    for theme in themes:
        result.extend(mapping.get(theme, []))
    return _normalize_sector_list(result)[:3]


def _build_preopen_bias(close_bias: ShihyoMarketBiasSnapshot, latest: MarketIndicatorSnapshot) -> dict:
    close_strong = _normalize_sector_list([str(x) for x in (close_bias.strong_sectors or [])[:3]])
    close_weak = _normalize_sector_list([str(x) for x in (close_bias.weak_sectors or [])[:3]])
    close_hot = _dedupe_keep_order([str(x) for x in (close_bias.hot_themes or [])[:3]])
    close_cold = _dedupe_keep_order([str(x) for x in (close_bias.cold_themes or [])[:3]])

    nikkei_pct = _safe_float(latest.nikkei_futures_change_pct, 0.0)
    fx_pct = _safe_float(latest.usdjpy_change_pct, 0.0)
    vix_pct = _safe_float(latest.vix_change_pct, 0.0)
    vix_last = _safe_float(latest.vix_last, 0.0)

    bullish_score = 0
    bearish_score = 0

    if nikkei_pct >= 0.7:
        bullish_score += 2
    elif nikkei_pct >= 0.2:
        bullish_score += 1
    elif nikkei_pct <= -0.7:
        bearish_score += 2
    elif nikkei_pct <= -0.2:
        bearish_score += 1

    if fx_pct >= 0.15:
        bullish_score += 1
    elif fx_pct <= -0.15:
        bearish_score += 1

    if vix_pct <= -3.0:
        bullish_score += 1
    elif vix_pct >= 3.0:
        bearish_score += 1

    if vix_last >= 25:
        bearish_score += 2
    elif 0 < vix_last < 18:
        bullish_score += 1

    predicted_strong = list(close_strong)
    predicted_weak = list(close_weak)
    predicted_hot = list(close_hot)
    predicted_cold = list(close_cold)

    if bullish_score >= bearish_score + 2:
        extra_hot = _dedupe_keep_order(
            close_hot + [_sector_to_theme(x) for x in close_strong]
        )[:3]
        predicted_hot = extra_hot
        predicted_cold = _dedupe_keep_order(close_cold)[:3]

        summary_title = "やや強い"
        tone = ShihyoMarketBiasSnapshot.TONE_RISK_ON
        summary_text = (
            f"先物と外部環境が追い風で、"
            f"{' / '.join(predicted_strong[:2]) if predicted_strong else '前日強かった業種'} "
            f"中心の買い継続を想定しています。"
        )

    elif bearish_score >= bullish_score + 2:
        defensive_themes = _dedupe_keep_order(
            close_cold + ["ディフェンシブ", "内需"]
        )[:3]
        predicted_cold = defensive_themes

        weak_from_themes = _theme_to_sectors(defensive_themes)
        if weak_from_themes:
            predicted_weak = weak_from_themes

        summary_title = "やや弱い"
        tone = ShihyoMarketBiasSnapshot.TONE_RISK_OFF
        summary_text = (
            f"先物・VIX・ドル円の組み合わせから朝は慎重寄りです。"
            f"{' / '.join(predicted_weak[:2]) if predicted_weak else '弱い業種'} "
            f"に注意です。"
        )

    else:
        predicted_hot = _dedupe_keep_order(close_hot + [_sector_to_theme(x) for x in close_strong])[:3]
        predicted_cold = _dedupe_keep_order(close_cold + [_sector_to_theme(x) for x in close_weak])[:3]

        summary_title = "まちまち"
        tone = ShihyoMarketBiasSnapshot.TONE_NEUTRAL
        summary_text = (
            f"前日の偏りは残りますが、外部環境はまだ混在しています。"
            f"{' / '.join(predicted_strong[:2]) if predicted_strong else '強い業種'} と "
            f"{' / '.join(predicted_weak[:2]) if predicted_weak else '弱い業種'} の両方を確認したい朝です。"
        )

    predicted_strong = _normalize_sector_list(predicted_strong)[:3]
    predicted_weak = _normalize_sector_list(predicted_weak)[:3]
    predicted_hot = _dedupe_keep_order(predicted_hot)[:3]
    predicted_cold = _dedupe_keep_order(predicted_cold)[:3]

    raw_detail = {
        "base_close_snapshot_date": close_bias.date.isoformat() if close_bias.date else None,
        "base_close_summary_title": close_bias.summary_title,
        "base_close_strong": close_strong,
        "base_close_weak": close_weak,
        "nikkei_futures_change_pct": nikkei_pct,
        "usdjpy_change_pct": fx_pct,
        "vix_change_pct": vix_pct,
        "vix_last": vix_last,
        "bullish_score": bullish_score,
        "bearish_score": bearish_score,
        "source": "close_bias_plus_latest_indicators",
    }

    return {
        "summary_title": summary_title,
        "summary_text": summary_text,
        "tone": tone,
        "strong_sectors": predicted_strong,
        "weak_sectors": predicted_weak,
        "hot_themes": predicted_hot,
        "cold_themes": predicted_cold,
        "raw_detail": raw_detail,
    }


class Command(BaseCommand):
    help = "引け後版の市場の偏りと最新指標から、朝7:00用の preopen 市場の偏りを保存します。"

    def handle(self, *args, **options):
        close_bias = (
            ShihyoMarketBiasSnapshot.objects
            .filter(mode=ShihyoMarketBiasSnapshot.MODE_CLOSE)
            .order_by("-date", "-updated_at")
            .first()
        )
        if not close_bias:
            self.stdout.write(
                self.style.ERROR(
                    "close版の市場の偏りがありません。先に `python manage.py shihyo_build_market_bias` を実行してください。"
                )
            )
            return

        latest = MarketIndicatorSnapshot.objects.order_by("-created_at").first()
        if not latest:
            self.stdout.write(
                self.style.ERROR(
                    "指標スナップショットがありません。先に `python manage.py shihyo_fetch` を実行してください。"
                )
            )
            return

        payload = _build_preopen_bias(close_bias, latest)

        # JST の今日で保存する
        today_local = timezone.localdate()

        obj, created = ShihyoMarketBiasSnapshot.objects.update_or_create(
            date=today_local,
            mode=ShihyoMarketBiasSnapshot.MODE_PREOPEN,
            defaults=payload,
        )

        action = "created" if created else "updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"[shihyo_build_market_bias_preopen] {action} "
                f"date={obj.date} mode={obj.mode} "
                f"strong={obj.strong_sectors} weak={obj.weak_sectors} "
                f"hot={obj.hot_themes} cold={obj.cold_themes}"
            )
        )