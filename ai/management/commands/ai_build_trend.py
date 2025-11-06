from __future__ import annotations
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
import pandas as pd
import numpy as np
import datetime as dt
import re

from ai.models import TrendResult

RE_ZW = r"[\u200B-\u200D\uFEFF\u2060\u00AD]"
RE_PRIV = r"[\uE000-\uF8FF]"
RE_CTRL = r"[\x00-\x1F\x7F-\x9F]"
RE_KILL = re.compile(f"{RE_ZW}|{RE_PRIV}|{RE_CTRL}")

def clean(s:str|None)->str:
    if s is None: return ""
    return RE_KILL.sub("", str(s)).strip()

def roll_feats(g: pd.DataFrame) -> pd.Series:
    g = g.sort_values("date")
    price = g["close"].astype(float)
    vol   = g["volume"].astype(float)
    # ざっくり特徴量
    ma5  = price.rolling(5).mean()
    ma20 = price.rolling(20).mean()
    ma60 = price.rolling(60).mean()
    daily_slope = (price.diff(1)).fillna(0.0).tail(5).mean()  # 簡易
    weekly_trend  = (price.pct_change(5)).fillna(0.0).tail(4).mean()
    monthly_trend = (price.pct_change(20)).fillna(0.0).tail(2).mean()
    vol_spike = (vol.tail(5).mean() / (vol.rolling(60).mean().tail(1).replace(0,np.nan))).fillna(1.0).iloc[-1]
    last_price  = float(price.iloc[-1])
    last_volume = int(vol.iloc[-1])
    return pd.Series({
        "last_price": last_price,
        "last_volume": last_volume,
        "daily_slope": float(daily_slope),
        "weekly_trend": float(weekly_trend),
        "monthly_trend": float(monthly_trend),
        "vol_spike": float(vol_spike),
        "ma5": float(ma5.iloc[-1]) if not ma5.empty else 0.0,
        "ma20": float(ma20.iloc[-1]) if not ma20.empty else 0.0,
        "ma60": float(ma60.iloc[-1]) if not ma60.empty else 0.0,
    })

class Command(BaseCommand):
    help = "snapshot(ohlcv.csv)→TrendResultを構築（masterとJOINしてsector_jpを強制）"

    def add_arguments(self, p):
        p.add_argument("--root", required=True)
        p.add_argument("--asof", default=None)

    def handle(self, *args, **o):
        asof = o["asof"] or timezone.now().date().isoformat()
        root = Path(o["root"])
        csv  = root / "ohlcv.csv"
        master = Path("media/jpx_master.csv")
        if not csv.exists():
            raise CommandError(f"snapshot 不在: {csv}")
        if not master.exists():
            raise CommandError(f"master 不在: {master}")

        # snapshot 読み込み（契約: 6列固定）
        df = pd.read_csv(csv, dtype=str, low_memory=False)
        need = ["code","date","close","volume","name","sector"]
        miss = [c for c in need if c not in df.columns]
        if miss: raise CommandError(f"snapshot 列不足: {miss}")

        df["code"]   = df["code"].astype(str).str.extract(r"(\d{4})")[0]
        df["name"]   = df["name"].map(clean)
        df["date"]   = pd.to_datetime(df["date"], errors="coerce")
        df["close"]  = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        df["sector"] = df["sector"].map(clean)  # snapshot側の日本語

        df = df.dropna(subset=["code","date","close","volume"])
        if df.empty: raise CommandError("snapshot 有効データ0件")

        # master 読み込み（code, name, sector→sector_jpへリネーム）
        md = pd.read_csv(master, dtype=str, low_memory=False)
        for c in ("code","name","sector"):
            if c not in md.columns: raise CommandError(f"master 列不足: {c}")
        md["code"]   = md["code"].astype(str).str.extract(r"(\d{4})")[0]
        md["name"]   = md["name"].map(clean)
        md["sector"] = md["sector"].map(lambda s: clean(s).replace(".0",""))
        md = md.dropna(subset=["code","sector"]).drop_duplicates(subset=["code"])
        md = md.rename(columns={"sector":"sector_jp"})

        # JOIN（code基準、snapshotのnameよりもmasterのnameを優先）
        codes_in_snap = df["code"].dropna().unique().tolist()
        j = pd.DataFrame({"code": codes_in_snap}).merge(md[["code","name","sector_jp"]], on="code", how="left")

        missing = j[j["sector_jp"].isna()]["code"].tolist()
        if missing:
            # ここで止める：sector_jp 無いコードは保存禁止
            raise CommandError(f"JOIN未解決 code={len(missing)}件 例: {missing[:10]}")

        # 特徴量集計
        feats = df.groupby("code").apply(roll_feats, include_groups=False).reset_index()
        out = j.merge(feats, on="code", how="left")

        # 相対強度（仮に1.0固定、将来TOPIX対比に差し替え）
        out["rs_index"]   = 1.0
        out["confidence"] = 0.5  # 初期は0.5、将来学習値を入れる

        # 保存
        created, updated = 0, 0
        for r in out.itertuples(index=False):
            tr, is_new = TrendResult.objects.update_or_create(
                code=r.code,
                defaults=dict(
                    name=r.name or r.code,
                    sector_jp=r.sector_jp or "不明",
                    last_price=r.last_price,
                    last_volume=int(r.last_volume or 0),
                    daily_slope=float(r.daily_slope or 0.0),
                    weekly_trend=float(r.weekly_trend or 0.0),
                    monthly_trend=float(r.monthly_trend or 0.0),
                    rs_index=float(r.rs_index or 1.0),
                    vol_spike=float(r.vol_spike or 1.0),
                    ma5=float(r.ma5 or 0.0),
                    ma20=float(r.ma20 or 0.0),
                    ma60=float(r.ma60 or 0.0),
                    confidence=float(r.confidence or 0.0),
                    as_of=dt.datetime.strptime(asof, "%Y-%m-%d").date(),
                )
            )
            created += 1 if is_new else 0
            updated += 0 if is_new else 1

        # 最終ガード：未設定が1件でもあれば落とす
        bad = TrendResult.objects.filter(sector_jp__in=["","-","不明",None], as_of=asof).count()
        if bad:
            raise CommandError(f"保存後ガード: sector_jp未設定 {bad}件（設計上は0のはず）")

        self.stdout.write(self.style.SUCCESS(
            f"Updated TrendResult: total={len(out)} created={created} updated={updated} (as_of={asof})"
        ))