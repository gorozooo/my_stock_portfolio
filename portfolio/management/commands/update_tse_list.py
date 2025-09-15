from __future__ import annotations
import os
import io
import sys
import time
import shutil
import hashlib
import tempfile
from typing import Optional, Tuple

import pandas as pd
import requests
from django.core.management.base import BaseCommand, CommandError

# 既定の保存先: services/trend.py が参照している data/tse_list.csv
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
DEFAULT_OUT = os.path.join(BASE_DIR, "portfolio", "data", "tse_list.csv")
DEFAULT_STATE_DIR = os.path.join(BASE_DIR, "portfolio", "data", ".state")

# 環境変数でURLを指定できるように
ENV_URL = os.environ.get("TSE_CSV_URL", "").strip()


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _atomic_write_bytes(path: str, content: bytes) -> None:
    _ensure_dir(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp_tse_", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _atomic_write_text(path: str, text: str, encoding: str = "utf-8-sig") -> None:
    _atomic_write_bytes(path, text.encode(encoding))


def _load_state(state_dir: str) -> Tuple[Optional[str], Optional[str]]:
    """前回保存したETag/Last-Modifiedを読む"""
    etag_path = os.path.join(state_dir, "tse_list.etag")
    lm_path = os.path.join(state_dir, "tse_list.last_modified")
    etag = None
    last_modified = None
    if os.path.isfile(etag_path):
        try:
            etag = open(etag_path, "r", encoding="utf-8").read().strip()
        except Exception:
            pass
    if os.path.isfile(lm_path):
        try:
            last_modified = open(lm_path, "r", encoding="utf-8").read().strip()
        except Exception:
            pass
    return etag or None, last_modified or None


def _save_state(state_dir: str, etag: Optional[str], last_modified: Optional[str]) -> None:
    _ensure_dir(state_dir)
    if etag:
        _atomic_write_text(os.path.join(state_dir, "tse_list.etag"), etag)
    if last_modified:
        _atomic_write_text(os.path.join(state_dir, "tse_list.last_modified"), last_modified)


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    列名ゆらぎを吸収し、必要な2列 'code','name' をUTF-8で出力できる形に整える。
    - code はゼロ埋めせず“文字列のまま”保持（4〜5桁を想定）
    - name は前後スペース除去
    """
    # 列名を小文字化マップ
    cols = {c.lower(): c for c in df.columns}
    code_col = cols.get("code") or cols.get("銘柄コード") or cols.get("コード")
    name_col = cols.get("name") or cols.get("銘柄名") or cols.get("銘柄")

    if not code_col or not name_col:
        raise CommandError("CSVに 'code' と 'name'（または等価の列名）が見つかりません。")

    out = pd.DataFrame({
        "code": df[code_col].astype(str).str.strip(),
        "name": df[name_col].astype(str).str.strip(),
    })

    # 4〜5桁の数字のみを残す（REIT/ETFなど5桁も通す）
    mask = out["code"].str.fullmatch(r"\d{4,5}")
    out = out[mask].dropna().drop_duplicates(subset=["code"])

    # 並びをコード昇順に
    try:
        out = out.sort_values(by="code", key=lambda s: s.astype(int))
    except Exception:
        out = out.sort_values(by="code")

    return out


class Command(BaseCommand):
    help = "東証公式の「銘柄コード→銘柄名」CSVを取得して portfolio/data/tse_list.csv を更新します。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            type=str,
            default=ENV_URL or "",
            help="CSVの取得先URL（環境変数 TSE_CSV_URL でも指定可）",
        )
        parser.add_argument(
            "--out",
            type=str,
            default=DEFAULT_OUT,
            help=f"出力先CSVパス（既定: {DEFAULT_OUT}）",
        )
        parser.add_argument(
            "--state-dir",
            type=str,
            default=DEFAULT_STATE_DIR,
            help=f"ETag/Last-Modified保存ディレクトリ（既定: {DEFAULT_STATE_DIR}）",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="304でも強制的に再取得・上書きする",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=30,
            help="HTTPタイムアウト秒（既定: 30）",
        )

    def handle(self, *args, **opts):
        url = (opts["url"] or "").strip()
        out_path = opts["out"]
        state_dir = opts["state_dir"]
        force = bool(opts["force"])
        timeout = int(opts["timeout"])

        if not url:
            raise CommandError("CSVのURLが指定されていません。--url か環境変数 TSE_CSV_URL を設定してください。")

        self.stdout.write(self.style.NOTICE(f"Fetching: {url}"))

        # If-None-Match / If-Modified-Since
        etag_prev, lm_prev = _load_state(state_dir)
        headers = {}
        if etag_prev and not force:
            headers["If-None-Match"] = etag_prev
        if lm_prev and not force:
            headers["If-Modified-Since"] = lm_prev

        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except Exception as e:
            raise CommandError(f"ダウンロード失敗: {e}")

        if resp.status_code == 304 and not force:
            self.stdout.write(self.style.SUCCESS("サーバ応答 304 Not Modified。更新不要。"))
            return

        if resp.status_code != 200 or not resp.content:
            raise CommandError(f"不正な応答: {resp.status_code}")

        # pandasで読みつつ検証・整形
        try:
            df = pd.read_csv(io.BytesIO(resp.content), encoding="utf-8-sig")
        except UnicodeDecodeError:
            # もしShift_JISなどなら自動判定にチェンジ
            df = pd.read_csv(io.BytesIO(resp.content), encoding_errors="ignore")
        df = _normalize_df(df)

        if df.empty:
            raise CommandError("整形後のデータが空です。元CSVの列名や中身を確認してください。")

        # 出力（UTF-8 BOM付き）
        csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
        _atomic_write_bytes(out_path, csv_bytes)
        self.stdout.write(self.style.SUCCESS(f"書き込み完了: {out_path}（{len(df)}行）"))

        # ETag / Last-Modified 保存
        etag_new = resp.headers.get("ETag")
        lm_new = resp.headers.get("Last-Modified")
        if etag_new or lm_new:
            _save_state(state_dir, etag_new, lm_new)
            self.stdout.write(self.style.NOTICE("ETag/Last-Modified を保存しました。"))

        self.stdout.write(self.style.SUCCESS("完了。"))