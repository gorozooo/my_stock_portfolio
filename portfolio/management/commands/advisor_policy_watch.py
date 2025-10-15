# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import math
import datetime as dt

from django.core.management.base import BaseCommand, CommandParser
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone


# ===== ユーティリティ =====
def _read_jsonl_tail(path: Path, n: int = 50) -> List[dict]:
    """末尾から最大n行だけJSONLを読む（ファイルが無ければ空）"""
    if not path.exists():
        return []
    lines: List[str] = []
    with path.open("rb") as f:
        f.seek(0, 2)  # EOF
        size = f.tell()
        chunk = bytearray()
        # ざっくり後ろ1〜2MBだけ読めば十分
        read_size = min(size, 2_000_000)
        f.seek(size - read_size)
        chunk.extend(f.read(read_size))
        for line in chunk.decode("utf-8", errors="ignore").splitlines():
            if line.strip():
                lines.append(line.strip())
    rows = []
    for s in lines[-n:]:
        try:
            rows.append(json.loads(s))
        except Exception:
            pass
    return rows


def _pct_drop(prev: float, curr: float) -> float:
    """prev→curr の相対下落率（%）。prev<=0は0扱い。"""
    if prev <= 0:
        return 0.0
    return (max(0.0, prev - curr) / prev) * 100.0


@dataclass
class CheckResult:
    ok: bool
    messages: List[str]
    context: Dict[str, float]


# ===== チェック本体 =====
def _check_policy_degradation(
    policy_log: Path,
    min_drop_pct: float,
    min_days_gap: int = 5,
) -> CheckResult:
    """
    policy_history.jsonl の最近2スナップショットを比較して劣化検知。
    quality_index = kind_weight の平均（1.0が基準、<1は弱い）
    """
    rows = _read_jsonl_tail(policy_log, n=200)
    if len(rows) < 2:
        return CheckResult(True, ["policyログが不足（<2件）"], {})

    # 日付順に整列（保険）
    def _ts(r: dict) -> dt.datetime:
        v = r.get("ts") or r.get("updated_at")
        try:
            return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except Exception:
            return timezone.now()

    rows.sort(key=_ts)
    # 最後のスナップショットと、そこからmin_days_gap以上離れた直近を採用
    last = rows[-1]
    last_ts = _ts(last)
    prev: Optional[dict] = None
    for r in reversed(rows[:-1]):
        if (last_ts - _ts(r)).days >= min_days_gap:
            prev = r
            break
    if prev is None:
        # 間隔の条件を緩めて1つ前を使う
        prev = rows[-2]

    def _quality(r: dict) -> float:
        kw = (r.get("policy") or r).get("kind_weight") or r.get("kind_weight") or {}
        if not kw:
            return 1.0
        vals = [float(v) for v in kw.values() if isinstance(v, (int, float))]
        if not vals:
            return 1.0
        return sum(vals) / len(vals)

    q_prev = _quality(prev)
    q_last = _quality(last)
    drop = _pct_drop(q_prev, q_last)

    msgs: List[str] = []
    ok = True
    if drop >= min_drop_pct:
        ok = False
        msgs.append(f"policy品質低下: {q_prev:.3f} → {q_last:.3f}（- {drop:.1f}%）")

    return CheckResult(ok, msgs or [f"policy品質: {q_last:.3f}（前回 {q_prev:.3f}）"], {
        "policy_quality_prev": q_prev,
        "policy_quality_last": q_last,
        "policy_drop_pct": drop,
    })


def _check_ab_degradation(
    ab_log: Path,
    min_drop_pct: float,
    min_days_gap: int = 5,
    variant: str = "B",
) -> CheckResult:
    """
    ab_metrics.jsonl から variant=B の avg_improve を比較して劣化検知。
    """
    rows = _read_jsonl_tail(ab_log, n=200)
    # variant=Bのみ
    rows = [r for r in rows if (r.get("variant") or "").upper() == variant.upper()]
    if len(rows) < 2:
        return CheckResult(True, [f"ABログ（{variant}）が不足（<2件）"], {})

    def _ts(r: dict) -> dt.datetime:
        v = r.get("ts") or r.get("at")
        try:
            return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except Exception:
            return timezone.now()

    rows.sort(key=_ts)
    last = rows[-1]
    last_ts = _ts(last)
    prev = None
    for r in reversed(rows[:-1]):
        if (last_ts - _ts(r)).days >= min_days_gap:
            prev = r
            break
    if prev is None:
        prev = rows[-2]

    a_prev = float(prev.get("avg_improve") or 0.0)
    a_last = float(last.get("avg_improve") or 0.0)
    drop = _pct_drop(a_prev, a_last)

    msgs: List[str] = []
    ok = True
    if drop >= min_drop_pct:
        ok = False
        msgs.append(f"A/B(B)のAvgImprove低下: {a_prev:+.3f} → {a_last:+.3f}（- {drop:.1f}%）")
    if a_last < 0 and a_prev >= 0:
        ok = False
        msgs.append(f"A/B(B)のAvgImproveが負に転落: {a_last:+.3f}")

    return CheckResult(ok, msgs or [f"A/B(B) AvgImprove: {a_last:+.3f}（前回 {a_prev:+.3f}）"], {
        "ab_prev": a_prev,
        "ab_last": a_last,
        "ab_drop_pct": drop,
    })


