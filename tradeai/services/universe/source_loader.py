# =========================================================
# [FILE] source_loader.py
# [PATH] <project_root>/tradeai/services/universe/source_loader.py
#
# このファイルは何？
# - ユニバース用の銘柄ソースを読み込むファイルです。
# - まずはローカルのテキストファイルから読み込みます。
# - 既存 aiapp の nk225.txt を再利用します。
# =========================================================

from __future__ import annotations

from pathlib import Path

from tradeai.services.common.ticker_normalizer import normalize_ticker


def _read_ticker_file(path: Path) -> set[str]:
    tickers: set[str] = set()
    if not path.exists():
        return tickers

    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        if raw.startswith("#"):
            continue
        ticker = normalize_ticker(raw)
        if ticker:
            tickers.add(ticker)

    return tickers


def load_nikkei225_tickers(base_dir: Path) -> set[str]:
    """
    既存 aiapp の nk225.txt を優先利用。
    追加で tradeai/data/universe/nikkei225.txt があればそれもマージ。
    """
    sources = [
        base_dir / "aiapp" / "data" / "universe" / "nk225.txt",
        base_dir / "tradeai" / "data" / "universe" / "nikkei225.txt",
    ]

    merged: set[str] = set()
    for source in sources:
        merged |= _read_ticker_file(source)
    return merged


def load_topix_tickers(base_dir: Path) -> set[str]:
    """
    TOPIX は今後の自動同期に広げる前提。
    現段階では tradeai/data/universe/topix.txt があれば読み込む。
    """
    sources = [
        base_dir / "tradeai" / "data" / "universe" / "topix.txt",
    ]

    merged: set[str] = set()
    for source in sources:
        merged |= _read_ticker_file(source)
    return merged