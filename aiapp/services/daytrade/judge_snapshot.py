# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/judge_snapshot.py

これは何？
- Judge（GO/NO-GO）の結果を、毎回ファイルに保存するためのユーティリティ。
- 自動売買は「後から検証できるログ」が命。
  いつ、どのpolicyで、どの結果だったかを残す。

保存先（重要）
- <project_root>/media/aiapp/daytrade/judge/YYYYMMDD/judge.json

※ project_root は settings.BASE_DIR を基準にする。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

from django.conf import settings


def _project_root() -> Path:
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir is None:
        raise RuntimeError("Django settings.BASE_DIR is not set.")
    return Path(base_dir).resolve()


def judge_snapshot_dir(d: date) -> Path:
    ymd = d.strftime("%Y%m%d")
    return _project_root() / "media" / "aiapp" / "daytrade" / "judge" / ymd


def save_judge_snapshot(
    d: date,
    policy: Dict[str, Any],
    judge_result: Any,
    extra: Optional[Dict[str, Any]] = None,
) -> Path:
    """
    Judge結果をJSONで保存する。

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

    Returns
    -------
    Path : 保存した judge.json のパス
    """
    out_dir = judge_snapshot_dir(d)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "saved_at": datetime.now().isoformat(),
        "date": d.isoformat(),
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