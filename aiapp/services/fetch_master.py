# aiapp/services/fetch_master.py
from __future__ import annotations

import io
import os
import re
import typing as t
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests
from django.db import transaction
from django.utils.timezone import now

from aiapp.models import StockMaster

# ───────────────────────────────────────────────
# JPX 公式マスタ取得設定
# ───────────────────────────────────────────────
JPX_PAGE_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2.html"
JPX_ATT_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
JPX_ATT_PATTERN = re.compile(r"data_j\.xls$", re.IGNORECASE)

# ダンプ保存先（障害時の再利用にも使う）
DUMP_DIR = Path("media/jpx")
DUMP_DIR.mkdir(parents=True, exist_ok=True)

# ───────────────────────────────────────────────
# 33業種コード → 業種名（JPX準拠）
# ───────────────────────────────────────────────
SECTOR33_MAP: dict[str, str] = {
    "005": "水産・農林業", "010": "鉱業", "015": "建設業", "020": "食料品", "025": "繊維製品",
    "030": "パルプ・紙", "035": "化学", "040": "医薬品", "045": "石油・石炭製品", "050": "ゴム製品",
    "055": "ガラス・土石製品", "060": "鉄鋼", "065": "非鉄金属", "070": "金属製品", "075": "機械",
    "080": "電気機器", "085": "輸送用機器", "090": "精密機器", "095": "その他製品", "100": "電気・ガス業",
    "105": "陸運業", "110": "海運業", "115": "空運業", "120": "倉庫・運輸関連業", "125": "情報・通信業",
    "130": "卸売業", "135": "小売業", "140": "銀行業", "145": "証券・商品先物取引業", "150": "保険業",
    "155": "その他金融業", "160": "不動産業", "165": "サービス業",
    "650": "電気機器", "700": "輸送用機器",
}

ETF_SECTOR_NAME = "ETF/ETN"
STD_COLS = ("code", "name", "sector_code", "sector_name")


@dataclass
class RefreshResult:
    inserted: int = 0
    updated: int = 0
    total_input: int = 0
    after_rows: int = 0
    missing_sector: int = 0
    ts: str = ""


def _is_local_path(src: str) -> bool:
    return os.path.exists(src) or src.startswith(("/", "./"))


def _http_get(url: str, timeout: float = 30.0) -> requests.Response:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MyStockPortfolioBot/1.0; +https://gorozooo.com/)",
        "Accept": "*/*",
        "Accept-Language": "ja,en;q=0.8",
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r


# ───────────────────────────────────────────────
# ダウンロード（安定優先：添付 → ページ探索）
# ───────────────────────────────────────────────
def _download_jpx_bytes() -> bytes:
    """
    1) 添付ファイル直リンク（最も安定）を優先
    2) 失敗したらページを取得 → href から data_j.xls を探して再トライ
    取得内容は media/jpx/ にダンプ保存
    """
    override = os.getenv("AIAPP_JPX_URL")
    if override:
        r = _http_get(override)
        DUMP_DIR.joinpath(Path(override).name).write_bytes(r.content)
        return r.content

    # 1️⃣ まず直リンク
    try:
        att = _http_get(JPX_ATT_URL, timeout=30.0)
        DUMP_DIR.joinpath("data_j.xls").write_bytes(att.content)
        return att.content
    except Exception:
        pass

    # 2️⃣ ページ経由でリンク探索
    page = _http_get(JPX_PAGE_URL, timeout=30.0)
    DUMP_DIR.joinpath("jpx_page.html").write_bytes(page.content)

    m = re.search(r'href="([^"]*data_j\.xls)"', page.text, re.IGNORECASE)
    if m:
        att_url = requests.compat.urljoin(JPX_PAGE_URL, m.group(1))
        att = _http_get(att_url, timeout=30.0)
        DUMP_DIR.joinpath("data_j.xls").write_bytes(att.content)
        return att.content

    # 3️⃣ 総当りフォールバック
    cands = re.findall(r'href="([^"]+)"', page.text, re.IGNORECASE)
    for href in cands:
        if JPX_ATT_PATTERN.search(href):
            att_url = requests.compat.urljoin(JPX_PAGE_URL, href)
            att = _http_get(att_url, timeout=30.0)
            DUMP_DIR.joinpath("data_j.xls").write_bytes(att.content)
            return att.content

    raise RuntimeError("JPXの添付 data_j.xls を取得できませんでした。")


# ───────────────────────────────────────────────
# 汎用的なExcel/HTML/CSV読み取り
# ───────────────────────────────────────────────
def _read_any_table_from_bytes(data: bytes) -> pd.DataFrame:
    try:
        df = pd.read_excel(io.BytesIO(data), dtype=str)
        if not df.empty and df.shape[1] >= 2:
            return df
    except Exception:
        pass

    try:
        text = data.decode("cp932", errors="ignore")
        tables = pd.read_html(io.StringIO(text), flavor="lxml")
        for df in tables:
            if not df.empty and df.shape[1] >= 2:
                return df.astype(str)
    except Exception:
        pass

    try:
        df = pd.read_csv(io.BytesIO(data), dtype=str)
        if not df.empty and df.shape[1] >= 2:
            return df
    except Exception:
        pass

    raise RuntimeError("JPXマスタを表形式として読み取れませんでした。")


