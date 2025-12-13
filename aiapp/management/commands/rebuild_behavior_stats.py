# aiapp/management/commands/rebuild_behavior_stats.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from django.utils import timezone

from aiapp.models.behavior_stats import BehaviorStats


JST = dt_timezone(timedelta(hours=9))

# 常にこの3社で統合（あなたの方針）
BROKERS = ("rakuten", "sbi", "matsui")


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


def _to_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # "2025-12-13" / "2025-12-13T12:34:56" 両対応寄せ
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(JST).replace(tzinfo=None)
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except Exception:
        return None


def _clamp01(x: Any) -> float:
    v = _safe_float(x)
    if v is None:
        return 0.0
    return float(max(0.0, min(1.0, v)))


def _sum_eval_pl_all_brokers(d: Dict[str, Any]) -> Optional[float]:
    """
    楽天・SBI・松井の eval_pl_* を合算した PL を返す。
    - eval_pl_* が無い / 変換できない → 0 として扱う
    - 3社すべて 0 かつ None しかない → 0.0 を返す（label 側で弾く運用）
    """
    total = 0.0
    any_found = False
    for b in BROKERS:
        v = _safe_float(d.get(f"eval_pl_{b}"))
        if v is None:
            v = 0.0
        else:
            any_found = True
        total += float(v)
    if any_found:
        return float(total)
    # eval_pl_* が本当に何も無い古いデータの場合は None を返す
    return None


def _fallback_label_from_brokers(d: Dict[str, Any]) -> Optional[str]:
    """
    _combined_label が無い古いデータ向けフォールバック。
    3社の eval_label_* を見て、最も情報量があるものを返す。

    優先:
      - win/lose/flat が一つでもあれば、それを合成して
        (win と lose が混在 → mixed)
      - 全部 no_position なら skip
      - それ以外 → unknown
    """
    labels: List[str] = []
    for b in BROKERS:
        v = d.get(f"eval_label_{b}")
        if v is None:
            continue
        s = str(v).strip().lower()
        if not s:
            continue
        labels.append(s)

    if not labels:
        return None

    sset = set(labels)

    if sset <= {"no_position"}:
        return "skip"

    has_win = "win" in sset
    has_lose = "lose" in sset
    has_flat = "flat" in sset

    if has_win and has_lose:
        return "mixed"
    if has_win:
        return "win"
    if has_lose:
        return "lose"
    if has_flat and (sset <= {"flat"}):
        return "flat"

    # ここまで来たら pending/unknown/skip 等が混ざっている
    if "win" in sset:
        return "win"
    if "lose" in sset:
        return "lose"
    if "flat" in sset:
        return "flat"
    if "skip" in sset:
        return "skip"
    return "unknown"


def _compute_stability01_from_record(d: Dict[str, Any]) -> Optional[float]:
    """
    stability(0..1) を “1レコード” から計算。
    使うのは behavior jsonl に既に入っている feature_snapshot を優先。

    直感:
      - SLOPE と RET の符号一致 → 安定
      - RSI の極端（>=85 or <=15）→ 不安定
      - BB_Z が極端（|z|が大）→ 不安定（過熱/投げ）
      - VWAP_GAP_PCT が極端 → 不安定

    ※ あくまで「育つAI用の蓄積指標」で、完璧な学術モデルではなく
       “一貫したルールで積み上がる” ことを最優先にしている。
    """
    snap = d.get("feature_snapshot") or {}
    if not isinstance(snap, dict):
        snap = {}

    slope = _safe_float(snap.get("SLOPE_25") or d.get("slope_25"))
    ret20 = _safe_float(snap.get("RET_20") or d.get("ret_20"))
    rsi = _safe_float(snap.get("RSI14"))
    bbz = _safe_float(snap.get("BB_Z"))
    vwap_gap = _safe_float(snap.get("VWAP_GAP_PCT"))

    # どれも無い古いレコードは None
    if slope is None and ret20 is None and rsi is None and bbz is None and vwap_gap is None:
        return None

    score = 0.6  # 中立スタート

    # slope/ret の符号一致
    if slope is not None and ret20 is not None:
        if (slope >= 0 and ret20 >= 0) or (slope <= 0 and ret20 <= 0):
            score += 0.25
        else:
            score -= 0.25

    # RSIの極端/張り付き
    if rsi is not None:
        if rsi >= 85 or rsi <= 15:
            score -= 0.20
        elif 45 <= rsi <= 55:
            score -= 0.05  # ずっと50付近は「勢いが弱い」側で軽く減点
        else:
            score += 0.05

    # BB_Z（ボリンジャーz）の極端
    if bbz is not None:
        az = abs(float(bbz))
        if az >= 2.5:
            score -= 0.20
        elif az >= 1.8:
            score -= 0.10
        elif az <= 0.6:
            score += 0.05

    # VWAP gap
    if vwap_gap is not None:
        ag = abs(float(vwap_gap))
        if ag >= 2.0:
            score -= 0.15
        elif ag >= 1.0:
            score -= 0.08
        else:
            score += 0.03

    return float(max(0.0, min(1.0, score)))


