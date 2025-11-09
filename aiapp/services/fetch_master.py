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

# 既定のページと添付想定ファイル
JPX_PAGE_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2.html"
JPX_ATT_PATTERN = re.compile(r"data_j\.xls$", re.IGNORECASE)

# ダンプ保存（障害時の手動確認・再読込用）
DUMP_DIR = Path("media/jpx")
DUMP_DIR.mkdir(parents=True, exist_ok=True)

# 33業種（JPX準拠・欠損補完用）
SECTOR33_MAP: dict[str, str] = {
    "005": "水産・農林業", "010": "鉱業", "015": "建設業", "020": "食料品", "025": "繊維製品",
    "030": "パルプ・紙", "035": "化学", "040": "医薬品", "045": "石油・石炭製品", "050": "ゴム製品",
    "055": "ガラス・土石製品", "060": "鉄鋼", "065": "非鉄金属", "070": "金属製品", "075": "機械",
    "080": "電気機器", "085": "輸送用機器", "090": "精密機器", "095": "その他製品", "100": "電気・ガス業",
    "105": "陸運業", "110": "海運業", "115": "空運業", "120": "倉庫・運輸関連業", "125": "情報・通信業",
    "130": "卸売業", "135": "小売業", "140": "銀行業", "145": "証券・商品先物取引業", "150": "保険業",
    "155": "その他金融業", "160": "不動産業", "165": "サービス業",
    # 不正コード混入時の保険
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


def _download_jpx_bytes() -> bytes:
    """
    1) ページを取得 → 2) data_j.xls への絶対URLを検出 → 3) 添付本体を取得
    失敗時はページ本文もダンプしておく。
    """
    page = _http_get(JPX_PAGE_URL)
    DUMP_DIR.joinpath("jpx_page.html").write_bytes(page.content)

    # href 探索（シンプルな正規表現でOK）
    m = re.search(r'href="([^"]*data_j\.xls)"', page.text, re.IGNORECASE)
    if not m:
        # 絶対パス/相対パスどちらも拾えるように <a ...> を総当たり
        cands = re.findall(r'href="([^"]+)"', page.text, re.IGNORECASE)
        for href in cands:
            if JPX_ATT_PATTERN.search(href):
                m = re.match(r".*", href)
                url = href
                break
        else:
            raise RuntimeError("JPXページに data_j.xls へのリンクが見つかりません。")

        att_url = requests.compat.urljoin(JPX_PAGE_URL, url)
    else:
        att_url = requests.compat.urljoin(JPX_PAGE_URL, m.group(1))

    att = _http_get(att_url)
    DUMP_DIR.joinpath("data_j.xls").write_bytes(att.content)
    return att.content


def _read_any_table_from_bytes(data: bytes) -> pd.DataFrame:
    """
    data_j.xls は形式がまちまち。Excel→HTML→CSVの順で強耐性読み。
    """
    # 1) Excel
    try:
        df = pd.read_excel(io.BytesIO(data), dtype=str)
        if df is not None and not df.empty and df.shape[1] >= 2:
            return df
    except Exception:
        pass

    # 2) HTML（cp932優先）
    try:
        text = data.decode("cp932", errors="ignore")
        tables = pd.read_html(io.StringIO(text), flavor="lxml")
        for df in tables:
            if df is not None and not df.empty and df.shape[1] >= 2:
                return df.astype(str)
    except Exception:
        pass

    # 3) CSV
    try:
        df = pd.read_csv(io.BytesIO(data), dtype=str)
        if df is not None and not df.empty and df.shape[1] >= 2:
            return df
    except Exception:
        pass

    raise RuntimeError("JPXマスタを表形式として読み取れませんでした。")


def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    # 列名を空白除去・小文字化キーに変換
    orig_cols = [str(c) for c in df.columns]
    cmap = {c: re.sub(r"\s+", "", str(c)).lower() for c in orig_cols}
    df = df.rename(columns=cmap)

    # 候補
    code_keys = ["コード", "コード番号", "銘柄コード", "証券コード", "code"]
    name_keys = ["銘柄名", "銘柄", "会社名", "name"]
    scode_keys = ["33業種コード", "33業種分類コード", "33業種ｺｰﾄﾞ", "sectorcode", "業種コード"]
    sname_keys = ["33業種区分", "33業種", "業種", "sectorname", "業種名"]

    def pick(keys: list[str]) -> str | None:
        for k in keys:
            lk = re.sub(r"\s+", "", k).lower()
            if lk in df.columns:
                return lk
        return None

    k_code = pick(code_keys)
    k_name = pick(name_keys)
    k_scd = pick(scode_keys)
    k_snm = pick(sname_keys)

    out = pd.DataFrame()
    if k_code and k_name:
        out["code"] = df[k_code].astype(str)
        out["name"] = df[k_name].astype(str)
    else:
        # フォーマット変化の非常口（先頭2列）
        out["code"] = df.iloc[:, 0].astype(str)
        out["name"] = df.iloc[:, 1].astype(str)

    out["sector_code"] = df[k_scd].astype(str) if k_scd else None
    out["sector_name"] = df[k_snm].astype(str) if k_snm else None

    # 正規化
    out["code"] = out["code"].str.replace(r"\D", "", regex=True).str.zfill(4)

    # ETF/ETN 判定
    is_etf = out["code"].str.match(r"^(13|15)\d{2}$") | out["name"].str.contains("ETF|ETN|上場投信", na=False)
    out.loc[is_etf, "sector_code"] = out.loc[is_etf, "sector_code"].fillna("ETF")
    out.loc[is_etf, "sector_name"] = out.loc[is_etf, "sector_name"].fillna(ETF_SECTOR_NAME)

    # sector_code → sector_name 補完
    def norm_code(x: t.Any) -> str | None:
        if x is None:
            return None
        s = re.sub(r"\D", "", str(x))
        if not s:
            return None
        return s.zfill(3)

    out["sector_code"] = out["sector_code"].map(norm_code)

    mask = out["sector_name"].isna() | (out["sector_name"] == "") | (out["sector_name"].str.lower() == "nan")
    out.loc[mask & out["sector_code"].notna(), "sector_name"] = out.loc[
        mask & out["sector_code"].notna(), "sector_code"
    ].map(SECTOR33_MAP)

    for c in ("name", "sector_name"):
        out[c] = out[c].astype(str).str.strip().replace({"None": None, "nan": None})

    out = out.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    return out


def fetch_jpx_rows(source_url: str | None = None) -> list[dict[str, t.Any]]:
    """
    URL未指定: JPXページ→添付 を辿る。指定あり: それを優先（ローカル/URL両対応）。
    """
    if source_url:
        if _is_local_path(source_url):
            data = Path(source_url).read_bytes()
        else:
            data = _http_get(source_url).content
    else:
        # 直近ダンプがあればまずそれを使う（ネット障害時の耐性）
        dump = DUMP_DIR.joinpath("data_j.xls")
        if dump.exists() and dump.stat().st_size > 0:
            data = dump.read_bytes()
        else:
            data = _download_jpx_bytes()

    df = _read_any_table_from_bytes(data)
    df = _normalize_cols(df)

    # 必須列確認（sector_* は後補完可）
    for c in ("code", "name"):
        if c not in df.columns:
            raise RuntimeError(f"JPXマスタ列欠落: {c}")

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
            scode: str | None = r.get("sector_code")
            sname: str | None = r.get("sector_name")

            # 補完
            if (not sname) and scode:
                sname = SECTOR33_MAP.get(scode)
            if code.startswith(("13", "15")) or ("ETF" in name or "ETN" in name or "上場投信" in name):
                sname = ETF_SECTOR_NAME
                scode = "ETF"

            obj, created = StockMaster.objects.get_or_create(
                code=code,
                defaults={"name": name, "sector_code": scode, "sector_name": sname},
            )
            if created:
                res.inserted += 1
            else:
                to_update: dict[str, t.Any] = {}
                if name and obj.name != name:
                    to_update["name"] = name
                if (scode or obj.sector_code) and obj.sector_code != scode:
                    to_update["sector_code"] = scode
                if (sname or obj.sector_name) and obj.sector_name != sname:
                    to_update["sector_name"] = sname
                if to_update:
                    for k, v in to_update.items():
                        setattr(obj, k, v)
                    obj.save(update_fields=list(to_update.keys()))
                    res.updated += 1

        res.after_rows = StockMaster.objects.count()
        res.missing_sector = (
            StockMaster.objects.filter(sector_name__isnull=True).count()
            + StockMaster.objects.filter(sector_name="").count()
        )
        res.ts = now().strftime("%Y-%m-%d %H:%M:%S")

    _apply()
    return {
        "inserted": res.inserted,
        "updated": res.updated,
        "total_input": res.total_input,
        "after_rows": res.after_rows,
        "missing_sector": res.missing_sector,
        "ts": res.ts,
    }