# ───────────────────────────────────────────────
# 列名正規化・業種補完
# ───────────────────────────────────────────────
def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    orig_cols = [str(c) for c in df.columns]
    cmap = {c: re.sub(r"\s+", "", str(c)).lower() for c in orig_cols}
    df = df.rename(columns=cmap)

    code_keys = ["コード", "銘柄コード", "証券コード", "code"]
    name_keys = ["銘柄名", "会社名", "name"]
    scode_keys = ["33業種コード", "33業種分類コード", "sectorcode", "業種コード"]
    sname_keys = ["33業種区分", "33業種", "業種", "sectorname", "業種名"]

    def pick(keys: list[str]) -> str | None:
        for k in keys:
            lk = re.sub(r"\s+", "", k).lower()
            if lk in df.columns:
                return lk
        return None

    k_code, k_name, k_scd, k_snm = map(pick, [code_keys, name_keys, scode_keys, sname_keys])

    out = pd.DataFrame()
    out["code"] = df[k_code].astype(str) if k_code else df.iloc[:, 0].astype(str)
    out["name"] = df[k_name].astype(str) if k_name else df.iloc[:, 1].astype(str)
    out["sector_code"] = df[k_scd].astype(str) if k_scd else None
    out["sector_name"] = df[k_snm].astype(str) if k_snm else None

    out["code"] = out["code"].str.replace(r"\D", "", regex=True).str.zfill(4)

    is_etf = out["code"].str.match(r"^(13|15)\d{2}$") | out["name"].str.contains("ETF|ETN|上場投信", na=False)
    out.loc[is_etf, "sector_code"] = "ETF"
    out.loc[is_etf, "sector_name"] = ETF_SECTOR_NAME

    def norm_code(x: t.Any) -> str | None:
        if x is None:
            return None
        s = re.sub(r"\D", "", str(x))
        return s.zfill(3) if s else None

    out["sector_code"] = out["sector_code"].map(norm_code)

    mask = out["sector_name"].isna() | (out["sector_name"] == "") | (out["sector_name"].str.lower() == "nan")
    out.loc[mask & out["sector_code"].notna(), "sector_name"] = out.loc[
        mask & out["sector_code"].notna(), "sector_code"
    ].map(SECTOR33_MAP)

    for c in ("name", "sector_name"):
        out[c] = out[c].astype(str).str.strip().replace({"None": None, "nan": None})

    out = out.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    return out


# ───────────────────────────────────────────────
# 公開API（fetch + refresh）
# ───────────────────────────────────────────────
def fetch_jpx_rows(source_url: str | None = None) -> list[dict[str, t.Any]]:
    if source_url:
        if _is_local_path(source_url):
            data = Path(source_url).read_bytes()
        else:
            data = _http_get(source_url).content
    else:
        dump = DUMP_DIR.joinpath("data_j.xls")
        if dump.exists() and dump.stat().st_size > 0:
            data = dump.read_bytes()
        else:
            data = _download_jpx_bytes()

    df = _read_any_table_from_bytes(data)
    df = _normalize_cols(df)
    return df.to_dict(orient="records")


def refresh_master(source_url: str | None = None) -> dict[str, t.Any]:
    rows = fetch_jpx_rows(source_url)
    res = RefreshResult(total_input=len(rows))

    @transaction.atomic
    def _apply():
        for r in rows:
            code = str(r.get("code", "")).strip()
            if not code:
                continue
            name = (r.get("name") or "").strip()
            scode = r.get("sector_code")
            sname = r.get("sector_name")

            if (not sname) and scode:
                sname = SECTOR33_MAP.get(scode)
            if code.startswith(("13", "15")) or ("ETF" in name or "ETN" in name or "上場投信" in name):
                sname, scode = ETF_SECTOR_NAME, "ETF"

            obj, created = StockMaster.objects.get_or_create(
                code=code,
                defaults={"name": name, "sector_code": scode, "sector_name": sname},
            )
            if created:
                res.inserted += 1
            else:
                updates = {}
                if name and obj.name != name:
                    updates["name"] = name
                if (scode or obj.sector_code) and obj.sector_code != scode:
                    updates["sector_code"] = scode
                if (sname or obj.sector_name) and obj.sector_name != sname:
                    updates["sector_name"] = sname
                if updates:
                    for k, v in updates.items():
                        setattr(obj, k, v)
                    obj.save(update_fields=list(updates.keys()))
                    res.updated += 1

        res.after_rows = StockMaster.objects.count()
        res.missing_sector = (
            StockMaster.objects.filter(sector_name__isnull=True).count()
            + StockMaster.objects.filter(sector_name="").count()
        )
        res.ts = now().strftime("%Y-%m-%d %H:%M:%S")

    _apply()
    return res.__dict__