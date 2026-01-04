# aiapp/management/commands/behavior_stats.py
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
behavior_stats

latest_behavior.jsonl を読み込んで、行動 × 結果 データのサマリを表示する。

ポイント:
- 同じ日付・同じ銘柄・同じエントリー条件（entry, qty_rakuten, qty_matsui, mode）が
  完全に同じレコードは「重複」とみなし、1件として扱う。
  → これで、同じシミュレを何度か打った場合でも、統計上は1カウントになる。

使い方:
    python manage.py behavior_stats
    python manage.py behavior_stats --user 1
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser


@dataclass
class BehaviorRec:
    ts: Optional[str]
    user_id: Optional[int]
    mode: Optional[str]
    code: Optional[str]
    name: Optional[str]
    sector: Optional[str]
    price_date: Optional[str]
    entry: Optional[float]
    qty_rakuten: Optional[float]
    qty_matsui: Optional[float]
    eval_label_rakuten: Optional[str]
    eval_pl_rakuten: Optional[float]
    eval_label_matsui: Optional[str]
    eval_pl_matsui: Optional[float]


def _safe_float(x: Any) -> Optional[float]:
    if x in (None, "", "null"):
        return None
    try:
        v = float(x)
        if not np.isfinite(v):
            return None
        return v
    except Exception:
        return None


def _safe_int(x: Any) -> Optional[int]:
    if x in (None, "", "null"):
        return None
    try:
        return int(x)
    except Exception:
        return None


def _load_latest_behavior(
    *,
    user_filter: Optional[int] = None,
) -> List[BehaviorRec]:
    """
    /media/aiapp/behavior/latest_behavior.jsonl を読み込んで BehaviorRec のリストを返す。

    ここで「重複レコードの除外」も行う:
      - key = (mode, code, price_date, round(entry,2), round(qty_rakuten,0), round(qty_matsui,0))
      が同じものは 1件にまとめる。
    """
    behavior_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "behavior"
    latest_path = behavior_dir / "latest_behavior.jsonl"

    recs: List[BehaviorRec] = []
    seen_keys: set[tuple] = set()

    if not latest_path.exists():
        return recs

    try:
        text = latest_path.read_text(encoding="utf-8")
    except Exception:
        return recs

    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except Exception:
            continue

        uid = _safe_int(d.get("user_id"))
        if user_filter is not None and uid != user_filter:
            continue

        mode = (d.get("mode") or "").lower() if isinstance(d.get("mode"), str) else None
        code = str(d.get("code") or "") if d.get("code") is not None else None
        price_date = str(d.get("price_date") or "") if d.get("price_date") is not None else None
        entry = _safe_float(d.get("entry"))
        qty_r = _safe_float(d.get("qty_rakuten"))
        qty_m = _safe_float(d.get("qty_matsui"))

        # 重複判定キー
        key = (
            mode or "",
            code or "",
            price_date or "",
            round(entry if entry is not None else 0.0, 2),
            round(qty_r if qty_r is not None else 0.0, 0),
            round(qty_m if qty_m is not None else 0.0, 0),
        )

        # すでに同じ条件があればスキップ
        if key in seen_keys:
            continue
        seen_keys.add(key)

        rec = BehaviorRec(
            ts=d.get("ts"),
            user_id=uid,
            mode=mode,
            code=code,
            name=str(d.get("name") or "") if d.get("name") is not None else None,
            sector=str(d.get("sector") or "") if d.get("sector") is not None else None,
            price_date=price_date or None,
            entry=entry,
            qty_rakuten=qty_r,
            qty_matsui=qty_m,
            eval_label_rakuten=str(d.get("eval_label_rakuten")) if d.get("eval_label_rakuten") is not None else None,
            eval_pl_rakuten=_safe_float(d.get("eval_pl_rakuten")),
            eval_label_matsui=str(d.get("eval_label_matsui")) if d.get("eval_label_matsui") is not None else None,
            eval_pl_matsui=_safe_float(d.get("eval_pl_matsui")),
        )
        recs.append(rec)

    return recs


def _count_labels(
    recs: List[BehaviorRec],
    broker: str,
) -> Dict[str, int]:
    """
    broker = "rakuten" / "matsui"
    ラベルの件数をカウントして dict で返す。
    """
    counts = {
        "win": 0,
        "lose": 0,
        "flat": 0,
        "no_position": 0,
        "none": 0,  # ラベル自体がない
    }

    for r in recs:
        if broker == "rakuten":
            label = r.eval_label_rakuten
            qty = r.qty_rakuten or 0.0
        else:
            label = r.eval_label_matsui
            qty = r.qty_matsui or 0.0

        # そもそも数量 0 or None は "no_position" 扱い
        if qty == 0 or qty is None:
            counts["no_position"] += 1
            continue

        if label is None:
            counts["none"] += 1
        elif label in counts:
            counts[label] += 1
        else:
            # 想定外ラベル
            counts["none"] += 1

    return counts


