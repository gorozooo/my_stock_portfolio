# advisor/management/commands/evaluate_triggers.py
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta, date
from typing import Dict, Any, List, Tuple, Optional

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from advisor.models import WatchEntry, ActionLog
from advisor.models_policy import AdvisorPolicy
from advisor.models_trend import TrendResult
from advisor.services.notify import push_line_message, make_flex_from_tr  # 既存
from advisor.services.policy_snapshot import load_final_rules_for_today   # ★追加

JST = timezone(timedelta(hours=9))

def _passes_policy(tr: TrendResult, rule_json: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    ここは“PolicySnapshot or rule_json”の **dict** を前提に判定するよう変更。
    """
    r = rule_json or {}
    why: List[str] = []
    ok = True

    mo = int(r.get("min_overall", 0))
    if tr.overall_score is not None and tr.overall_score < mo:
        ok = False; why.append(f"overall<{mo}")

    mt = float(r.get("min_theme", 0.0))
    if tr.theme_score is not None and float(tr.theme_score) < mt:
        ok = False; why.append(f"theme<{mt}")

    allow = r.get("allow_weekly") or ["up", "flat", "down"]
    if tr.weekly_trend and tr.weekly_trend not in allow:
        ok = False; why.append(f"weekly:{tr.weekly_trend} not in {allow}")

    if "min_slope_yr" in r:
        sval = float(tr.slope_annual or 0.0)
        minv = float(r["min_slope_yr"])
        if sval < minv:
            ok = False; why.append(f"slope {round(sval, 4)}<{minv}")

    return ok, (why or ["OK"])

def _cooldown_blocked(user, ticker: str) -> Optional[str]:
    """24時間以内に同銘柄でnotify/save_orderがあればブロック"""
    since = datetime.now(JST) - timedelta(hours=24)
    seen = ActionLog.objects.filter(
        user=user, ticker=ticker.upper(),
        action__in=["save_order", "notify"],
        created_at__gte=since
    ).exists()
    return "cooldown(24h)" if seen else None

class Command(BaseCommand):
    help = "Evaluate watch triggers and send notifications. Supports snapshots (PolicySnapshot) + --dry-run/--why/--tickers/--force/--relax"

    def add_arguments(self, parser: argparse.ArgumentParser):
        parser.add_argument("--window", type=str, default="preopen",
                            help="運用ウィンドウ名（preopen / intraday / closeなど）")
        parser.add_argument("--user-id", type=int, default=None)
        parser.add_argument("--tickers", type=str, default="",
                            help="カンマ区切りで銘柄を限定（例: 7203.T,6758.T）")
        parser.add_argument("--dry-run", action="store_true", help="通知せず判定だけ行う")
        parser.add_argument("--why", action="store_true", help="不合格理由を表示")
        parser.add_argument("--force", action="store_true", help="クールダウン等を無視して強制送信")
        parser.add_argument("--relax", action="store_true", help="閾値を緩めて検証（min値を緩和）")

    def handle(self, *args, **opts):
        user = (
            get_user_model().objects.filter(id=opts.get("user_id")).first()
            or get_user_model().objects.first()
        )
        if not user:
            self.stdout.write("no user"); return

        tickers_filter = []
        if opts.get("tickers"):
            tickers_filter = [t.strip().upper() for t in opts["tickers"].split(",") if t.strip()]

        # アクティブWatch
        status_active = getattr(WatchEntry, "STATUS_ACTIVE", "active")
        wqs = WatchEntry.objects.filter(user=user, status=status_active)
        if tickers_filter:
            wqs = wqs.filter(ticker__in=tickers_filter)
        watches = list(wqs.values_list("ticker", flat=True))

        # ★ 当日の“確定ルール”をロード（PolicySnapshot優先）
        #    -> [(policy_obj, rule_dict)] の形
        pol_and_rules = load_final_rules_for_today()

        # --relax オプション：ルールを上書きで緩和
        if opts.get("relax"):
            for i, (p, r) in enumerate(pol_and_rules):
                rr = (r or {}).copy()
                rr["min_overall"] = 45
                rr["min_theme"] = 0.4
                rr["allow_weekly"] = ["up", "flat", "down"]
                rr["min_slope_yr"] = -1.0
                pol_and_rules[i] = (p, rr)

        today = date.today()
        sent = 0
        skipped = 0

        # LINEダイアグ（環境チェックのみ）: text/flex無しで呼ぶと “skip send” 表示
        if opts.get("dry_run") and not watches:
            try:
                # 診断だけ（何も送らない）
                push_line_message(alt_text="diag", text=None, flex=None)
            except TypeError:
                # 古いpush_line_messageシグネチャの場合は無視
                pass

        for t in watches:
            tr = (
                TrendResult.objects.filter(user=user, ticker=t, asof=today)
                .order_by("-updated_at").first()
            )
            if not tr:
                skipped += 1
                if opts["why"]:
                    self.stdout.write(f"⛔ {t}: no TrendResult(today)")
                continue

            hit_names: List[str] = []
            reasons_ng: List[Tuple[str, List[str]]] = []

            for p, rule in pol_and_rules:
                ok, why = _passes_policy(tr, rule)
                if ok:
                    hit_names.append(p.name)
                else:
                    reasons_ng.append((p.name, why))

            if not hit_names:
                skipped += 1
                if opts["why"]:
                    why_text = " | ".join([f"{n}:{';'.join(w)}" for n, w in reasons_ng])
                    self.stdout.write(f"⛔ {t}: policy_miss → {why_text}")
                continue

            cd = _cooldown_blocked(user, t)
            if cd and not opts["force"]:
                skipped += 1
                if opts["why"]:
                    self.stdout.write(f"⛔ {t}: {cd}")
                continue

            if opts.get("dry_run"):
                self.stdout.write(f"✅ {t}: would notify (policies={hit_names})")
            else:
                # 送信（Flex生成は既存の make_flex_from_tr を使用）
                try:
                    bubble = make_flex_from_tr(tr, hit_names, window=opts["window"])
                except TypeError:
                    # 古い実装を配慮（exits指定なし）
                    bubble = make_flex_from_tr(tr, hit_names, window=opts["window"])  # type: ignore

                # LINE push
                try:
                    push_line_message(alt_text=f"{t} alert", text=None, flex=bubble)
                except TypeError:
                    # 古い関数シグネチャに合わせる後方互換（textのみ送信）
                    push_line_message(alt_text=f"{t} alert", text=f"{t} 通知", flex=None)  # type: ignore

                # ログ
                ActionLog.objects.create(
                    user=user, ticker=t,
                    action="notify",
                    note=f"window={opts['window']}; policies={','.join(hit_names)}"
                )
                self.stdout.write(f"📨 {t}: notified (policies={hit_names})")
                sent += 1

        self.stdout.write(
            f"evaluate_triggers done: sent={sent}, skipped={skipped}, window={opts['window']}"
            + (" [dry-run]" if opts.get("dry_run") else "")
        )