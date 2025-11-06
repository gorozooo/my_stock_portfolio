# ai/presentation/views/home.py
from __future__ import annotations

import re
from typing import Dict, Iterable

from django.utils import timezone
from django.views.generic import TemplateView

from ai.services.screening import generate_top10_candidates
from ai.services.regime import calculate_market_regime
from ai.models import TrendResult  # ← DB の sector_jp を参照


# JPX 33業種（数値コード → 日本語）
SECT33: Dict[int, str] = {
    50: "水産・農林業", 51: "鉱業", 52: "建設業", 53: "食料品", 54: "繊維製品", 55: "パルプ・紙",
    56: "化学", 57: "医薬品", 58: "石油・石炭製品", 59: "ゴム製品", 60: "ガラス・土石製品",
    61: "鉄鋼", 62: "非鉄金属", 63: "金属製品", 64: "機械", 65: "電気機器", 66: "輸送用機器",
    67: "精密機器", 68: "その他製品", 69: "電気・ガス業", 70: "陸運業", 71: "海運業", 72: "空運業",
    73: "倉庫・運輸関連業", 74: "情報・通信業", 75: "卸売業", 76: "小売業", 77: "銀行業",
    78: "証券、商品先物取引業", 79: "保険業", 80: "その他金融業", 81: "不動産業", 82: "サービス業", 83: "その他",
}


def _build_sector_map(codes: Iterable[str]) -> Dict[str, str]:
    """
    TrendResult.sector_jp を優先して code→sector_jp の辞書を作る。
    DBに無ければ空を返す（後段でSECT33/ガードで補う）。
    """
    qs = TrendResult.objects.filter(code__in=list(codes)).values("code", "sector_jp")
    mp: Dict[str, str] = {}
    zwc = r"[\u200B-\u200D\uFEFF\u2060\u00AD\uE000-\uF8FF\x00-\x1F\x7F-\x9F]"
    for r in qs:
        s = re.sub(zwc, "", (r["sector_jp"] or "")).strip()
        if s:
            mp[r["code"]] = s
    return mp


def _normalize_sector(raw_sector, code: str, sector_map: Dict[str, str]) -> str:
    """
    どんな値が来ても最終的に『日本語の33業種名』を返す防弾関数。
    優先度:
      1) DB(TrendResult) の sector_jp
      2) 数値 50..83 → SECT33
      3) それ以外は '-' （無効値や価格 2050.0 の混入もここで捨てる）
    """
    # 1) DB 優先
    if code in sector_map and sector_map[code]:
        return sector_map[code]

    # 2) 数値コードを許容（"65", "65.0" など）
    s = "" if raw_sector is None else str(raw_sector).strip()
    m = re.fullmatch(r"(\d+)(?:\.0+)?", s)
    if m:
        n = int(m.group(1))
        if 50 <= n <= 83:
            return SECT33.get(n, "-")

    # 2050.0 のような価格混入はここで無効化
    return "-"


class AIHomeView(TemplateView):
    template_name = "ai/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["updated_at"] = timezone.localtime().strftime("%H:%M")

        # レジーム（日/週/月）
        regime = calculate_market_regime()
        ctx["regime"] = regime.get("headline", regime)
        ctx["mode"] = {"period": "中期", "stance": "普通"}

        # 候補を取得（最大10件想定）
        candidates = list(generate_top10_candidates())

        # 事前に code→sector_jp の辞書をまとめて取得（N+1回避）
        codes = [c.code for c in candidates]
        sector_map = _build_sector_map(codes)

        # テンプレに流す最終データ
        items = []
        for c in candidates:
            items.append({
                "name":   c.name,
                "code":   c.code,
                # ← ここが肝：どんな入力でも日本語の33業種に正規化
                "sector": _normalize_sector(getattr(c, "sector", None), c.code, sector_map),
                "score":  c.score,
                "stars":  c.stars,
                "trend":  {"d": c.trend.d, "w": c.trend.w, "m": c.trend.m},
                "reasons": c.reasons,
                "prices": {
                    "entry": c.prices.entry,
                    "tp":    c.prices.tp,
                    "sl":    c.prices.sl,
                },
                "qty": {
                    "shares":   c.qty.shares,
                    "capital":  c.qty.capital,
                    "pl_plus":  c.qty.pl_plus,
                    "pl_minus": c.qty.pl_minus,
                    "r":        c.qty.r,
                },
            })

        ctx["items"] = items
        return ctx