def _compute_design_q01_from_record(d: Dict[str, Any]) -> Optional[float]:
    """
    design_q(0..1) を “1レコード” から計算。
    使うのは behavior jsonl に入っている design_rr / risk_atr / reward_atr を優先。

    直感:
      - RRが高いほど良い
      - risk_atrが極端に小さい/大きいのはダメ
      - reward_atrが極端に大きい（遠すぎ）もダメ
    """
    rr = _safe_float(d.get("design_rr"))
    risk_atr = _safe_float(d.get("risk_atr"))
    reward_atr = _safe_float(d.get("reward_atr"))

    # 欠損だらけの古いレコードは None
    if rr is None and risk_atr is None and reward_atr is None:
        return None

    score = 0.5  # 中立

    # RR
    if rr is not None:
        if rr >= 2.0:
            score += 0.35
        elif rr >= 1.2:
            score += 0.20
        elif rr >= 1.0:
            score += 0.10
        elif rr < 0.9:
            score -= 0.15
        else:
            score -= 0.05

    # risk_atr（近すぎ/遠すぎを嫌う）
    if risk_atr is not None:
        ra = float(risk_atr)
        if ra < 0.25:
            score -= 0.15
        elif ra < 0.40:
            score -= 0.08
        elif ra <= 2.5:
            score += 0.06
        else:
            score -= 0.12

    # reward_atr（遠すぎを嫌う）
    if reward_atr is not None:
        wa = float(reward_atr)
        if wa > 6.0:
            score -= 0.12
        elif wa > 4.0:
            score -= 0.06
        else:
            score += 0.03

    return float(max(0.0, min(1.0, score)))


@dataclass
class Rec:
    code: str
    mode_period: str
    mode_aggr: str
    source: str
    eval_label: Optional[str]
    eval_pl: Optional[float]
    run_date: Optional[datetime]
    stability01: Optional[float]
    design_q01: Optional[float]


def _load_latest_behavior_jsonl(
    *,
    days: int,
    include_live: bool = False,
) -> List[Rec]:
    """
    media/aiapp/behavior/latest_behavior.jsonl から、直近days日を読む。
    ここでは “all/all” で統合する（モード/証券会社無関係の育成用）。
    ※ mode_period/mode_aggr はJSONに無いので固定 all/all。

    ★重要：証券会社は常に 楽天・SBI・松井 を統合した世界で扱う。
      - label: _combined_label を最優先
      - pl   : eval_pl_rakuten + eval_pl_sbi + eval_pl_matsui
      - stability/design_q: レコードの feature_snapshot/design_* から計算して蓄積
    """
    behavior_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "behavior"
    latest_path = behavior_dir / "latest_behavior.jsonl"
    if not latest_path.exists():
        return []

    now = datetime.now(JST).replace(tzinfo=None)
    cutoff = now - timedelta(days=int(days))

    out: List[Rec] = []

    try:
        text = latest_path.read_text(encoding="utf-8")
    except Exception:
        return []

    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except Exception:
            continue

        src = str(d.get("source") or "").strip().lower()
        mode = str(d.get("mode") or "").strip().lower()

        # live を入れるかはオプション（基本は紙シミュ育成）
        if not include_live:
            if mode == "live":
                continue

        run_date = _to_date(str(d.get("run_date") or "") or None) or _to_date(str(d.get("ts") or "") or None)
        if run_date is not None and run_date < cutoff:
            continue

        code = str(d.get("code") or "").strip()
        if not code:
            continue
        if code.endswith(".T"):
            code = code[:-2]

        # ===== label =====
        label = d.get("_combined_label")
        if label is None:
            label = _fallback_label_from_brokers(d)
        if label is not None:
            label = str(label).strip().lower()

        # ===== pl（3社統合）=====
        plv = _sum_eval_pl_all_brokers(d)
        if plv is None:
            # どうしても無い古いデータ → 最後の砦（楽天だけ）
            plv = _safe_float(d.get("eval_pl_rakuten"))

        # ===== growable signals（0..1）=====
        stability01 = _compute_stability01_from_record(d)
        design_q01 = _compute_design_q01_from_record(d)

        out.append(
            Rec(
                code=code,
                mode_period="all",
                mode_aggr="all",
                source=src,
                eval_label=label,
                eval_pl=plv,
                run_date=run_date,
                stability01=stability01,
                design_q01=design_q01,
            )
        )

    return out


