from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from pathlib import Path
import pandas as pd
import numpy as np
import datetime as dt
import re

# 33業種コード(50-83) → 日本語
SECT33 = {
    50: "水産・農林業", 51: "鉱業", 52: "建設業", 53: "食料品", 54: "繊維製品", 55: "パルプ・紙",
    56: "化学", 57: "医薬品", 58: "石油・石炭製品", 59: "ゴム製品", 60: "ガラス・土石製品",
    61: "鉄鋼", 62: "非鉄金属", 63: "金属製品", 64: "機械", 65: "電気機器", 66: "輸送用機器",
    67: "精密機器", 68: "その他製品", 69: "電気・ガス業", 70: "陸運業", 71: "海運業", 72: "空運業",
    73: "倉庫・運輸関連業", 74: "情報・通信業", 75: "卸売業", 76: "小売業", 77: "銀行業",
    78: "証券、商品先物取引業", 79: "保険業", 80: "その他金融業", 81: "不動産業", 82: "サービス業", 83: "その他",
}

# ゼロ幅/制御文字を除去
_ZW = r"[\u200B-\u200D\uFEFF\u2060\u00AD]"
_PRIV = r"[\uE000-\uF8FF]"
_CTRL = r"[\x00-\x1F\x7F-\x9F]"
SAN = re.compile(f"{_ZW}|{_PRIV}|{_CTRL}")

def clean_txt(s: str) -> str:
    if s is None:
        return ""
    return SAN.sub("", str(s)).strip()

def to_code4(s: str) -> str:
    m = re.search(r"(\d{4})", str(s))
    return m.group(1) if m else ""

def sector_to_jp(val) -> str:
    """CSVのsectorが日本語/数値/float/None どれでも日本語化して返す"""
    if val is None:
        return ""
    s = clean_txt(val)
    if s == "":
        return ""
    # 数値コード？
    m = re.fullmatch(r"(\d+)(?:\.0)?", s)
    if m:
        n = int(m.group(1))
        return SECT33.get(n, "")
    # すでに日本語想定
    return s

def load_master_map(master_csv: Path) -> dict[str, str]:
    """JPXマスターから code→sector_jp を作る。無ければ空dict"""
    if not master_csv.exists():
        return {}
    df = pd.read_csv(master_csv, dtype=str, low_memory=False)
    if not set(["code", "sector"]).issubset(df.columns):
        return {}
    df["code"] = df["code"].map(to_code4)
    df["sector"] = df["sector"].map(lambda x: sector_to_jp(x))
    df = df.dropna(subset=["code"]).drop_duplicates(subset=["code"])
    return {r.code: r.sector for r in df.itertuples(index=False)}

def slope_sign(series: pd.Series, win: int = 5) -> float:
    """簡易スロープ符号：直近winの線形回帰傾き"""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 2:
        return 0.0
    s = s.tail(win)
    x = np.arange(len(s))
    try:
        k = float(np.polyfit(x, s.values, 1)[0])
    except Exception:
        k = 0.0
    # そのまま傾き値を返す（UI側で up/flat/down 判定）
    return k

