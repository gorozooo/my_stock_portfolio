# =========================================================
# [FILE] source_loader.py
# [PATH] <project_root>/tradeai/services/universe/source_loader.py
#
# このファイルは何？
# - ユニバース用の銘柄ソースを読み込むファイルです。
# - tradeai 専用のテキストファイルから読み込みます。
# - 日経225 / TOPIX / グロース を個別に管理します。
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
    tradeai 専用の日経225ファイルだけを読み込む。
    """
    source = base_dir / "tradeai" / "data" / "universe" / "nikkei225.txt"
    return _read_ticker_file(source)


def load_topix_tickers(base_dir: Path) -> set[str]:
    """
    tradeai 専用の TOPIX ファイルを読み込む。
    """
    source = base_dir / "tradeai" / "data" / "universe" / "topix.txt"
    return _read_ticker_file(source)


def load_growth_tickers(base_dir: Path) -> set[str]:
    """
    tradeai 専用の グロース ファイルを読み込む。
    """
    source = base_dir / "tradeai" / "data" / "universe" / "growth.txt"
    return _read_ticker_file(source)