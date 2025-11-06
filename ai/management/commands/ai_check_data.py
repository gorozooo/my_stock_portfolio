from __future__ import annotations
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
import pandas as pd
import re, sys, hashlib

RE_ZW = r"[\u200B-\u200D\uFEFF\u2060\u00AD]"           # ゼロ幅
RE_PRIV = r"[\uE000-\uF8FF]"                           # 私用領域
RE_CTRL = r"[\x00-\x1F\x7F-\x9F]"                      # 制御
RE_KILL = re.compile(f"{RE_ZW}|{RE_PRIV}|{RE_CTRL}")

SECT33 = {
  50:'水産・農林業',51:'鉱業',52:'建設業',53:'食料品',54:'繊維製品',55:'パルプ・紙',
  56:'化学',57:'医薬品',58:'石油・石炭製品',59:'ゴム製品',60:'ガラス・土石製品',
  61:'鉄鋼',62:'非鉄金属',63:'金属製品',64:'機械',65:'電気機器',66:'輸送用機器',
  67:'精密機器',68:'その他製品',69:'電気・ガス業',70:'陸運業',71:'海運業',72:'空運業',
  73:'倉庫・運輸関連業',74:'情報・通信業',75:'卸売業',76:'小売業',77:'銀行業',
  78:'証券、商品先物取引業',79:'保険業',80:'その他金融業',81:'不動産業',82:'サービス業',83:'その他'
}

def clean(s:str|None)->str:
    if s is None: return ""
    return RE_KILL.sub("", str(s)).strip()

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

class Command(BaseCommand):
    help = "master/snapshot/DB の一貫性を検証する品質ゲート（NGがあれば非0終了）"

    def add_arguments(self, p):
        p.add_argument("--asof", default=None)

    def handle(self, *args, **o):
        asof = o["asof"] or timezone.now().date().isoformat()
        master = Path("media/jpx_master.csv")
        snap = Path(f"media/ohlcv/snapshots/{asof}/ohlcv.csv")

        # --- master 検証 ---
        if not master.exists():
            raise CommandError(f"master 不在: {master}")
        md = pd.read_csv(master, dtype=str, low_memory=False)
        for c in ("code","name","sector"):
            if c not in md.columns: raise CommandError(f"master 列不足: {c}")
        md["code"]   = md["code"].astype(str).str.extract(r"(\d{4})")[0]
        md["name"]   = md["name"].map(clean)
        md["sector"] = md["sector"].map(lambda s: clean(s).replace(".0",""))
        md = md.dropna(subset=["code","sector"]).drop_duplicates(subset=["code"])
        if md.empty: raise CommandError("master が空")
        sect_bad = md["sector"].eq("").sum()
        if sect_bad: raise CommandError(f"master sector 空 {sect_bad}件")
        msha = sha256_of(master)

        # --- snapshot 検証 ---
        if not snap.exists():
            raise CommandError(f"snapshot 不在: {snap}")
        df = pd.read_csv(snap, dtype=str, low_memory=False)
        need = ["code","date","close","volume","name","sector"]
        miss = [c for c in need if c not in df.columns]
        if miss: raise CommandError(f"snapshot 列不足: {miss}")
        df["code"] = df["code"].astype(str).str.extract(r"(\d{4})")[0]
        df["sector"] = df["sector"].map(clean)
        dupe = df.duplicated(subset=["code","date"]).sum()
        if dupe: raise CommandError(f"snapshot (code,date) 重複 {dupe}件")
        if df["code"].isna().any(): raise CommandError("snapshot code NaNあり")
        ssha = sha256_of(snap)

        # --- DB 検証 ---
        from ai.models import TrendResult as T
        bad = T.objects.filter(sector_jp__in=["","-","不明",None]).count()
        price_bad = T.objects.filter(last_price__isnull=True).count()
        if bad>0 or price_bad>0:
            raise CommandError(f"DB不整合: sector_jp未設定={bad}, price欠損={price_bad}")

        self.stdout.write(self.style.SUCCESS(
            f"[OK] as_of={asof} master_sha={msha[:8]} snapshot_sha={ssha[:8]} "
            f"codes_master={md['code'].nunique()} rows_snapshot={len(df)}"
        ))