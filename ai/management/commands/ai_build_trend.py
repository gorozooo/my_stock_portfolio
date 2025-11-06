from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
import pandas as pd
import datetime as dt
import re

from ai.models import TrendResult

def _clean(s: str) -> str:
    if s is None:
        return ''
    s = str(s)
    # ゼロ幅／私用領域／制御文字を削除
    s = re.sub(r'[\u200B-\u200D\uFEFF\u2060\u00AD\uE000-\uF8FF\x00-\x1F\x7F-\x9F]', '', s)
    return s.strip()

def _load_sector_map(jpx_master_csv: Path) -> dict[str, str]:
    """
    media/jpx_master.csv（code,name,sector）から 4桁code -> 日本語sector名 を作る。
    sectorが50..83の数値の場合は33業種名へ解決。
    """
    SECT33 = {
        50:'水産・農林業',51:'鉱業',52:'建設業',53:'食料品',54:'繊維製品',55:'パルプ・紙',
        56:'化学',57:'医薬品',58:'石油・石炭製品',59:'ゴム製品',60:'ガラス・土石製品',
        61:'鉄鋼',62:'非鉄金属',63:'金属製品',64:'機械',65:'電気機器',66:'輸送用機器',
        67:'精密機器',68:'その他製品',69:'電気・ガス業',70:'陸運業',71:'海運業',72:'空運業',
        73:'倉庫・運輸関連業',74:'情報・通信業',75:'卸売業',76:'小売業',77:'銀行業',
        78:'証券、商品先物取引業',79:'保険業',80:'その他金融業',81:'不動産業',82:'サービス業',83:'その他'
    }
    if not jpx_master_csv.exists():
        return {}
    m = pd.read_csv(jpx_master_csv, dtype=str, low_memory=False)
    m['code'] = m['code'].astype(str).str.extract(r'(\d{4})')[0]
    m['sector'] = m['sector'].map(lambda x: _clean(x).replace('.0',''))
    m = m.dropna(subset=['code']).drop_duplicates(subset=['code'])
    mp = {}
    for r in m.itertuples(index=False):
        c = getattr(r, 'code')
        s = getattr(r, 'sector', '')
        if re.fullmatch(r'\d{2}', s or ''):
            jp = SECT33.get(int(s), '')
            mp[c] = jp or s
        else:
            mp[c] = s or ''
    return mp

class Command(BaseCommand):
    help = "ohlcv.csv（code,date,close,volume,name,sector）から TrendResult を更新。sector_jp はJPXマスター優先、なければ既存値を温存。last_price/last_volumeはCSVの“最終日”から確定。"

    def add_arguments(self, p):
        p.add_argument("--root", required=True, help="snapshots/DATE ディレクトリ")
        p.add_argument("--asof", default=None, help="基準日（YYYY-MM-DD）")

    def handle(self, *args, **o):
        root = Path(o["root"])
        csv_path = root / "ohlcv.csv"
        if not csv_path.exists():
            raise CommandError(f"CSV not found: {csv_path}")

        asof = o["asof"] or timezone.now().date().isoformat()
        as_of_date = dt.datetime.strptime(asof, "%Y-%m-%d").date()

        # CSV読み込み（厳密）
        df = pd.read_csv(csv_path, dtype={'code': str}, low_memory=False)
        for col in ["code","date","close","volume"]:
            if col not in df.columns:
                raise CommandError(f"CSV列が足りません: {col}")
        # 正規化
        df["code"] = df["code"].astype(str).str.extract(r'(\d{4})')[0]
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["code","date"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

        # 最新日（銘柄ごとに最大日付）のレコードだけ抽出
        idx = df.sort_values(["code","date"]).groupby("code")["date"].idxmax()
        latest = df.loc[idx].copy()

        # JPXマスターから sector_jp マップを用意
        master_csv = Path("media/jpx_master.csv")
        sector_jp_map = _load_sector_map(master_csv)

        # 更新ループ
        updated = 0
        created = 0
        for r in latest.itertuples(index=False):
            code = getattr(r, "code")
            name = _clean(getattr(r, "name", "") or "")
            last_price = float(getattr(r, "close") or 0.0)
            last_volume = int(float(getattr(r, "volume") or 0.0))

            # 既存レコード取得 or 新規
            try:
                tr = TrendResult.objects.get(code=code)
                is_new = False
            except TrendResult.DoesNotExist:
                tr = TrendResult(code=code, name=name or code)
                is_new = True

            # 名前は空なら補完（上書きはしない）
            if not tr.name and name:
                tr.name = name

            # sector_jp 決定ロジック（**恒久運用**）
            # 1) JPXマスターにあればそれを採用
            # 2) なければ既存の sector_jp を温存（'-' や空は触らない）
            # 3) どちらも無ければ '-' にする（今回だけ）
            new_sector = sector_jp_map.get(code, "").strip()
            if new_sector:
                tr.sector_jp = new_sector
            elif not tr.sector_jp:
                tr.sector_jp = "-"

            # 価格・出来高・as_of を確定
            tr.last_price = last_price
            tr.last_volume = last_volume
            tr.as_of = as_of_date

            # 既存のトレンド指標は温存（あなたの既存ロジックで別ジョブ更新する想定）
            tr.save()
            if is_new: created += 1
            else: updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Updated TrendResult: total={TrendResult.objects.count()} created={created} updated={updated} (as_of={as_of_date})"
        ))