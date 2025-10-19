# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import date, timedelta
from typing import Dict, List, Tuple, Optional
import math
import time
import re
import unicodedata

from django.core.management.base import BaseCommand, CommandParser
from django.conf import settings

from ...models_market import SectorSignal
from ...services.sector_map import normalize_sector  # ← 公式正規化を使用


# -----------------------------
# ビルトイン代表シンボル（不足分はここで自動補完）
# 必要に応じて入れ替えてOK。1業種1～数銘柄でOK。
# -----------------------------
DEFAULT_SECTOR_SYMBOLS: Dict[str, str] = {
    "水産・農林業": "1332.T",          # 日本水産
    "鉱業": "1518.T",                 # 三井松島HD など
    "建設業": "1801.T",               # 大成建設
    "食料品": "2269.T",               # 明治
    "繊維製品": "3401.T",             # 帝人
    "パルプ・紙": "3861.T",           # 王子HD
    "化学": "4063.T",                 # 信越化学
    "医薬品": "4502.T",               # 武田薬品
    "石油・石炭製品": "5020.T",       # ENEOS
    "ゴム製品": "5108.T",             # ブリヂストン
    "ガラス・土石製品": "5233.T",     # 太平洋セメント
    "鉄鋼": "5401.T",                 # 日本製鉄
    "非鉄金属": "5713.T",             # 住友金属鉱山
    "金属製品": "6113.T",             # アマダ
    "機械": "6301.T",                 # コマツ
    "電気機器": "6501.T",             # 日立
    "輸送用機器": "7203.T",           # トヨタ
    "精密機器": "7733.T",             # オリンパス
    "その他製品": "7951.T",           # ヤマハ
    "電気・ガス業": "9531.T",         # 東ガス
    "陸運業": "9020.T",               # JR東日本
    "海運業": "9101.T",               # 日本郵船
    "空運業": "9202.T",               # ANA
    "倉庫・運輸関連業": "9301.T",     # 三菱倉庫
    "情報・通信業": "9432.T",         # NTT
    "卸売業": "8058.T",               # 三菱商事
    "小売業": "9983.T",               # ファーストリテイリング
    "銀行業": "8306.T",               # 三菱UFJ
    "証券、商品先物取引業": "8604.T", # 野村HD
    "保険業": "8766.T",               # 東京海上
    "その他金融業": "8591.T",         # オリックス
    "不動産業": "8802.T",             # 三菱地所
    "サービス業": "6098.T",           # リクルート
}

# 任意依存。未導入でもコマンドは安全終了する。
try:
    import yfinance as yf
    import pandas as pd
except Exception:
    yf = None
    pd = None


# -----------------------------
# 文字列整形（不可視文字の除去＋NFKC）
# -----------------------------
_INVIS = re.compile(r"[\u2000-\u200F\u202A-\u202E\u2066-\u2069\uFEFF]+")


def _clean(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = _INVIS.sub("", s)
    return s.strip()


# -----------------------------
# 計算系ヘルパ
# -----------------------------
def _calc_base_close(close: "pd.Series") -> Tuple[float, float, float]:
    """5日/20日騰落の合成（素点）。"""
    if close is None or len(close) < 21:
        return 0.0, 0.0, 0.0
    try:
        chg5 = (close.iloc[-1] / close.iloc[-6] - 1.0) * 100.0
    except Exception:
        chg5 = 0.0
    try:
        chg20 = (close.iloc[-1] / close.iloc[-21] - 1.0) * 100.0
    except Exception:
        chg20 = 0.0
    base = 0.6 * chg5 + 0.4 * chg20
    return float(base), float(chg5), float(chg20)


def _vol_ratio(vol: "pd.Series") -> Optional[float]:
    """出来高比: 近20/過去60 の比."""
    if vol is None or len(vol) < 80:
        return None
    v20 = float(vol.iloc[-20:].mean() or 0.0)
    v60 = float(vol.iloc[-80:-20].mean() or 0.0)
    if v60 <= 0:
        return None
    return float(v20 / v60)


def _tanh_standardize(values: List[float]) -> List[float]:
    """同日セクター間の相対化（z-score→tanhで-1..+1へ圧縮）。"""
    if not values:
        return []
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / max(len(values), 1)
    sd = math.sqrt(var) if var > 0 else 1.0
    z = [(v - m) / (sd or 1.0) for v in values]
    return [math.tanh(v) for v in z]


def _chunk(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i:i + n] for i in range(0, len(lst), n)]