# ===== Django management command =====
class Command(BaseCommand):
    help = "policy履歴とA/B指標を監視し、劣化を検知したらメール通知します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--policy-log", type=str, default="media/advisor/policy_history.jsonl",
                           help="policy履歴のJSONLパス（advisor_policy_logが出力）")
        parser.add_argument("--ab-log", type=str, default="media/advisor/ab_metrics.jsonl",
                           help="A/B集計のJSONLパス（advisor_policy_logが出力）")
        parser.add_argument("--min-drop-pct", type=float, default=10.0,
                           help="“前回比”で何%以上下がったら通知するか（%）")
        parser.add_argument("--gap-days", type=int, default=5,
                           help="比較対象サンプル間の最低日数（近すぎ比較を避ける）")
        parser.add_argument("--to", type=str, default=getattr(settings, "ADMIN_EMAIL", ""),
                           help="通知先（カンマ区切り）未指定なら settings.ADMIN_EMAIL")
        parser.add_argument("--subject", type=str, default="[ALERT] Advisor品質 劣化検知",
                           help="メール件名")
        parser.add_argument("--print-only", action="store_true",
                           help="メール送信せず出力だけ行う")

    def handle(self, *args, **opts):
        policy_log = Path(opts["policy_log"])
        ab_log = Path(opts["ab_log"])
        min_drop = float(opts["min_drop_pct"])
        gap_days = int(opts["gap_days"])
        to = [x.strip() for x in (opts["to"] or "").split(",") if x.strip()]
        print_only = bool(opts["print_only"])
        subj = str(opts["subject"])

        now = timezone.now()

        p_res = _check_policy_degradation(policy_log, min_drop, gap_days)
        a_res = _check_ab_degradation(ab_log, min_drop, gap_days, variant="B")

        # どちらかがNGなら通知
        alerts: List[str] = []
        if not p_res.ok:
            alerts.extend(p_res.messages)
        if not a_res.ok:
            alerts.extend(a_res.messages)

        if alerts:
            body_lines = [
                f"監視時刻: {now:%Y-%m-%d %H:%M:%S}",
                f"しきい値: 前回比 -{min_drop:.1f}% 以上の低下で通知",
                "",
                "▼ 検知内容",
                *[f"・{m}" for m in alerts],
                "",
                "▼ 現状サマリ",
                f"- policy_quality: {p_res.context.get('policy_quality_last', 0):.3f}（前回 {p_res.context.get('policy_quality_prev', 0):.3f} / 低下 {p_res.context.get('policy_drop_pct', 0):.1f}%）",
                f"- AB(B)_AvgImprove: {a_res.context.get('ab_last', 0):+.3f}（前回 {a_res.context.get('ab_prev', 0):+.3f} / 低下 {a_res.context.get('ab_drop_pct', 0):.1f}%）",
                "",
                "※ 閾値やログの場所は `manage.py advisor_policy_watch --help` を参照。",
            ]
            body = "\n".join(body_lines)

            if print_only or not to:
                # 送信先がなければ標準出力に出して終了
                self.stdout.write(self.style.WARNING("[advisor_policy_watch] ALERT (print only)"))
                self.stdout.write(body)
                return

            send_mail(
                subject=subj,
                message=body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
                recipient_list=to,
                fail_silently=False,
            )
            self.stdout.write(self.style.ERROR(
                f"[advisor_policy_watch] ALERT sent to {', '.join(to)}"
            ))
        else:
            # 問題なし
            note = " / ".join(p_res.messages + a_res.messages)
            self.stdout.write(self.style.SUCCESS(f"[advisor_policy_watch] OK - {note}"))