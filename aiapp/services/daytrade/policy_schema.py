# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/policy_schema.py

これは何？
- policies/daytrade/active.yml（デイトレ全自動の「憲法」）を読み込んだあと、
  必須キー欠落や構造崩れを検知して、事故を防ぐバリデーション。

注意（Judgeしきい値）
- 新形式: judge_thresholds: { dev:{...}, prod:{...} } を推奨
- 互換: 旧形式 judge_thresholds（フラット）も許可（prod扱い）
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


def _require_judge_thresholds_dict(th: Dict[str, Any], path: str) -> None:
    """
    judge_thresholds の中身（1セット）を検証する。
    """
    if not _is_dict(th):
        raise PolicySchemaError(f"{path} must be a dict.")
    for k in ["max_dd_pct", "max_consecutive_losses", "max_daylimit_days_pct", "min_avg_r"]:
        _require(th, k, path)


def validate_policy_dict(policy: Dict[str, Any]) -> None:
    """
    active.yml を読み込んだ dict を検証する（フェーズ1の最小スキーマ）。

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

    # judge_thresholds（新旧互換で検証）
    judge = policy["judge_thresholds"]
    if not _is_dict(judge):
        raise PolicySchemaError("policy.judge_thresholds must be a dict.")

    # 新形式: dev/prod を持つ
    has_dev = _is_dict(judge.get("dev"))
    has_prod = _is_dict(judge.get("prod"))

    if has_dev or has_prod:
        # dev/prod のうち、存在するものを検証
        if has_dev:
            _require_judge_thresholds_dict(judge["dev"], "policy.judge_thresholds.dev")
        if has_prod:
            _require_judge_thresholds_dict(judge["prod"], "policy.judge_thresholds.prod")
        # dev/prod両方無いは上に引っかからないのでここには来ない
    else:
        # 旧形式: フラット
        _require_judge_thresholds_dict(judge, "policy.judge_thresholds")