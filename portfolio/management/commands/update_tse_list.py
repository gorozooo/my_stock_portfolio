# portfolio/management/commands/update_tse_list.py
from __future__ import annotations

import io
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple, List

import pandas as pd
import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

# 既定URL（JPX: 東証上場銘柄一覧 / Excel）
DEFAULT_JPX_EXCEL_URL = (
    "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
)

def _base_dir() -> Path:
    # settings.BASE_DIR を優先、なければ manage.py からの相対にフォールバック
    try:
        return Path(settings.BASE_DIR)  # type: ignore[attr-defined]
    except Exception:
        return Path(__file__).resolve().parents[4]  # .../project_root/

def _data_dir() -> Path:
    d = getattr(settings, "TSE_DATA_DIR", None)
    if d:
        p = Path(d)
    else:
        p = _base_dir() / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p

def _csv_path() -> Path:
    # 上書き可能な設定: TSE_LIST_CSV_PATH
    path_in_settings = getattr(settings, "TSE_LIST_CSV_PATH", None)
    if path_in_settings:
        p = Path(path_in_settings)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return _data_dir() / "tse_list.csv"

CODE_RE = re.compile(r"^\d{4}$")  # 東証の4桁コード

NAME_COL_KEYWORDS = [
    "銘柄", "会社", "名称", "社名",       # 日本語
    "Name", "Issuer", "Security", "Company"  # 英語
]

def _guess_columns(df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
    """
    Excelの列名が版によって違っても、4桁コード列と銘柄名列を推測する。
    """
    if df is None or df.empty:
        return None, None

    # 文字列化してから探索（欠損は空文字）
    dff = df.copy()
    dff.columns = [str(c).strip() for c in dff.columns]

    # 候補: まず厳密に「4桁数字」で埋まっている列を探す
    code_col = None
    for col in dff.columns:
        ser = dff[col].astype(str).str.strip()
        # 値のうち、4桁数字の比率が高い列を優先
        total = (ser != "").sum()
        if total == 0:
            continue
        match_ratio = ser.str.fullmatch(CODE_RE).sum() / total
        if match_ratio > 0.7:  # 7割以上が4桁数字ならコード列とみなす
            code_col = col
            break

    # 銘柄名っぽい列（列名にキーワード）
    name_col = None
    for col in dff.columns:
        col_lower = str(col).lower()
        if any(k.lower() in col_lower for k in NAME_COL_KEYWORDS):
            name_col = col
            break

    # ダメ押し：nameが見つからない場合、文字長の平均が長い列を使う
    if name_col is None:
        best_col = None
        best_len = -1.0
        for col in dff.columns:
            ser = dff[col].astype(str).str.strip()
            avg_len = ser.map(len).mean()
            if avg_len > best_len:
                best_len = avg_len
                best_col = col
        name_col = best_col

    return code_col, name_col

def _download_excel(url: str) -> bytes:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content

def _read_all_sheets(xls_bytes: bytes) -> pd.DataFrame:
    with pd.ExcelFile(io.BytesIO(xls_bytes)) as xls:
        frames: List[pd.DataFrame] = []
        for name in xls.sheet_names:
            try:
                df = pd.read_excel(xls, sheet_name=name, dtype=str)
                if df is not None and not df.empty:
                    frames.append(df)
            except Exception:
                continue
        if not frames:
            raise CommandError("Excelのどのシートからもデータを読み込めませんでした。")
        return pd.concat(frames, ignore_index=True)

def _normalize_name(s: str) -> str:
    if not isinstance(s, str):
        return ""
    t = s.strip()
    # ありがちなノイズの簡易除去（任意）
    t = t.replace("\u3000", " ")  # 全角スペース
    return t

class Command(BaseCommand):
    help = "JPX（東証）のExcelを読み込み、コード→銘柄名のCSVを作成します。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            type=str,
            default=None,
            help="Excelの取得URL（未指定なら既定のJPX Excel URLを使用）",
        )
        parser.add_argument(
            "--out",
            type=str,
            default=None,
            help="出力CSVパス（未指定時は settings.TSE_LIST_CSV_PATH または <BASE>/data/tse_list.csv）",
        )
        parser.add_argument(
            "--encoding",
            type=str,
            default="utf-8-sig",
            help="CSVエンコーディング（既定: utf-8-sig）",
        )

    def handle(self, *args, **opts):
        url: Optional[str] = opts.get("url") or getattr(settings, "TSE_CSV_URL", None)
        if not url:
            # URL未指定なら既定を採用（Excel）
            url = DEFAULT_JPX_EXCEL_URL

        out_path = Path(opts.get("out") or _csv_path())
        encoding = opts.get("encoding") or "utf-8-sig"

        self.stdout.write(self.style.NOTICE(f"Downloading: {url}"))
        try:
            xls_bytes = _download_excel(url)
        except Exception as e:
            raise CommandError(f"Excelのダウンロードに失敗しました: {e}")

        self.stdout.write("Reading Excel sheets...")
        df_all = _read_all_sheets(xls_bytes)

        code_col, name_col = _guess_columns(df_all)
        if not code_col or not name_col:
            raise CommandError(
                f"列を特定できませんでした。code_col={code_col}, name_col={name_col}"
            )

        self.stdout.write(f"Detected columns -> code: '{code_col}', name: '{name_col}'")

        df = df_all[[code_col, name_col]].copy()
        df.columns = ["code", "name"]
        # 正規化
        df["code"] = df["code"].astype(str).str.strip()
        df["name"] = df["name"].astype(str).map(_normalize_name)

        # 4桁コードのみ残す
        df = df[df["code"].str.fullmatch(CODE_RE)].copy()

        # 空・重複を処理
        df = df[(df["code"] != "") & (df["name"] != "")]
        df = df.drop_duplicates(subset=["code"], keep="first").sort_values("code")

        count = len(df)
        if count == 0:
            raise CommandError("抽出結果が0件でした。Excelの形式変更の可能性があります。")

        out_path.parent.mkdir(parents=True, exist_ok=True)

        # 保存
        df.to_csv(out_path, index=False, encoding=encoding)
        self.stdout.write(self.style.SUCCESS(f"Saved CSV: {out_path} ({count} rows)"))

        # ついでに JSON も（任意・高速化用）
        try:
            mapping = {r["code"]: r["name"] for _, r in df.iterrows()}
            (out_path.with_suffix(".json")).write_text(
                pd.Series(mapping).to_json(force_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.stdout.write(self.style.SUCCESS(f"Saved JSON: {out_path.with_suffix('.json')}"))
        except Exception:
            pass

        self.stdout.write(self.style.SUCCESS("Done."))