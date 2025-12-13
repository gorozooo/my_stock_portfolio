# aiapp/management/commands/rebuild_behavior_stats.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from math import log
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


def _clamp01(x: float) -> float:
    try:
        x = float(x)
    except Exception:
        return 0.5
    if not np.isfinite(x):
        return 0.5
    return float(min(1.0, max(0.0, x)))


def _sigmoid(x: float) -> float:
    try:
        return float(1.0 / (1.0 + np.exp(-float(x))))
    except Exception:
        return 0.5


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


def _extract_rr_design_hint(d: Dict[str, Any]) -> Optional[float]:
    """
    design_q（設計品質）を作るためのヒントをJSONから拾う。
    取れたら「RR（reward/risk）」相当を返す。

    対応（あるものだけ使う）:
      - rr / RR
      - entry,tp,sl
      - entry,tp,sl,atr があってもRRは atr不要なので entry/tp/sl 優先
    """
    rr = _safe_float(d.get("rr"))
    if rr is None:
        rr = _safe_float(d.get("RR"))
    if rr is not None and rr > 0:
        return float(rr)

    e = _safe_float(d.get("entry"))
    t = _safe_float(d.get("tp"))
    s = _safe_float(d.get("sl"))
    if e is None or t is None or s is None:
        return None
    reward = float(t) - float(e)
    risk = float(e) - float(s)
    if reward <= 0 or risk <= 0:
        return None
    return float(reward / risk)


def _compute_stability(
    *,
    n: int,
    win: int,
    lose: int,
    flat: int,
    avg_pl: Optional[float],
    std_pl: Optional[float],
) -> float:
    """
    stability = 0..1（“再現性/ブレ耐性”）
    ねらい:
      - 勝ってる/負けてる以前に「ブレが少ない」ほど上げる
      - 試行数 n が少ないときは必ず 0.50 に寄せる（過信防止）

    要素:
      1) outcomeのエントロピー（win/lose/flat の偏りがあるほど安定）
      2) PLの分散（std_pl が小さいほど安定）
    """
    if n <= 0:
        return 0.50

    # nゲート（少数データは中立へ）
    r = _sigmoid((n - 8) / 3.0)  # 0..1

    # --- (1) outcome entropy ---
    w = max(0, int(win))
    l = max(0, int(lose))
    f = max(0, int(flat))
    tot = w + l + f
    if tot <= 0:
        entropy_norm = 1.0
    else:
        ps = []
        for x in (w, l, f):
            if x > 0:
                ps.append(x / tot)
        # entropy in [0, log(3)]
        ent = 0.0
        for p in ps:
            ent -= p * log(p)
        ent_max = log(3.0)
        entropy_norm = (ent / ent_max) if ent_max > 0 else 1.0  # 0..1
    # 偏りがある=entropy小=安定が高い
    stab_outcome = 1.0 - float(entropy_norm)  # 0..1

    # --- (2) PL stability ---
    ap = float(avg_pl) if (avg_pl is not None and np.isfinite(avg_pl)) else 0.0
    sp = float(std_pl) if (std_pl is not None and np.isfinite(std_pl)) else None

    if sp is None:
        stab_pl = 0.50
    else:
        # スケールは “だいたいの許容ブレ” を作る（銘柄でPL水準が違うので abs(avg_pl)も少し反映）
        scale = max(1200.0, 0.5 * abs(ap) + 1200.0)
        stab_pl = 1.0 / (1.0 + (sp / scale))  # 0..1-ish
        stab_pl = _clamp01(stab_pl)

    base = 0.60 * stab_outcome + 0.40 * stab_pl
    base = _clamp01(base)

    # 少数データは中立へブレンド
    blended = (1.0 - r) * 0.50 + r * base
    return _clamp01(blended)


