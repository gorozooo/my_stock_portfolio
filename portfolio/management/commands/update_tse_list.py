# portfolio/management/commands/update_tse_list.py
from __future__ import annotations
import os
import io
import json
import unicodedata
from typing import Optional

import pandas as pd
import requests
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings


DEFAULT_XLS_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "data")
CSV_PATH = os.path.join(DATA_DIR, "tse_list.csv")
JSON_PATH = os.path.join(DATA_DIR, "tse_list.json")


def clean_text(s: Optional[str]) -> str:
    """Unicode正規化し、制御/私用領域/ゼロ幅等の不可視文字を除去"""
    if s is None:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKC", s)
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat[0] == "C":  # C* = control/format/private-use/unknown
            continue
        if ch in "\u200B\u200C\u200D\u2060\uFEFF":
            continue
        out.append(ch)
    return "".join(out).strip()


def detect_columns(df: pd.DataFrame):
    # 列名をクレンジング & 小文字化して探索
    norm_map = {c: clean_text(c).lower() for c in df.columns}
    # 候補
    code_candidates = {"code", "ｺｰﾄﾞ", "コード", "こーど"}
    name_candidates = {"name", "銘柄名", "めいがらめい"}

    code_col = None
    name_col = None
    for raw, low in norm_map.items():
        if low in code_candidates and code_col is None:
            code_col = raw
        if low in name_candidates and name_col is None:
            name_col = raw
    return code_col, name_col


class Command(BaseCommand):
    help = "東証の銘柄コード→銘柄名一覧を取得して data/tse_list.csv & json を更新します。"

    def add_arguments(self, parser):
        parser.add_argument("--url", help="ExcelのURL（未指定なら既定URLを使用）")

    def handle(self, *args, **opts):
        url = opts.get("url") or os.environ.get("TSE_XLS_URL") or DEFAULT_XLS_URL
        self.stdout.write(f"Downloading: {url}")

        os.makedirs(DATA_DIR, exist_ok=True)

        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            raise CommandError(f"ダウンロードに失敗: {e}")

        self.stdout.write("Reading Excel sheets...")

        # .xls だが pandas が読める（xlrd>=2.0 は xls未対応のため pyxlsb 等が必要な環境も。
        # ここでは pandasのエンジン自動判定に任せる）
        try:
            xls = pd.ExcelFile(io.BytesIO(resp.content))
        except Exception as e:
            raise CommandError(f"Excel解析に失敗: {e}")

        # 最初に 'コード' と '銘柄名' っぽい列を持つシートを探す
        df_all = []
        for sheet in xls.sheet_names:
            try:
                df = xls.parse(sheet_name=sheet, dtype=str, header=0)
                code_col, name_col = detect_columns(df)
                if code_col and name_col:
                    df = df[[code_col, name_col]].copy()
                    df.columns = ["code", "name"]
                    df_all.append(df)
            except Exception:
                continue

        if not df_all:
            raise CommandError("コード/銘柄名の列を持つシートが見つかりませんでした。")

        df = pd.concat(df_all, ignore_index=True)

        # クレンジング
        df["code"] = df["code"].map(clean_text)
        df["name"] = df["name"].map(clean_text)

        # 4〜5桁数字のみに限定、重複は最後を優先
        df = df[df["code"].str.fullmatch(r"\d{4,5}")].dropna()
        df = df.drop_duplicates(subset=["code"], keep="last").sort_values("code")

        # 保存
        df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(dict(zip(df["code"], df["name"])), f, ensure_ascii=False, indent=2)

        self.stdout.write(self.style.SUCCESS(f"Saved CSV:  {CSV_PATH} ({len(df)} rows)"))
        self.stdout.write(self.style.SUCCESS(f"Saved JSON: {JSON_PATH}"))
        self.stdout.write(self.style.SUCCESS("Done."))