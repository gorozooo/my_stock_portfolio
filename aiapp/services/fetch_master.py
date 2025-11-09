# aiapp/services/fetch_master.py
from __future__ import annotations

import io
import os
import re
import sys
import typing as t
from dataclasses import dataclass

import pandas as pd
import requests
from django.db import transaction
from django.utils.timezone import now

from aiapp.models import StockMaster

# ─────────────────────────────────────────────────────────
# JPX 公式（上場銘柄一覧）既定URL
# 例: https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2.html
# 実体: data_j.xls（拡張子がxlsでも中身はXML Spreadsheetだったりすることがある）
# ─────────────────────────────────────────────────────────
JPX_DEFAULT_URL = (
    "https://www.jpx.co.jp/markets/statistics-equities/misc/"
    "tvdivq0000001vg2-att/data_j.xls"
)

# ─────────────────────────────────────────────────────────
# 33業種コード → 日本語名称（JPX準拠）
# 一部CSVでは「33業種コード」だけで名称列が欠損することがあるため、必ず埋められるように持つ
# ─────────────────────────────────────────────────────────
SECTOR33_MAP: dict[str, str] = {
    "005": "水産・農林業",
    "010": "鉱業",
    "015": "建設業",
    "020": "食料品",
    "025": "繊維製品",
    "030": "パルプ・紙",
    "035": "化学",
    "040": "医薬品",
    "045": "石油・石炭製品",
    "050": "ゴム製品",
    "055": "ガラス・土石製品",
    "060": "鉄鋼",
    "065": "非鉄金属",
    "070": "金属製品",
    "075": "機械",
    "080": "電気機器",
    "085": "輸送用機器",
    "090": "精密機器",
    "095": "その他製品",
    "100": "電気・ガス業",
    "105": "陸運業",
    "110": "海運業",
    "115": "空運業",
    "120": "倉庫・運輸関連業",
    "125": "情報・通信業",
    "130": "卸売業",
    "135": "小売業",
    "140": "銀行業",
    "145": "証券・商品先物取引業",
    "150": "保険業",
    "155": "その他金融業",
    "160": "不動産業",
    "165": "サービス業",
    # 旧CSV/旧XLSで 3桁・4桁・ゼロ詰めなし混在を吸収するために代表的別表記も受ける
    "375": "その他製品",
    "525": "情報・通信業",
    "650": "電気機器",
    "700": "輸送用機器",
    "720": "輸送用機器",
}

ETF_SECTOR_NAME = "ETF/ETN"

# 受け取り側が期待する標準キー
STD_COLS = ("code", "name", "sector_code", "sector_name")


@dataclass
class MasterRow:
    code: str
    name: str
    sector_code: str | None
    sector_name: str | None


def _is_local_path(src: str) -> bool:
    return os.path.exists(src) or src.startswith(("/", "./"))


def _download_bytes(url: str, timeout: float = 20.0) -> bytes:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content


def _read_any_table_from_bytes(data: bytes) -> pd.DataFrame:
    """
    JPXの 'data_j.xls' は実体が古いExcel or XML-Spreadsheet or CSV風など、
    いくつか形式が混ざる。強耐性で読み込み、銘柄一覧の表を返す。
    """
    # 1) Excelとして試行
    try:
        # engineは自動に任せる（xlrd/openpyxlのどちらでも可）
        df = pd.read_excel(io.BytesIO(data), dtype=str)
        if df is not None and len(df.columns) > 1 and len(df) > 0:
            return df
    except Exception:
        pass

    # 2) HTMLテーブルとして試行（XML SpreadsheetやHTMLラップにも対応）
    try:
        tables = pd.read_html(io.BytesIO(data), flavor="lxml")
        for df in tables:
            if df is not None and len(df.columns) > 1 and len(df) > 0:
                return df.astype(str)
    except Exception:
        pass

    # 3) CSVとして試行
    try:
        df = pd.read_csv(io.BytesIO(data), dtype=str)
        if df is not None and len(df.columns) > 1 and len(df) > 0:
            return df
    except Exception:
        pass

    raise RuntimeError("JPXマスタを表形式として読み取れませんでした。")