class Command(BaseCommand):
    help = "ohlcv.csv を読み、TrendResult を更新（sector_jp を恒久的に確定保存）。"

    def add_arguments(self, p):
        p.add_argument("--root", required=True, help="media/ohlcv/snapshots/YYY-MM-DD")
        p.add_argument("--asof", default=None, help="基準日（省略で今日）")
        p.add_argument("--master", default="media/jpx_master.csv", help="JPXマスターCSV（code,name,sector）")

    def handle(self, *args, **o):
        from ai.models import TrendResult  # 遅延import（migrate中の安全策）

        root = Path(o["root"])
        csv_path = root / "ohlcv.csv"
        if not csv_path.exists():
            raise CommandError(f"ohlcv.csv が見つかりません: {csv_path}")

        asof = o["asof"] or timezone.now().date().isoformat()
        try:
            as_of_date = dt.datetime.strptime(asof, "%Y-%m-%d").date()
        except ValueError:
            raise CommandError("--asof は YYYY-MM-DD 形式で指定してください")

        # --- 1) ohlcv.csv 読み込み（6列固定想定だが堅牢化） -----------------
        df = pd.read_csv(csv_path, dtype=str, low_memory=False)
        for need in ["code", "date", "close", "volume", "name", "sector"]:
            if need not in df.columns:
                raise CommandError(f"ohlcv.csv に列が不足しています: {need}")

        # 正規化
        df["code"] = df["code"].map(to_code4)
        df["name"] = df["name"].map(clean_txt)
        df["sector"] = df["sector"].map(sector_to_jp)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        df = df.dropna(subset=["code", "date", "close"]).sort_values(["code", "date"])

        # --- 2) JPXマスター（fallback） --------------------------------------
        master_map = load_master_map(Path(o["master"]))

        # ohlcv上の “code→sector_jp” （空なら落とす）
        csv_sector = (
            df[["code", "sector"]]
            .dropna()
            .drop_duplicates(subset=["code"])
            .set_index("code")["sector"]
            .to_dict()
        )

        # --- 3) サマリ計算（last/MA/簡易トレンド） ---------------------------
        # 直近レコード
        last = df.sort_values(["code", "date"]).groupby("code").tail(1)

        # ローリング用に groupby
        def roll_feats(grp: pd.DataFrame):
            grp = grp.sort_values("date")
            closes = grp["close"]
            out = {
                "ma5": float(closes.rolling(5).mean().iloc[-1]) if len(closes) >= 5 else float(closes.iloc[-1]),
                "ma20": float(closes.rolling(20).mean().iloc[-1]) if len(closes) >= 20 else float(closes.mean()),
                "ma60": float(closes.rolling(60).mean().iloc[-1]) if len(closes) >= 60 else float(closes.mean()),
                "daily_slope": float(slope_sign(closes, 5)),
                "weekly_trend": float(slope_sign(closes, 20)),
                "monthly_trend": float(slope_sign(closes, 60)),
            }
            return pd.Series(out)

        feats = df.groupby("code").apply(roll_feats).reset_index()

        # last と結合
        agg = last.merge(feats, on="code", how="left")

        # --- 4) DB更新（sector_jp を“常に確定”させ、空値で潰さない） ---------
        created = updated = 0
        with transaction.atomic():
            for r in agg.itertuples(index=False):
                code = r.code
                name = r.name or ""
                last_price = float(r.close)
                last_volume = int(r.volume) if not np.isnan(r.volume) else 0

                # sector_jp の出所優先度: CSVの日本語 > JPXマスター > 既存DB
                candidate = csv_sector.get(code, "")
                if not candidate:
                    candidate = master_map.get(code, "")
                candidate = sector_to_jp(candidate)  # 最終正規化

                try:
                    obj = TrendResult.objects.get(code=code)
                    # 既存名称が空なら埋める／違っていたらクリーン文字列で更新
                    to_update = []
                    if clean_txt(obj.name) != clean_txt(name) and name:
                        obj.name = clean_txt(name)
                        to_update.append("name")

                    # sector_jp は「新しい値が非空」のときだけ上書き
                    if candidate and clean_txt(obj.sector_jp) != candidate:
                        obj.sector_jp = candidate
                        to_update.append("sector_jp")

                    obj.last_price = last_price
                    obj.last_volume = last_volume
                    obj.daily_slope = float(r.daily_slope) if r.daily_slope == r.daily_slope else 0.0
                    obj.weekly_trend = float(r.weekly_trend) if r.weekly_trend == r.weekly_trend else 0.0
                    obj.monthly_trend = float(r.monthly_trend) if r.monthly_trend == r.monthly_trend else 0.0
                    obj.ma5 = float(r.ma5) if r.ma5 == r.ma5 else 0.0
                    obj.ma20 = float(r.ma20) if r.ma20 == r.ma20 else 0.0
                    obj.ma60 = float(r.ma60) if r.ma60 == r.ma60 else 0.0
                    obj.rs_index = obj.rs_index if obj.rs_index is not None else 1.0
                    obj.vol_spike = obj.vol_spike if obj.vol_spike is not None else 1.0
                    obj.confidence = obj.confidence if obj.confidence is not None else 0.0
                    obj.as_of = as_of_date
                    obj.save()
                    updated += 1
                except TrendResult.DoesNotExist:
                    TrendResult.objects.create(
                        code=code,
                        name=clean_txt(name),
                        sector_jp=candidate if candidate else "不明",
                        last_price=last_price,
                        last_volume=last_volume,
                        daily_slope=float(r.daily_slope) if r.daily_slope == r.daily_slope else 0.0,
                        weekly_trend=float(r.weekly_trend) if r.weekly_trend == r.weekly_trend else 0.0,
                        monthly_trend=float(r.monthly_trend) if r.monthly_trend == r.monthly_trend else 0.0,
                        rs_index=1.0,
                        vol_spike=1.0,
                        ma5=float(r.ma5) if r.ma5 == r.ma5 else 0.0,
                        ma20=float(r.ma20) if r.ma20 == r.ma20 else 0.0,
                        ma60=float(r.ma60) if r.ma60 == r.ma60 else 0.0,
                        confidence=0.0,
                        as_of=as_of_date,
                    )
                    created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Updated TrendResult: total={len(agg)} created={created} updated={updated} (as_of={asof})"
        ))