def _compute_design_q(
    *,
    n: int,
    avg_pl: Optional[float],
    rr_vals: List[float],
) -> float:
    """
    design_q = 0..1（“設計品質”）
    ねらい:
      - Entry/TP/SL設計が良い（RRが良い）ほど上げる
      - 結果が悪い（avg_plが極端にマイナス）なら少し抑える
      - rrデータが無い場合は中立0.50
      - nが少ない場合は中立へ寄せる
    """
    if n <= 0:
        return 0.50

    # nゲート（少数データは中立へ）
    r_gate = _sigmoid((n - 8) / 3.0)

    # RR由来（中心 1.0 を境に上げる）
    rr_score = None
    vals = [float(x) for x in rr_vals if x is not None and np.isfinite(x) and x > 0]
    if vals:
        rr_med = float(np.median(vals))
        rr_score = _sigmoid((rr_med - 1.0) * 2.2)  # rr=1 →0.5, rr=1.5→~0.75, rr=2→~0.90
        rr_score = _clamp01(rr_score)

    if rr_score is None:
        base = 0.50
    else:
        base = rr_score

    # 平均PLで微調整（設計は良いが結果が悪い“地雷”を軽く抑える）
    ap = float(avg_pl) if (avg_pl is not None and np.isfinite(avg_pl)) else 0.0
    pl_adj = _sigmoid(ap / 3000.0)  # ap=0→0.5, ap=+3000→~0.73, ap=-3000→~0.27
    # 調整幅は小さく（設計を主役にする）
    base2 = _clamp01(0.80 * base + 0.20 * pl_adj)

    # 少数データは中立へブレンド
    blended = (1.0 - r_gate) * 0.50 + r_gate * base2
    return _clamp01(blended)


@dataclass
class Rec:
    code: str
    mode_period: str
    mode_aggr: str
    source: str
    eval_label: Optional[str]
    eval_pl: Optional[float]
    run_date: Optional[datetime]
    rr_hint: Optional[float]


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

        # ===== design hint（RR）=====
        rr_hint = _extract_rr_design_hint(d)

        out.append(
            Rec(
                code=code,
                mode_period="all",
                mode_aggr="all",
                source=src,
                eval_label=label,
                eval_pl=plv,
                run_date=run_date,
                rr_hint=rr_hint,
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
    help = "BehaviorStats を再集計してDBへ upsert（紙シミュ育成: all/all・楽天/SBI/松井統合）"

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
                    "rrs": [],
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
                if r.rr_hint is not None and np.isfinite(float(r.rr_hint)) and float(r.rr_hint) > 0:
                    b["rrs"].append(float(r.rr_hint))

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

        preview_rows: List[Tuple[str, int, float, Optional[float], Optional[float], float, float, int]] = []
        for code, st in bucket.items():
            n = int(st["n"])
            win = int(st["win"])
            lose = int(st["lose"])
            flat = int(st["flat"])

            wr = (100.0 * win / n) if n > 0 else 0.0
            avg_pl = _avg(st["pls"])
            std_pl = _std(st["pls"])

            stability = _compute_stability(
                n=n,
                win=win,
                lose=lose,
                flat=flat,
                avg_pl=avg_pl,
                std_pl=std_pl,
            )

            design_q = _compute_design_q(
                n=n,
                avg_pl=avg_pl,
                rr_vals=st.get("rrs") or [],
            )

            stars = _stars_rule(wr, n, avg_pl)
            preview_rows.append((code, n, wr, avg_pl, std_pl, stability, design_q, stars))

        preview_rows.sort(key=lambda x: x[1], reverse=True)

        for code, n, wr, avg_pl, std_pl, stability, design_q, stars in preview_rows[:30]:
            ap = 0.0 if avg_pl is None else avg_pl
            stv = float(stability)
            dqv = float(design_q)
            self.stdout.write(
                f"  {code} [all/all]: n={n:3d} win_rate={wr:5.1f}% avg_pl={ap:7.1f} "
                f"stability={stv:.2f} design_q={dqv:.2f} -> stars={stars}"
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

            for code, n, wr, avg_pl, std_pl, stability, design_q, stars in preview_rows:
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
                        "stability": float(_clamp01(stability)),
                        "design_q": float(_clamp01(design_q)),
                        "window_days": int(days),
                        "updated_at": now,
                    },
                )
                upserted += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"[rebuild_behavior_stats] DB更新完了: {upserted} 件 upsert（n>0のみ）"))