def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    列名の表記ゆれを標準化し、最低限（code, name, sector_code, sector_name）を揃える。
    """
    original_cols = [str(c).strip() for c in df.columns]
    lower_map = {c: re.sub(r"\s+", "", str(c)).lower() for c in original_cols}
    df = df.rename(columns=lower_map)

    # 候補セット
    code_keys = ["コード", "コード番号", "銘柄コード", "証券コード", "code"]
    name_keys = ["銘柄名", "銘柄", "会社名", "name"]
    sec_code_keys = ["33業種コード", "33業種分類コード", "33業種ｺｰﾄﾞ", "sectorcode", "業種コード"]
    sec_name_keys = ["33業種区分", "33業種", "業種", "sectorname", "業種名"]

    def pick(col_candidates: list[str]) -> str | None:
        # 事前に lower 化したキーで探索
        for key in col_candidates:
            k = re.sub(r"\s+", "", key).lower()
            if k in df.columns:
                return k
        # 元の列名に対しても一応
        for c in original_cols:
            if c in col_candidates:
                return c
        return None

    k_code = pick(code_keys)
    k_name = pick(name_keys)
    k_scd = pick(sec_code_keys)
    k_snm = pick(sec_name_keys)

    out = pd.DataFrame()
    if k_code is None or k_name is None:
        # JPXの表構造変更時の保険：先頭から近い2列を仮採用（異常系）
        out["code"] = df.iloc[:, 0].astype(str)
        out["name"] = df.iloc[:, 1].astype(str)
    else:
        out["code"] = df[k_code].astype(str)
        out["name"] = df[k_name].astype(str)

    if k_scd is not None:
        out["sector_code"] = df[k_scd].astype(str)
    else:
        out["sector_code"] = None

    if k_snm is not None:
        out["sector_name"] = df[k_snm].astype(str)
    else:
        out["sector_name"] = None

    # 4桁コード整形（先頭ゼロ詰め/記号除去）
    out["code"] = out["code"].str.replace(r"\D", "", regex=True).str.zfill(4)

    # ETF/ETN の簡易判定：コードが13xx/15xx帯 ＋ 名称にETF/ETNが含まれるなど
    is_etf = out["code"].str.match(r"^(13|15)\d{2}$")
    name_has_etf = out["name"].str.contains("ETF|ETN|上場投信", na=False)
    out.loc[is_etf | name_has_etf, "sector_name"] = out.loc[
        is_etf | name_has_etf, "sector_name"
    ].fillna(ETF_SECTOR_NAME)
    out.loc[is_etf | name_has_etf, "sector_code"] = out.loc[
        is_etf | name_has_etf, "sector_code"
    ].fillna("ETF")

    # sector_name 欠損を sector_code から補完
    def _norm_code(x: t.Any) -> str | None:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return None
        s = re.sub(r"\D", "", str(x))
        if not s:
            return None
        if len(s) in (2, 3):
            s = s.zfill(3)
        elif len(s) == 1:
            s = s.zfill(3)
        return s

    out["sector_code"] = out["sector_code"].map(_norm_code)
    # map で名称補完（既に入っている場合は触らない）
    mask_missing_name = out["sector_name"].isna() | (out["sector_name"] == "") | (
        out["sector_name"].str.lower() == "nan"
    )
    out.loc[mask_missing_name & out["sector_code"].notna(), "sector_name"] = out.loc[
        mask_missing_name & out["sector_code"].notna(), "sector_code"
    ].map(SECTOR33_MAP)

    # 余計な空白等を除去
    for c in ["name", "sector_name"]:
        out[c] = out[c].astype(str).str.strip().replace({"None": None, "nan": None})

    # 重複除去（同一コード行が複数混じることがある）
    out = out.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)

    return out


def fetch_jpx_rows(source_url: str | None = None) -> list[dict[str, t.Any]]:
    """
    JPXの一覧を取得（URL or ローカルファイル）。辞書リスト（標準キー）で返す。
    """
    src = source_url or JPX_DEFAULT_URL
    if _is_local_path(src):
        with open(src, "rb") as f:
            data = f.read()
    else:
        data = _download_bytes(src)

    df = _read_any_table_from_bytes(data)
    df = _normalize_cols(df)

    # 必須列の最終チェック
    for col in STD_COLS:
        if col not in df.columns:
            if col in ("sector_code", "sector_name"):
                # 欠けても許容（後で補完）する
                continue
            raise RuntimeError(f"JPXマスタ列が見つかりません: {col}")

    rows: list[dict[str, t.Any]] = df.to_dict(orient="records")
    return rows


@dataclass
class RefreshResult:
    inserted: int = 0
    updated: int = 0
    total_input: int = 0
    after_rows: int = 0
    missing_sector: int = 0
    ts: str = ""


def refresh_master(source_url: str | None = None) -> dict[str, t.Any]:
    """
    JPXマスタを取得→StockMasterへ upsert（insert / update の両方をカウント）。
    sector_name 欠損は sector_code からの辞書で必ず補完。ETFは 'ETF/ETN' に統一。
    戻り値: カウント辞書
    """
    rows = fetch_jpx_rows(source_url)
    res = RefreshResult(total_input=len(rows))

    @transaction.atomic
    def _apply():
        for r in rows:
            code: str = str(r.get("code", "")).strip()
            if not code:
                continue
            name: str = (r.get("name") or "").strip()
            scode: str | None = r.get("sector_code")
            sname: str | None = r.get("sector_name")

            # sector_name が空なら、sector_code から補完（最後の砦）
            if (not sname) and scode:
                sname = SECTOR33_MAP.get(scode)

            # ETFの最終統一
            if code.startswith(("13", "15")) or (name and ("ETF" in name or "ETN" in name)):
                sname = ETF_SECTOR_NAME
                scode = "ETF"

            obj, created = StockMaster.objects.get_or_create(code=code, defaults={
                "name": name,
                "sector_code": scode,
                "sector_name": sname,
            })
            if created:
                res.inserted += 1
            else:
                # 既存との差分を見て、変化があれば更新
                changed = False
                to_update: dict[str, t.Any] = {}
                if name and obj.name != name:
                    to_update["name"] = name
                    changed = True
                if (scode or obj.sector_code) and (obj.sector_code != scode):
                    to_update["sector_code"] = scode
                    changed = True
                if (sname or obj.sector_name) and (obj.sector_name != sname):
                    to_update["sector_name"] = sname
                    changed = True

                if changed:
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