# =========================================================
# [FILE] ticker_normalizer.py
# [PATH] <project_root>/tradeai/services/common/ticker_normalizer.py
#
# このファイルは何？
# - 証券コードを正規化する小さな共通関数です。
# - 例: "7203.T" → "7203"
# =========================================================

from __future__ import annotations


def normalize_ticker(raw: str) -> str:
    value = (raw or "").strip().upper().replace(" ", "")
    if value.endswith(".T"):
        value = value[:-2]
    return value