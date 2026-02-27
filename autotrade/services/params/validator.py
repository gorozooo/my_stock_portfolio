"""
[FILE] autotrade/services/params/validator.py
[PATH] <project_root>/autotrade/services/params/validator.py

このファイルは何？
- UI や API から渡された数値が「安全かどうか」をチェックする番人です。
- 危険な値が1つでもあれば、バックテストは実行されません。

BREAKOUT一本運用（互換方針）：
- PARAM_SCHEMA に存在しないカテゴリ（例: vwap）が params に混ざっていてもエラーにしない。
  （過去データ/古いUI/古いsnapshotが混ざっても死なないため）
- 検証対象は「schemaに定義されたカテゴリだけ」。
"""

from typing import Dict, List, Any

from .schema import PARAM_SCHEMA


class ParamValidationError(Exception):
    """
    パラメータが不正なときに投げる専用エラー

    UI ではこのメッセージをそのまま表示してOK
    """
    pass


# 互換許可カテゴリ（schema外でも“エラーにしない”）
# 例：BREAKOUT一本へ移行する過程で残る vwap
_ALLOWED_UNKNOWN_CATEGORIES = {"vwap", "VWAP"}


def validate_params(params: Dict[str, Dict[str, Any]]) -> None:
    """
    パラメータ全体を検証するメイン関数

    引数:
        params:
            {
              "common": {...},
              "breakout": {...},
              （互換で "vwap": {...} が来てもOK：無視する）
            }

    戻り値:
        なし（OKなら何も返さない）

    エラー時:
        ParamValidationError を投げる
    """

    if not isinstance(params, dict):
        raise ParamValidationError("params の形式が不正です（dictが必要）。")

    errors: List[str] = []

    # =====================================================
    # 大枠チェック：知らないカテゴリがないか
    #   - ただし vwap は互換として許可
    # =====================================================
    for category in params.keys():
        if category in PARAM_SCHEMA:
            continue
        if str(category) in _ALLOWED_UNKNOWN_CATEGORIES:
            continue
        errors.append(f"未知のカテゴリ '{category}' が含まれています。")

    # =====================================================
    # 各カテゴリ・各パラメータをチェック（schemaにあるものだけ）
    # =====================================================
    for category, schema_items in PARAM_SCHEMA.items():
        category_params = params.get(category, {})
        if not isinstance(category_params, dict):
            category_params = {}

        for key, rule in schema_items.items():
            # -----------------------------
            # 値が存在するか
            # -----------------------------
            if key not in category_params:
                errors.append(f"[{category}] '{rule['label']}' が設定されていません。")
                continue

            value = category_params[key]

            # -----------------------------
            # 型チェック
            # -----------------------------
            expected_type = rule["type"]
            if not isinstance(value, expected_type):
                errors.append(
                    f"[{category}] '{rule['label']}' の型が不正です。"
                    f"（{expected_type.__name__} が必要）"
                )
                continue

            # -----------------------------
            # 最小値チェック
            # -----------------------------
            if value < rule["min"]:
                errors.append(
                    f"[{category}] '{rule['label']}' が小さすぎます。"
                    f"（最小 {rule['min']}）"
                )

            # -----------------------------
            # 最大値チェック
            # -----------------------------
            if value > rule["max"]:
                errors.append(
                    f"[{category}] '{rule['label']}' が大きすぎます。"
                    f"（最大 {rule['max']}）"
                )

    # =====================================================
    # エラーが1つでもあれば即中断
    # =====================================================
    if errors:
        raise ParamValidationError("\n".join(errors))


def fill_defaults(params: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    足りないパラメータを default 値で埋めるユーティリティ

    BREAKOUT一本運用：
    - schema にあるカテゴリだけを「完全な形」にする。
    - schema外（例: vwap）は無視する（混ざっててもOK・返さない）。
    """

    if not isinstance(params, dict):
        params = {}

    filled: Dict[str, Dict[str, Any]] = {}

    for category, schema_items in PARAM_SCHEMA.items():
        filled[category] = {}

        category_params = params.get(category, {})
        if not isinstance(category_params, dict):
            category_params = {}

        for key, rule in schema_items.items():
            if key in category_params:
                filled[category][key] = category_params[key]
            else:
                filled[category][key] = rule["default"]

    return filled