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


def _clip01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)


def _safe_float(x: Any) -> Optional[float]:
    if x in (None, "", "null"):
        return None
    try:
        v = float(x)
        if not np.isfinite(v):
            return None
        return float(v)
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


def _first_float(d: Dict[str, Any], keys: List[str]) -> Optional[float]:
    for k in keys:
        v = _safe_float(d.get(k))
        if v is not None:
            return v
    return None


@dataclass
class Rec:
    code: str
    mode_period: str
    mode_aggr: str
    source: str
    eval_label: Optional[str]
    eval_pl: Optional[float]
    run_date: Optional[datetime]
    # --- design / stability 用（あれば使う。無ければ None） ---
    design_rr: Optional[float]
    risk_atr: Optional[float]
    reward_atr: Optional[float]


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

        # ===== design/stability の素材 =====
        # JSON内のキーが将来変わっても拾えるように「候補キー」で拾う
        design_rr = _first_float(d, ["design_rr", "design_rrr", "rr_design", "design_rratio"])
        risk_atr = _first_float(d, ["risk_atr", "design_risk_atr", "atr_risk"])
        reward_atr = _first_float(d, ["reward_atr", "design_reward_atr", "atr_reward"])

        out.append(
            Rec(
                code=code,
                mode_period="all",
                mode_aggr="all",
                source=src,
                eval_label=label,
                eval_pl=plv,
                run_date=run_date,
                design_rr=design_rr,
                risk_atr=risk_atr,
                reward_atr=reward_atr,
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


def _calc_stability(
    *,
    n: int,
    win_rate_pct: float,
    avg_pl: Optional[float],
    std_pl: Optional[float],
) -> Optional[float]:
    """
    stability: 0..1（「再現性がありそう」ほど高い）
    - 勝率が極端に低い/高い
    - PLのバラつきが小さい
    - 試行数が多い
    を総合して雑に一本化（学習が育つほど自然に上がる）

    ※ “おすすめ順” をブレにくくする目的なので、計算は安定重視（単純・決定的）
    """
    if n <= 0:
        return None

    # 試行数（育ってるほど信頼できる）
    # 20回でほぼ頭打ち
    n_factor = float(np.log1p(n) / np.log1p(20.0))
    n_factor = _clip01(n_factor)

    # 勝率（0..1）
    wr_factor = _clip01(float(win_rate_pct) / 100.0)

    # ばらつき（小さいほど良い）
    # avg_pl が小さいと割り算が暴れるので 5000 を床にする
    base = max(5000.0, float(abs(avg_pl)) if avg_pl is not None else 5000.0)
    if std_pl is None:
        vol_factor = 0.5  # 情報不足は中立
    else:
        ratio = float(std_pl) / base
        vol_factor = 1.0 / (1.0 + ratio)  # ratio=0 ->1, ratio=1 ->0.5, ratio=2 ->0.33...
        vol_factor = _clip01(vol_factor)

    # 合成（勝率と安定性を主役、試行数で下支え）
    st = 0.40 * wr_factor + 0.35 * vol_factor + 0.25 * n_factor
    return _clip01(st)


def _calc_design_q(
    *,
    rr_list: List[float],
    risk_atr_list: List[float],
    reward_atr_list: List[float],
) -> Optional[float]:
    """
    design_q: 0..1（「設計がまともそう」ほど高い）
    - design_rr が取れるならそれを優先
    - 無ければ reward_atr / risk_atr で RR を推定
    - 大きすぎるRRは上限で丸める（過剰最適化に見えるので）
    """
    rr_values: List[float] = []

    # まず design_rr
    for rr in rr_list:
        if rr is None:
            continue
        if not np.isfinite(rr):
            continue
        if rr <= 0:
            continue
        rr_values.append(float(rr))

    # 無ければ reward_atr / risk_atr
    if not rr_values and risk_atr_list and reward_atr_list:
        m = min(len(risk_atr_list), len(reward_atr_list))
        for i in range(m):
            r = float(risk_atr_list[i])
            w = float(reward_atr_list[i])
            if not np.isfinite(r) or not np.isfinite(w):
                continue
            if r <= 0:
                continue
            rr_values.append(w / r)

    if not rr_values:
        return None

    rr_avg = float(np.mean(rr_values))

    # rr=1 で 0.5、rr=2 で 0.8、rr>=3 は 1.0 に近づく（上限丸め）
    # 単純でブレないカーブにする
    rr_norm = rr_avg / 3.0
    rr_norm = _clip01(rr_norm)

    # 「RRが高い＝良い」だけじゃないので、過剰な値は丸める（上のclipでOK）
    return rr_norm


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
                    "risk_atrs": [],
                    "reward_atrs": [],
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

                if r.design_rr is not None:
                    b["rrs"].append(float(r.design_rr))
                if r.risk_atr is not None:
                    b["risk_atrs"].append(float(r.risk_atr))
                if r.reward_atr is not None:
                    b["reward_atrs"].append(float(r.reward_atr))

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

        # 表示/計算用
        def _avg(xs: List[float]) -> Optional[float]:
            if not xs:
                return None
            return float(np.mean(xs))

        def _std(xs: List[float]) -> Optional[float]:
            if not xs or len(xs) < 2:
                return None
            return float(np.std(xs, ddof=0))

        # preview_rows:
        # (code, n, wr, avg_pl, std_pl, stars, stability, design_q)
        preview_rows: List[Tuple[str, int, float, Optional[float], Optional[float], int, Optional[float], Optional[float]]] = []

        for code, st in bucket.items():
            n = int(st["n"])
            win = int(st["win"])
            wr = (100.0 * win / n) if n > 0 else 0.0

            avg_pl = _avg(st["pls"])
            std_pl = _std(st["pls"])

            stars = _stars_rule(wr, n, avg_pl)

            stability = _calc_stability(n=n, win_rate_pct=wr, avg_pl=avg_pl, std_pl=std_pl)
            design_q = _calc_design_q(
                rr_list=st["rrs"],
                risk_atr_list=st["risk_atrs"],
                reward_atr_list=st["reward_atrs"],
            )

            preview_rows.append((code, n, wr, avg_pl, std_pl, stars, stability, design_q))

        # ===== おすすめ順 =====
        # stars → stability → design_q → n → win_rate → avg_pl の順で安定ソート
        def _k(row: Tuple[str, int, float, Optional[float], Optional[float], int, Optional[float], Optional[float]]):
            code, n, wr, avg_pl, std_pl, stars, stability, design_q = row
            stv = float(stability) if stability is not None else -1.0
            dq = float(design_q) if design_q is not None else -1.0
            ap = float(avg_pl) if avg_pl is not None else -1e18
            return (
                int(stars),
                stv,
                dq,
                int(n),
                float(wr),
                ap,
                code,
            )

        preview_rows.sort(key=_k, reverse=True)

        # 表示（おすすめ順）
        for code, n, wr, avg_pl, std_pl, stars, stability, design_q in preview_rows[:30]:
            ap = 0.0 if avg_pl is None else float(avg_pl)
            stv = "-" if stability is None else f"{float(stability):.3f}"
            dq = "-" if design_q is None else f"{float(design_q):.3f}"
            self.stdout.write(
                f"  {code} [all/all]: n={n:3d} win_rate={wr:5.1f}% avg_pl={ap:8.1f} stars={stars} stability={stv} design_q={dq}"
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

            for code, n, wr, avg_pl, std_pl, stars, stability, design_q in preview_rows:
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
                        "stability": float(stability) if stability is not None else None,
                        "design_q": float(design_q) if design_q is not None else None,
                        "window_days": int(days),
                        "updated_at": now,
                    },
                )
                upserted += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"[rebuild_behavior_stats] DB更新完了: {upserted} 件 upsert（n>0のみ）"))