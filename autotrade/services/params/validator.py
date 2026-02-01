"""
[FILE] autotrade/services/params/validator.py
[PATH] <project_root>/autotrade/services/params/validator.py

このファイルは何？
- UI や API から渡された数値が「安全かどうか」をチェックする番人です。
- 危険な値が1つでもあれば、バックテストは実行されません。

なぜ必要？
- 数値を自由に触れる＝事故も起きやすい
- 検証する前に「これはダメ」と止めることで、
  ・意味のないバックテスト
  ・本番での破綻
  を防ぎます。

初心者ポイント：
- ここは自分で修正しなくてOK
- エラーメッセージは、そのまま画面に表示できます
"""

from typing import Dict, List

from .schema import PARAM_SCHEMA


class ParamValidationError(Exception):
    """
    パラメータが不正なときに投げる専用エラー

    UI ではこのメッセージをそのまま表示してOK
    """
    pass


def validate_params(params: Dict[str, Dict[str, float]]) -> None:
    """
    パラメータ全体を検証するメイン関数

    引数:
        params:
            {
              "common": {...},
              "breakout": {...},
              "vwap": {...}
            }

    戻り値:
        なし（OKなら何も返さない）

    エラー時:
        ParamValidationError を投げる
    """

    errors: List[str] = []

    # =====================================================
    # 大枠チェック：知らないカテゴリがないか
    # =====================================================
    for category in params.keys():
        if category not in PARAM_SCHEMA:
            errors.append(
                f"未知のカテゴリ '{category}' が含まれています。"
            )

    # =====================================================
    # 各カテゴリ・各パラメータをチェック
    # =====================================================
    for category, schema_items in PARAM_SCHEMA.items():
        category_params = params.get(category, {})

        for key, rule in schema_items.items():
            # -----------------------------
            # 値が存在するか
            # -----------------------------
            if key not in category_params:
                errors.append(
                    f"[{category}] '{rule['label']}' が設定されていません。"
                )
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
        # 複数エラーをまとめて表示
        raise ParamValidationError("\n".join(errors))


def fill_defaults(params: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    """
    足りないパラメータを default 値で埋めるユーティリティ

    UI が部分送信してきても、
    ・schema に従って
    ・必ず完全な形にする
    ために使います。
    """

    filled: Dict[str, Dict[str, float]] = {}

    for category, schema_items in PARAM_SCHEMA.items():
        filled[category] = {}

        category_params = params.get(category, {})

        for key, rule in schema_items.items():
            if key in category_params:
                filled[category][key] = category_params[key]
            else:
                filled[category][key] = rule["default"]

    return filled