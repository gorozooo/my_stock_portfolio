# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/policy_schema.py

これは何？
- policies/daytrade/active.yml（デイトレ全自動の「憲法」= PolicySnapshot）を読み込んだあと、
  「必要な項目が揃っているか」「構造が壊れていないか」をチェック（バリデーション）するためのファイルです。
- 全自動では、設定ファイルのミスが事故につながるので、ここで早めに止めます。

なぜ必要？
- YAMLのインデントミス、キーの欠落、型の間違い（リストのはずが文字列など）を検知して、
  「壊れた設定のまま全自動が走る」事故を防ぎます。

置き場所（重要）
- プロジェクトルート（manage.py がある階層）から見て:
  aiapp/services/daytrade/policy_schema.py

関連する設定ファイル（YAML）
- active.yml（本番が読む“現行憲法”）:
  policies/daytrade/active.yml
- snapshots（履歴。バックテストやFixerで増える想定）:
  policies/daytrade/snapshots/*.yml

注意
- このフェーズ1では「厳密な数値レンジ」より「構造が正しいこと」を優先しています。
  後でフェーズが進んだら、範囲チェック（0〜1など）も強化できます。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


class PolicySchemaError(ValueError):
    """ポリシーYAMLに必須キーが無い／構造が壊れている場合に投げる例外です。"""


@dataclass(frozen=True)
class PolicyValidationIssue:
    """将来、複数の警告をまとめて出したい場合のための入れ物（現状は未使用）。"""
    path: str
    message: str


def _is_dict(x: Any) -> bool:
    return isinstance(x, dict)


def _is_list(x: Any) -> bool:
    return isinstance(x, list)


def _require(obj: Dict[str, Any], key: str, path: str) -> Any:
    """
    必須キーを強制するヘルパー。
    例: _require(policy, "risk", "policy") → policy.risk が無ければ例外
    """
    if key not in obj:
        raise PolicySchemaError(f"Missing required key: {path}.{key}")
    return obj[key]


def validate_policy_dict(policy: Dict[str, Any]) -> None:
    """
    active.yml を読み込んだ dict を検証する（フェーズ1の最小スキーマ）。

    目的:
    - 「全自動の憲法(PolicySnapshot)」が壊れていないことを保証する
    - 壊れているなら、ここで止めて事故を防ぐ

    期待するトップレベル構造:
      meta, capital, risk, time_filter, universe_filter, strategy,
      entry, exit, limits, judge_thresholds
    """
    if not _is_dict(policy):
        raise PolicySchemaError("Policy root must be a mapping (dict).")

    # Top-level required sections
    for top in [
        "meta",
        "capital",
        "risk",
        "time_filter",
        "universe_filter",
        "strategy",
        "entry",
        "exit",
        "limits",
        "judge_thresholds",
    ]:
        _require(policy, top, "policy")

    # meta
    meta = policy["meta"]
    if not _is_dict(meta):
        raise PolicySchemaError("policy.meta must be a dict.")
    _require(meta, "policy_id", "policy.meta")
    _require(meta, "created_at", "policy.meta")
    _require(meta, "note", "policy.meta")

    # capital
    capital = policy["capital"]
    if not _is_dict(capital):
        raise PolicySchemaError("policy.capital must be a dict.")
    _require(capital, "base_capital", "policy.capital")

    # risk
    risk = policy["risk"]
    if not _is_dict(risk):
        raise PolicySchemaError("policy.risk must be a dict.")
    _require(risk, "trade_loss_pct", "policy.risk")
    _require(risk, "day_loss_pct", "policy.risk")
    _require(risk, "max_positions", "policy.risk")

    # time_filter
    time_filter = policy["time_filter"]
    if not _is_dict(time_filter):
        raise PolicySchemaError("policy.time_filter must be a dict.")
    _require(time_filter, "session_start", "policy.time_filter")
    _require(time_filter, "session_end", "policy.time_filter")
    if "exclude_ranges" in time_filter:
        if not _is_list(time_filter["exclude_ranges"]):
            raise PolicySchemaError("policy.time_filter.exclude_ranges must be a list.")
        for i, rng in enumerate(time_filter["exclude_ranges"]):
            if not (_is_list(rng) and len(rng) == 2 and all(isinstance(x, str) for x in rng)):
                raise PolicySchemaError(
                    f"policy.time_filter.exclude_ranges[{i}] must be [\"HH:MM\", \"HH:MM\"]."
                )

    # universe_filter
    universe_filter = policy["universe_filter"]
    if not _is_dict(universe_filter):
        raise PolicySchemaError("policy.universe_filter must be a dict.")
    for k in ["min_volume_rank_pct", "min_atr_pct", "max_spread_pct", "exclude_price_gt"]:
        _require(universe_filter, k, "policy.universe_filter")

    # strategy
    strategy = policy["strategy"]
    if not _is_dict(strategy):
        raise PolicySchemaError("policy.strategy must be a dict.")
    for k in ["name", "timeframe", "order_type", "slippage_pct"]:
        _require(strategy, k, "policy.strategy")

    # entry
    entry = policy["entry"]
    if not _is_dict(entry):
        raise PolicySchemaError("policy.entry must be a dict.")
    reqs = _require(entry, "require", "policy.entry")
    if not _is_list(reqs):
        raise PolicySchemaError("policy.entry.require must be a list of mappings.")
    for i, item in enumerate(reqs):
        if not _is_dict(item):
            raise PolicySchemaError(f"policy.entry.require[{i}] must be a dict.")
        if len(item.keys()) == 0:
            raise PolicySchemaError(f"policy.entry.require[{i}] must contain at least one key.")

    # exit
    exit_ = policy["exit"]
    if not _is_dict(exit_):
        raise PolicySchemaError("policy.exit must be a dict.")
    for k in ["take_profit_r", "max_hold_minutes", "exit_on_vwap_break"]:
        _require(exit_, k, "policy.exit")

    # limits
    limits = policy["limits"]
    if not _is_dict(limits):
        raise PolicySchemaError("policy.limits must be a dict.")
    _require(limits, "max_trades_per_day", "policy.limits")

    # judge_thresholds
    judge = policy["judge_thresholds"]
    if not _is_dict(judge):
        raise PolicySchemaError("policy.judge_thresholds must be a dict.")
    for k in ["max_dd_pct", "max_consecutive_losses", "max_daylimit_days_pct", "min_avg_r"]:
        _require(judge, k, "policy.judge_thresholds")