"""
[FILE] shihyo_build_market_bias_preopen.py
[PATH] <project_root>/shihyo/management/commands/shihyo_build_market_bias_preopen.py

このファイルは何？
- 引け後版の市場の偏り(close)と、最新の指標スナップショットを使って
  朝7:00用の市場の偏り(preopen)を作るコマンドです。
- 目的は「昨日どうだったか」ではなく、
  「今日の寄り前に、どこへ資金が向かいそうか」を作ることです。

使い方:
- まず shihyo_fetch で最新指標を保存
- そのあとこのコマンドで preopen を作成

  python manage.py shihyo_fetch
  python manage.py shihyo_build_market_bias_preopen
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

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


def _theme_to_sectors(themes: list[str]) -> list[str]:
    """
    予報用にテーマから代表的な業種名へ戻す。
    UIは業種名を出したいので、ざっくり代表名へ変換する。
    """
    mapping = {
        "金利関連": ["銀行業", "保険業", "証券、商品先物取引業"],
        "商社・景気敏感": ["卸売業", "輸送用機器", "機械"],
        "資源・市況": ["海運業", "鉱業", "石油・石炭製品"],
        "ハイテク": ["電気機器", "精密機器", "情報・通信業"],
        "内需": ["小売業", "食料品", "不動産業"],
        "ディフェンシブ": ["医薬品", "電気・ガス業", "水産・農林業"],
        "グロース": ["サービス業", "情報・通信業"],
        "インフラ関連": ["建設業", "ガラス・土石製品", "倉庫・運輸関連業"],
        "その他": [],
    }

    result: list[str] = []
    for theme in themes:
        result.extend(mapping.get(theme, []))
    return _dedupe_keep_order(result)[:3]


def _build_preopen_bias(close_bias: ShihyoMarketBiasSnapshot, latest: MarketIndicatorSnapshot) -> dict:
    close_strong = [str(x) for x in (close_bias.strong_sectors or [])[:3]]
    close_weak = [str(x) for x in (close_bias.weak_sectors or [])[:3]]
    close_hot = [str(x) for x in (close_bias.hot_themes or [])[:3]]
    close_cold = [str(x) for x in (close_bias.cold_themes or [])[:3]]

    nikkei_pct = _safe_float(latest.nikkei_futures_change_pct, 0.0)
    fx_pct = _safe_float(latest.usdjpy_change_pct, 0.0)
    vix_pct = _safe_float(latest.vix_change_pct, 0.0)
    vix_last = _safe_float(latest.vix_last, 0.0)

    # 外部環境のざっくり判定
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

    # まずは引け後版を土台にする
    predicted_strong = list(close_strong)
    predicted_weak = list(close_weak)
    predicted_hot = list(close_hot)
    predicted_cold = list(close_cold)

    # 追い風が強いとき
    if bullish_score >= bearish_score + 2:
        extra_hot = _dedupe_keep_order(
            close_hot + [_sector_to_theme(x) for x in close_strong]
        )[:3]
        predicted_hot = extra_hot

        # 守りテーマを弱い側へ少し後退
        predicted_cold = _dedupe_keep_order(close_cold)[:3]

        summary_title = "やや強い"
        tone = ShihyoMarketBiasSnapshot.TONE_RISK_ON
        summary_text = (
            f"先物と外部環境が追い風で、"
            f"{' / '.join(predicted_strong[:2]) if predicted_strong else '前日強かった業種'} "
            f"中心の買い継続を想定しています。"
        )

    # 逆風が強いとき
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

    # 強弱まちまち
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

    raw_detail = {
        "base_close_snapshot_date": close_bias.date.isoformat() if close_bias.date else None,
        "base_close_summary_title": close_bias.summary_title,
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
        "strong_sectors": _dedupe_keep_order(predicted_strong)[:3],
        "weak_sectors": _dedupe_keep_order(predicted_weak)[:3],
        "hot_themes": _dedupe_keep_order(predicted_hot)[:3],
        "cold_themes": _dedupe_keep_order(predicted_cold)[:3],
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

        obj, created = ShihyoMarketBiasSnapshot.objects.update_or_create(
            date=latest.created_at.date(),
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