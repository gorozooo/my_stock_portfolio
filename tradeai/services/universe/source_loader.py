# =========================================================
# [FILE] source_loader.py
# [PATH] <project_root>/tradeai/services/universe/source_loader.py
#
# このファイルは何？
# - ユニバース用の銘柄ソースを読み込むファイルです。
# - ローカルのテキストファイルを読み込みます。
# - 日経225 / TOPIX の候補ファイルを複数パターンで探します。
# - 1行にコードだけでなく、CSV風の行が混ざっていても
#   最初に見つかった証券コードを拾えるようにしています。
# =========================================================

from __future__ import annotations

import re
from pathlib import Path

from tradeai.services.common.ticker_normalizer import normalize_ticker


def _extract_ticker(raw_line: str) -> str:
    """
    1行の文字列から、最初に見つかった証券コードを拾う。
    例:
    - 7203
    - 7203.T
    - 7203,トヨタ自動車
    - 7203 トヨタ自動車
    などに対応しやすくする。
    """
    line = (raw_line or "").strip()
    if not line:
        return ""

    candidates = [line]
    candidates.extend(re.split(r"[\s,\t;|]+", line))

    for token in candidates:
        ticker = normalize_ticker(token)
        if ticker:
            return ticker

    return ""


def _read_ticker_file(path: Path) -> set[str]:
    tickers: set[str] = set()
    if not path.exists():
        return tickers

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        lines = path.read_text(encoding="cp932").splitlines()

    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        if raw.startswith("#"):
            continue

        ticker = _extract_ticker(raw)
        if ticker:
            tickers.add(ticker)

    return tickers


def _merge_sources(sources: list[Path]) -> set[str]:
    merged: set[str] = set()
    for source in sources:
        merged |= _read_ticker_file(source)
    return merged


def load_nikkei225_tickers(base_dir: Path) -> set[str]:
    """
    日経225 は既存 aiapp 側も含めて複数候補を読む。
    """
    sources = [
        base_dir / "aiapp" / "data" / "universe" / "nk225.txt",
        base_dir / "aiapp" / "data" / "universe" / "nikkei225.txt",
        base_dir / "tradeai" / "data" / "universe" / "nk225.txt",
        base_dir / "tradeai" / "data" / "universe" / "nikkei225.txt",
    ]
    return _merge_sources(sources)


def load_topix_tickers(base_dir: Path) -> set[str]:
    """
    TOPIX は tradeai 側・aiapp 側の複数候補を読む。
    まずはローカルファイル前提。
    """
    sources = [
        base_dir / "tradeai" / "data" / "universe" / "topix.txt",
        base_dir / "tradeai" / "data" / "universe" / "topix_core.txt",
        base_dir / "aiapp" / "data" / "universe" / "topix.txt",
        base_dir / "aiapp" / "data" / "universe" / "topix_core.txt",
    ]
    return _merge_sources(sources)