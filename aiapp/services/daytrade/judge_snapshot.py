# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/judge_snapshot.py

これは何？
- Judge（GO/NO-GO）の結果を、毎回ファイルに保存するためのユーティリティ。
- 自動売買は「後から検証できるログ」が命。
  いつ、どのpolicyで、どの結果だったかを残す。

保存先（重要）
- <project_root>/media/aiapp/daytrade/judge/YYYYMMDD/{mode}/judge.json
  mode は "dev" or "prod"

※ project_root は settings.BASE_DIR を基準にする。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

from django.conf import settings


def _project_root() -> Path:
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir is None:
        raise RuntimeError("Django settings.BASE_DIR is not set.")
    return Path(base_dir).resolve()


def _normalize_mode(mode: Optional[str]) -> str:
    m = (mode or "prod").strip().lower()
    return m if m in ("dev", "prod") else "prod"


def judge_snapshot_dir(d: date, mode: str = "prod") -> Path:
    ymd = d.strftime("%Y%m%d")
    m = _normalize_mode(mode)
    return _project_root() / "media" / "aiapp" / "daytrade" / "judge" / ymd / m


def save_judge_snapshot(
    d: date,
    policy: Dict[str, Any],
    judge_result: Any,
    extra: Optional[Dict[str, Any]] = None,
    mode: str = "prod",
) -> Path:
    """
    Judge結果をJSONで保存する（dev/prodで保存先を分ける）。

    Parameters
    ----------
    d : date
        どの日の判定か（通常は当日）
    policy : dict
        load_policy_yaml().policy
    judge_result : JudgeResult
        judge_backtest_results の戻り値
    extra : dict
        任意の追加情報（例：バックテスト期間、銘柄数、実行環境など）
    mode : str
        "dev" or "prod"

    Returns
    -------
    Path : 保存した judge.json のパス
    """
    m = _normalize_mode(mode)
    out_dir = judge_snapshot_dir(d, m)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "saved_at": datetime.now().isoformat(),
        "date": d.isoformat(),
        "mode": m,  # ★明示的に保存
        "policy_id": (policy.get("meta") or {}).get("policy_id"),
        "decision": getattr(judge_result, "decision", None),
        "reasons": getattr(judge_result, "reasons", None),
        "metrics": getattr(judge_result, "metrics", None),
        "policy": policy,
        "extra": extra or {},
    }

    out_path = out_dir / "judge.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path