def _stars_rule(win_rate_pct: float, n: int, avg_pl: Optional[float]) -> int:
    """
    stars の基本ルール（いまは win_rate + n の安全設計）
    - n が少ないほど過信しない
    - avg_pl はプラス方向の補助に使う（ただし主役は勝率）
    """
    # データ不足は必ず⭐️1
    if n < 5:
        return 1

    wr = win_rate_pct

    # 平均PLが大きくマイナスなら上限を抑える（地雷抑止）
    if avg_pl is not None and avg_pl < -3000:
        if wr >= 60:
            return 3
        if wr >= 50:
            return 2
        return 1

    if wr >= 70:
        return 5
    if wr >= 60:
        return 4
    if wr >= 50:
        return 3
    if wr >= 45:
        return 2
    return 1


class Command(BaseCommand):
    help = "BehaviorStats を再集計してDBへ upsert（紙シミュ育成: all/all・楽天/SBI/松井統合・stability/design_q蓄積）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=90)
        parser.add_argument("--include-live", action="store_true", help="LIVE も統合に含める（基本はOFF）")
        parser.add_argument("--dry-run", action="store_true", help="DB更新せずプレビューだけ")
        parser.add_argument("--cleanup-zero", action="store_true", help="n=0 の既存行を掃除してから upsert")
        # 互換オプション（昔の呼び方でも落とさない）
        parser.add_argument("--broker", type=str, default=None)
        parser.add_argument("--mode_period", type=str, default=None)
        parser.add_argument("--mode_aggr", type=str, default=None)

    def handle(self, *args, **opts) -> None:
        days = int(opts.get("days") or 90)
        include_live = bool(opts.get("include_live") or False)
        dry_run = bool(opts.get("dry_run") or False)
        cleanup_zero = bool(opts.get("cleanup_zero") or False)

        # 互換オプションは “無視” して all/all に統合する（あなたの方針）
        recs = _load_latest_behavior_jsonl(days=days, include_live=include_live)

        if not recs:
            self.stdout.write(self.style.WARNING("[rebuild_behavior_stats] 対象レコードがありません。"))
            return

        # code ごと集計
        bucket: Dict[str, Dict[str, Any]] = {}
        for r in recs:
            b = bucket.setdefault(
                r.code,
                {
                    "n": 0,
                    "win": 0,
                    "lose": 0,
                    "flat": 0,
                    "pls": [],
                    "stabs": [],   # stability01
                    "dqs": [],     # design_q01
                },
            )

            # win/lose/flat 以外は “学習対象外”
            if r.eval_label in ("win", "lose", "flat"):
                b["n"] += 1
                if r.eval_label == "win":
                    b["win"] += 1
                elif r.eval_label == "lose":
                    b["lose"] += 1
                else:
                    b["flat"] += 1

                if r.eval_pl is not None:
                    b["pls"].append(float(r.eval_pl))

                if r.stability01 is not None:
                    b["stabs"].append(float(_clamp01(r.stability01)))

                if r.design_q01 is not None:
                    b["dqs"].append(float(_clamp01(r.design_q01)))

        # n>0 の銘柄だけを本体にする（n=0 はDBに作らない）
        bucket = {code: st for code, st in bucket.items() if int(st.get("n") or 0) > 0}
        unique_codes = len(bucket)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("===== rebuild_behavior_stats preview (ALL combined) ====="))
        self.stdout.write(f"  days={days}  include_live={include_live}  dry_run={dry_run}  cleanup_zero={cleanup_zero}")
        self.stdout.write("  brokers=rakuten+sbi+matsui (always)")
        self.stdout.write(f"  unique_codes(n>0)={unique_codes}")

        if unique_codes == 0:
            self.stdout.write(self.style.WARNING("[rebuild_behavior_stats] n>0 の銘柄がありません（pending/unknown/skip ばかりの可能性）。"))
            return

        # 表示用（n降順）
        def _avg(xs: List[float]) -> Optional[float]:
            if not xs:
                return None
            return float(np.mean(xs))

        def _std(xs: List[float]) -> Optional[float]:
            if not xs or len(xs) < 2:
                return None
            return float(np.std(xs, ddof=0))

        preview_rows: List[Tuple[str, int, float, Optional[float], Optional[float], int, Optional[float], Optional[float]]] = []
        for code, st in bucket.items():
            n = int(st["n"])
            win = int(st["win"])
            wr = (100.0 * win / n) if n > 0 else 0.0
            avg_pl = _avg(st["pls"])
            std_pl = _std(st["pls"])
            stars = _stars_rule(wr, n, avg_pl)

            stab01 = _avg(st.get("stabs") or [])
            dq01 = _avg(st.get("dqs") or [])

            preview_rows.append((code, n, wr, avg_pl, std_pl, stars, stab01, dq01))

        preview_rows.sort(key=lambda x: x[1], reverse=True)

        for code, n, wr, avg_pl, std_pl, stars, stab01, dq01 in preview_rows[:30]:
            ap = 0.0 if avg_pl is None else avg_pl
            ss = "-" if stab01 is None else f"{stab01:.2f}"
            dq = "-" if dq01 is None else f"{dq01:.2f}"
            self.stdout.write(
                f"  {code} [all/all]: n={n:3d} win_rate={wr:5.1f}% avg_pl={ap:7.1f} -> stars={stars}  stab={ss}  design_q={dq}"
            )

        if dry_run:
            self.stdout.write(self.style.WARNING("[rebuild_behavior_stats] dry-run のため DB 更新は行いません。"))
            return

        # DB upsert
        now = timezone.now()
        upserted = 0

        with transaction.atomic():
            if cleanup_zero:
                # all/all の n=0 を掃除（これまでの “未来評価→空” の残骸を消す）
                deleted, _ = BehaviorStats.objects.filter(mode_period="all", mode_aggr="all", n=0).delete()
                self.stdout.write(self.style.WARNING(f"[rebuild_behavior_stats] cleanup_zero: deleted={deleted}"))

            for code, n, wr, avg_pl, std_pl, stars, stab01, dq01 in preview_rows:
                win = int(bucket[code]["win"])
                lose = int(bucket[code]["lose"])
                flat = int(bucket[code]["flat"])

                BehaviorStats.objects.update_or_create(
                    code=str(code),
                    mode_period="all",
                    mode_aggr="all",
                    defaults={
                        "stars": int(stars),
                        "n": int(n),
                        "win": int(win),
                        "lose": int(lose),
                        "flat": int(flat),
                        "win_rate": float(round(wr, 1)),
                        "avg_pl": float(avg_pl) if avg_pl is not None else None,
                        "std_pl": float(std_pl) if std_pl is not None else None,
                        "stability": float(stab01) if stab01 is not None else None,
                        "design_q": float(dq01) if dq01 is not None else None,
                        "window_days": int(days),
                        "updated_at": now,
                    },
                )
                upserted += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"[rebuild_behavior_stats] DB更新完了: {upserted} 件 upsert（n>0のみ）"))