class Command(BaseCommand):
    help = "セクター強弱を自動取得して SectorSignal に保存（yfinance利用）。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--for-date", type=str, default="", help="対象日(YYYY-MM-DD)。未指定は今日")
        parser.add_argument("--days", type=int, default=None, help="lookback日数（settings未指定時に上書き）")
        parser.add_argument("--backfill", type=int, default=0, help="過去N日ぶん前方埋め（営業日想定で日次）")
        parser.add_argument("--skip-if-exists", action="store_true", help="同日の同セクターが既にあればスキップ")
        parser.add_argument("--chunk", type=int, default=24, help="yfinanceの同時取得チャンク幅（多すぎると失敗しやすい）")
        parser.add_argument("--sleep", type=float, default=0.8, help="チャンク間スリープ秒（レート制限対策）")

    # -----------------------------
    # メイン
    # -----------------------------
    def handle(self, *args, **opts):
        if yf is None or pd is None:
            self.stdout.write(self.style.WARNING(
                "yfinance/pandas が見つかりません。`pip install yfinance pandas` を実行してください。"
            ))
            return

        # 1) settings から読み込み
        user_map: Dict[str, str] = getattr(settings, "ADVISOR_SECTOR_SYMBOLS", {}) or {}
        # 2) セクター名を正規化＋不可視除去し、重複は最初を優先
        canon_map: Dict[str, str] = {}
        for raw_sec, sym in user_map.items():
            canon = normalize_sector(_clean(raw_sec))
            if canon and canon not in canon_map and sym:
                canon_map[canon] = sym

        # 3) 足りないセクターはビルトインで自動補完
        for raw_sec, sym in DEFAULT_SECTOR_SYMBOLS.items():
            canon = normalize_sector(_clean(raw_sec))
            if canon and canon not in canon_map:
                canon_map[canon] = sym

        if not canon_map:
            self.stdout.write(self.style.WARNING(
                "セクター代表シンボルが見つかりません（settings も既定も空）。処理を終了します。"
            ))
            return

        lookback = int(opts["days"] or getattr(settings, "ADVISOR_SECTOR_LOOKBACK_DAYS", 90))
        if lookback < 30:
            lookback = 30  # 20日変化を見るので最低限

        # 日付リスト
        if opts["for_date"]:
            yyyy, mm, dd = [int(x) for x in opts["for_date"].split("-")]
            base_day = date(yyyy, mm, dd)
        else:
            base_day = date.today()
        days_list = [base_day - timedelta(days=i) for i in range(int(opts["backfill"] or 0) + 1)]

        # 取得対象ティッカー
        all_symbols = list(canon_map.values())
        chunk_size = max(1, int(opts["chunk"]))
        sleep_sec = max(0.0, float(opts["sleep"]))

        for the_day in days_list:
            start = the_day - timedelta(days=lookback + 5)
            end = the_day + timedelta(days=1)

            self.stdout.write(
                f"[sector_update_auto] {the_day} fetching {len(all_symbols)} syms ({start} → {end})"
            )
            raws: List[Tuple[str, float, float, float, Optional[float]]] = []

            # チャンク分割 & リトライ
            for part in _chunk(all_symbols, chunk_size):
                for attempt in range(3):
                    try:
                        data = yf.download(
                            part,
                            start=start.isoformat(),
                            end=end.isoformat(),
                            progress=False,
                            group_by="ticker",
                            auto_adjust=False,
                            threads=True,
                        )
                        # partごとに集計
                        for sector, sym in canon_map.items():
                            if sym not in part:
                                continue
                            try:
                                # yfinance: 複数銘柄は MultiIndex
                                df = data[sym] if isinstance(data.columns, pd.MultiIndex) else data
                                if isinstance(df, pd.DataFrame) and not df.empty:
                                    close = df["Close"].dropna()
                                    vol = df["Volume"].dropna()
                                else:
                                    close = vol = None
                                base, chg5, chg20 = _calc_base_close(close)
                                vr = _vol_ratio(vol)
                                raws.append((sector, base, chg5, chg20, vr))
                            except Exception:
                                raws.append((sector, 0.0, 0.0, 0.0, None))
                        break  # 成功
                    except Exception as e:
                        if attempt == 2:
                            self.stdout.write(self.style.WARNING(f"   fetch failed for chunk {part}: {e}"))
                        time.sleep(0.8 * (attempt + 1))
                time.sleep(sleep_sec)

            # セクターごとに値が取れなかった場合の穴埋め（0で可視化上は中立扱い）
            seen = {r[0] for r in raws}
            for sector in canon_map.keys():
                if sector not in seen:
                    raws.append((sector, 0.0, 0.0, 0.0, None))

            # 正規化（同日セクター間）
            bases = [r[1] for r in raws] if raws else []
            norms = _tanh_standardize(bases) if bases else []

            created = updated = 0
            for (sector, base, chg5, chg20, vr), score in zip(raws, norms):
                if opts["skip_if_exists"] and SectorSignal.objects.filter(date=the_day, sector=sector).exists():
                    continue
                obj, is_new = SectorSignal.objects.update_or_create(
                    date=the_day,
                    sector=sector,  # ← すでに正規化済み（TOPIX-33 名）
                    defaults=dict(
                        rs_score=float(score),
                        advdec=None,
                        vol_ratio=(None if vr is None else float(vr)),
                        meta=dict(
                            chg5=float(chg5),
                            chg20=float(chg20),
                            base=float(base),
                            lookback=int(lookback),
                            source="yfinance",
                        ),
                    ),
                )
                created += 1 if is_new else 0
                updated += 0 if is_new else 1

            self.stdout.write(
                self.style.SUCCESS(
                    f"[sector_update_auto] {the_day} upsert done. created={created}, updated={updated}"
                )
            )