# aiapp/views/behavior.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict, Counter

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone


def _parse_float(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except Exception:
        return None


# ================================
# 重複判定キー
# ================================
def _dup_key(rec: Dict[str, Any]) -> Tuple:
    """
    同じ日・同じ銘柄・同じentry・数量が同じなら1件とみなす。
    """
    code = rec.get("code")
    mode = rec.get("mode")
    price_date = rec.get("price_date")

    entry = rec.get("entry")
    qty_r = rec.get("qty_rakuten")
    qty_m = rec.get("qty_matsui")

    # entry は小数第2位まで丸める
    try:
        entry_r = round(float(entry), 2)
    except Exception:
        entry_r = None

    # 数量は整数として扱う
    try:
        qty_r_i = int(float(qty_r)) if qty_r else 0
    except Exception:
        qty_r_i = 0
    try:
        qty_m_i = int(float(qty_m)) if qty_m else 0
    except Exception:
        qty_m_i = 0

    return (
        mode,
        code,
        price_date,
        entry_r,
        qty_r_i,
        qty_m_i,
    )


# ================================
# 行動データセットの読み込み
# ================================
def _load_behavior_dataset() -> List[Dict[str, Any]]:
    """latest_behavior.jsonl を読み込む"""
    behavior_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "behavior"
    latest = behavior_dir / "latest_behavior.jsonl"
    if not latest.exists():
        return []

    records: List[Dict[str, Any]] = []
    text = latest.read_text("utf-8")
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except Exception:
            continue
        records.append(rec)

    return records


# ================================
# Webダッシュボードビュー
# ================================
@login_required
def behavior_dashboard(request):
    """
    CLI版 behavior_stats と同じ中身をスマホ画面に表示するビュー。
    """

    records = _load_behavior_dataset()

    # -------------------------
    # 重複除外
    # -------------------------
    uniq_map: Dict[Tuple, Dict[str, Any]] = {}
    for r in records:
        key = _dup_key(r)
        if key not in uniq_map:
            uniq_map[key] = r

    uniq = list(uniq_map.values())

    # -------------------------
    # 集計準備
    # -------------------------
    total = len(uniq)

    # モード別
    mode_counts = Counter()
    for r in uniq:
        mode_counts[r.get("mode")] += 1

    # 勝敗ラベル（楽天）
    pl_counts_r = Counter()
    for r in uniq:
        lb = r.get("eval_label_rakuten") or "none"
        pl_counts_r[lb] += 1

    # 勝敗ラベル（松井）
    pl_counts_m = Counter()
    for r in uniq:
        lb = r.get("eval_label_matsui") or "none"
        pl_counts_m[lb] += 1

    # -------------------------
    # sector 別
    # -------------------------
    sector_stats: Dict[str, Dict[str, int]] = {}
    for r in uniq:
        sec = r.get("sector") or "(未分類)"

        if sec not in sector_stats:
            sector_stats[sec] = {"trials": 0, "wins": 0}

        label = r.get("eval_label_rakuten")

        sector_stats[sec]["trials"] += 1
        if label == "win":
            sector_stats[sec]["wins"] += 1

    # -------------------------
    # TOP5 win / lose（楽天）
    # -------------------------
    sorted_r = sorted(
        uniq,
        key=lambda x: (_parse_float(x.get("eval_pl_rakuten")) or 0),
        reverse=True,
    )

    top_win = [r for r in sorted_r if (r.get("eval_pl_rakuten") or 0) > 0][:5]

    sorted_l = sorted(
        uniq,
        key=lambda x: (_parse_float(x.get("eval_pl_rakuten")) or 0),
    )
    top_lose = [r for r in sorted_l if (r.get("eval_pl_rakuten") or 0) < 0][:5]

    ctx = {
        "total": total,
        "mode_counts": mode_counts,
        "pl_counts_r": pl_counts_r,
        "pl_counts_m": pl_counts_m,
        "sector_stats": sector_stats,
        "top_win": top_win,
        "top_lose": top_lose,
    }

    return render(request, "aiapp/behavior_dashboard.html", ctx)