def _calc_ratio(n: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(100.0 * n / total, 1)


class Command(BaseCommand):
    """
    行動データセット（latest_behavior.jsonl）のサマリを表示するコマンド。
    """

    help = "AI行動データセットの勝率・PL・sector別サマリを表示する（重複レコードは自動で間引く）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--user",
            type=int,
            default=None,
            help="特定ユーザーIDに絞りたい場合",
        )

    def handle(self, *args, **options) -> None:
        user_filter: Optional[int] = options["user"]

        recs = _load_latest_behavior(user_filter=user_filter)

        if not recs:
            self.stdout.write(
                self.style.WARNING("[behavior_stats] latest_behavior.jsonl に対象レコードがありません。")
            )
            return

        total = len(recs)
        modes = {"live": 0, "demo": 0, "other": 0}
        for r in recs:
            m = (r.mode or "").lower()
            if m in ("live", "demo"):
                modes[m] += 1
            else:
                modes["other"] += 1

        # ==========================
        # 全体サマリ
        # ==========================
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("===== 行動データセット サマリ（重複除外後） ====="))
        if user_filter is not None:
            self.stdout.write(f"  ユーザーID: {user_filter}")
        self.stdout.write(f"  レコード数: {total} 件")
        self.stdout.write(
            f"  モード内訳: LIVE={modes['live']} / DEMO={modes['demo']} / その他={modes['other']}"
        )

        # ==========================
        # 楽天 / 松井 の勝敗カウント
        # ==========================
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("===== 勝敗サマリ（楽天） ====="))
        counts_r = _count_labels(recs, broker="rakuten")
        effective_r = total  # 分母はとりあえず全レコード
        for k in ["win", "lose", "flat", "no_position", "none"]:
            v = counts_r[k]
            self.stdout.write(
                f"  {k:12s}: {v:4d} 件 ({_calc_ratio(v, effective_r):4.1f} %)"
            )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("===== 勝敗サマリ（松井） ====="))
        counts_m = _count_labels(recs, broker="matsui")
        effective_m = total
        for k in ["win", "lose", "flat", "no_position", "none"]:
            v = counts_m[k]
            self.stdout.write(
                f"  {k:12s}: {v:4d} 件 ({_calc_ratio(v, effective_m):4.1f} %)"
            )

        # ==========================
        # sector 別 勝率（楽天）
        # ==========================
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("===== sector 別 勝率（楽天） ====="))

        # sector -> [win数, 試行数]
        sector_stats: Dict[str, Dict[str, int]] = {}
        for r in recs:
            sector = r.sector or "(未分類)"
            d = sector_stats.setdefault(sector, {"win": 0, "total": 0})
            # 数量0は除外
            if not r.qty_rakuten or r.qty_rakuten == 0:
                continue
            if r.eval_label_rakuten in ("win", "lose", "flat"):
                d["total"] += 1
                if r.eval_label_rakuten == "win":
                    d["win"] += 1

        # 件数多い順に並べる
        sorted_sectors = sorted(
            sector_stats.items(),
            key=lambda kv: kv[1]["total"],
            reverse=True,
        )

        for sector, st in sorted_sectors:
            total_s = st["total"]
            win_s = st["win"]
            rate = _calc_ratio(win_s, total_s) if total_s > 0 else 0.0
            self.stdout.write(
                f"  {sector}: 試行={total_s:3d} / 勝ち={win_s:3d} ({rate:4.1f} %)"
            )

        # ==========================
        # TOP 勝ち / 負け（楽天）
        # ==========================
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("===== TOP5 勝ちトレード（楽天, PL順） ====="))

        win_recs: List[BehaviorRec] = []
        for r in recs:
            if r.eval_pl_rakuten is not None and r.eval_pl_rakuten > 0:
                win_recs.append(r)

        win_recs.sort(key=lambda r: r.eval_pl_rakuten or 0.0, reverse=True)
        for r in win_recs[:5]:
            self.stdout.write(
                f"  {r.code or ''} {r.name or ''}  PL={r.eval_pl_rakuten:.0f}  mode={r.mode or ''}  ts={r.ts or ''}"
            )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("===== TOP5 負けトレード（楽天, PL順） ====="))

        lose_recs: List[BehaviorRec] = []
        for r in recs:
            if r.eval_pl_rakuten is not None and r.eval_pl_rakuten < 0:
                lose_recs.append(r)

        lose_recs.sort(key=lambda r: r.eval_pl_rakuten or 0.0)  # 小さい順
        for r in lose_recs[:5]:
            self.stdout.write(
                f"  {r.code or ''} {r.name or ''}  PL={r.eval_pl_rakuten:.0f}  mode={r.mode or ''}  ts={r.ts or ''}"
            )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("[behavior_stats] 完了"))