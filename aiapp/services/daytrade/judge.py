# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/judge.py

これは何？
- デイトレ戦略を「本番で回してよいか」を機械的に判定する Judge。
- バックテストの期間結果（DayResultの配列）を入力として、
  policy 内の judge_thresholds を基準に GO / NO-GO を返す。

モード
- mode="dev": judge_thresholds.dev を使う（開発用）
- mode="prod": judge_thresholds.prod を使う（本番用）
- 互換:
  - 旧 judge_thresholds（フラット）は prod 扱い
  - judge_thresholds_dev / judge_thresholds_prod も拾う

重要バグ修正（2026-01-28）
- thresholds.get(..., default) の後に `or default` を使うと
  0.0 や 0 が falsy 扱いになって default に潰れてしまう。
  例: min_avg_r=0.0 が -999 に化ける → マイナスでも GO になり得る
- None のときだけ default を使うように修正する
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class JudgeResult:
    decision: str               # "GO" or "NO_GO"
    reasons: List[str]          # NO_GO の理由（GOなら空）
    metrics: Dict[str, Any]     # 判定に使った実数値
    mode: str = "prod"          # "dev" or "prod"


def _safe_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _to_float(v: Any, default: float) -> float:
    """
    None のときだけ default にする（0.0 は有効値として保持する）
    """
    if v is None:
        return float(default)
    try:
        return float(v)
    except Exception:
        return float(default)


def _to_int(v: Any, default: int) -> int:
    """
    None のときだけ default にする（0 は有効値として保持する）
    """
    if v is None:
        return int(default)
    try:
        return int(v)
    except Exception:
        return int(default)


def _pick_thresholds(policy: Dict[str, Any], mode: str) -> Dict[str, Any]:
    """
    しきい値の取り出し優先順位（安全＆互換）：
    1) judge_thresholds: {dev:{}, prod:{}} のネスト形式
    2) judge_thresholds_dev / judge_thresholds_prod（旧案）
    3) judge_thresholds（フラット：旧形式）は prod 扱い
    """
    mode = (mode or "prod").strip().lower()
    p = _safe_dict(policy)

    # 1) ネスト形式
    jt = _safe_dict(p.get("judge_thresholds"))
    if jt:
        dev = _safe_dict(jt.get("dev"))
        prod = _safe_dict(jt.get("prod"))

        if mode == "dev":
            if dev:
                return dev
            # dev が無い場合は prod を使う（安全側）
            if prod:
                return prod
            # それも無ければフラット扱いへ落とす（下へ）

        # prod
        if prod:
            return prod

        # judge_thresholds がフラット（旧形式）だった場合
        # （dev/prodキーが無いなら、そのままフラットとみなす）
        # 例: {"max_dd_pct":0.02,...}
        if any(k in jt for k in ["max_dd_pct", "max_consecutive_losses", "max_daylimit_days_pct", "min_avg_r"]):
            return jt

    # 2) 旧案キー
    if mode == "dev":
        th_dev = _safe_dict(p.get("judge_thresholds_dev"))
        if th_dev:
            return th_dev
        # 無ければ prod へ

    th_prod = _safe_dict(p.get("judge_thresholds_prod"))
    if th_prod:
        return th_prod

    # 3) 最終 fallback（旧 judge_thresholds フラット）
    th_flat = _safe_dict(p.get("judge_thresholds"))
    return th_flat


def judge_backtest_results(
    day_results: List[Any],
    policy: Dict[str, Any],
    mode: str = "prod",
) -> JudgeResult:
    mode = (mode or "prod").strip().lower()
    thresholds = _pick_thresholds(policy, mode)

    # ※ 0.0 / 0 を “有効な値” として扱う（or default 禁止）
    max_dd_pct_limit = _to_float(thresholds.get("max_dd_pct"), 0.0)
    max_consecutive_losses_limit = _to_int(thresholds.get("max_consecutive_losses"), 0)
    max_daylimit_days_pct_limit = _to_float(thresholds.get("max_daylimit_days_pct"), 1.0)
    min_avg_r_limit = _to_float(thresholds.get("min_avg_r"), -999.0)

    total_days = len(day_results or [])
    if total_days == 0:
        return JudgeResult(
            decision="NO_GO",
            reasons=["no_backtest_days"],
            metrics={},
            mode=mode,
        )

    # --- 集計 ---
    all_trades: List[Any] = []
    daylimit_days = 0
    max_dd_yen = 0
    max_consecutive_losses = 0

    for d in day_results:
        trades = list(getattr(d, "trades", []) or [])
        all_trades.extend(trades)

        if bool(getattr(d, "day_limit_hit", False)):
            daylimit_days += 1

        try:
            max_dd_yen = min(max_dd_yen, int(getattr(d, "max_drawdown_yen", 0) or 0))
        except Exception:
            pass

        try:
            max_consecutive_losses = max(max_consecutive_losses, int(getattr(d, "max_consecutive_losses", 0) or 0))
        except Exception:
            pass

    # --- 指標計算 ---
    avg_r = 0.0
    if all_trades:
        try:
            avg_r = float(sum(float(getattr(t, "r", 0.0) or 0.0) for t in all_trades)) / float(len(all_trades))
        except Exception:
            avg_r = 0.0

    base_capital = float(_safe_dict(policy.get("capital", {})).get("base_capital", 1) or 1)
    max_dd_pct = abs(float(max_dd_yen)) / float(base_capital) if base_capital > 0 else 9.0
    daylimit_days_pct = float(daylimit_days) / float(total_days) if total_days > 0 else 1.0

    metrics = {
        "avg_r": round(avg_r, 4),
        "max_dd_pct": round(max_dd_pct, 4),
        "max_consecutive_losses": int(max_consecutive_losses),
        "daylimit_days_pct": round(daylimit_days_pct, 4),
        "total_days": int(total_days),
        "total_trades": int(len(all_trades)),
        "thresholds": {
            "max_dd_pct": float(max_dd_pct_limit),
            "max_consecutive_losses": int(max_consecutive_losses_limit),
            "max_daylimit_days_pct": float(max_daylimit_days_pct_limit),
            "min_avg_r": float(min_avg_r_limit),
        },
    }

    # --- 判定 ---
    reasons: List[str] = []

    if max_dd_pct > max_dd_pct_limit:
        reasons.append(f"max_dd_pct exceeded: {max_dd_pct:.2%} > {max_dd_pct_limit:.2%}")

    if max_consecutive_losses > max_consecutive_losses_limit:
        reasons.append(f"max_consecutive_losses exceeded: {max_consecutive_losses} > {max_consecutive_losses_limit}")

    if daylimit_days_pct > max_daylimit_days_pct_limit:
        reasons.append(f"daylimit_days_pct exceeded: {daylimit_days_pct:.2%} > {max_daylimit_days_pct_limit:.2%}")

    if avg_r < min_avg_r_limit:
        reasons.append(f"avg_r too low: {avg_r:.3f} < {min_avg_r_limit:.3f}")

    decision = "GO" if not reasons else "NO_GO"

    return JudgeResult(
        decision=decision,
        reasons=reasons,
        metrics=metrics,
        mode=mode,
    )