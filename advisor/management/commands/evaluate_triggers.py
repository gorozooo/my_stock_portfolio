from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta, date
from typing import List, Tuple, Optional, Dict, Any

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from advisor.models import WatchEntry, ActionLog
from advisor.models_policy import AdvisorPolicy
from advisor.models_trend import TrendResult

from advisor.services.notify import push_line_message, make_flex_from_tr
from advisor.services.policy_rules import compute_exit_targets  # ★ 追加

JST = timezone(timedelta(hours=9))

def _passes_policy(tr: TrendResult, policy: AdvisorPolicy) -> Tuple[bool, List[str]]:
    r = policy.rule_json or {}
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
        sval = float(tr.slope_annual or 0.0); minv = float(r["min_slope_yr"])
        if sval < minv:
            ok = False; why.append(f"slope {round(sval, 4)}<{minv}")
    return ok, (why or ["OK"])

def _cooldown_blocked(user, ticker: str, hours: int = 24) -> Optional[str]:
    since = datetime.now(JST) - timedelta(hours=hours)
    seen = ActionLog.objects.filter(
        user=user, ticker=ticker.upper(),
        action__in=["save_order", "notify"],
        created_at__gte=since
    ).exists()
    return f"cooldown({hours}h)" if seen else None

def _calc_exits_by_policy(tr: TrendResult, pol: AdvisorPolicy) -> Dict[str, Any]:
    """policy.rule_json を使って TP/SL 具体価格を求める。"""
    rules = {"targets": (pol.rule_json or {}).get("targets", {}), "exits": (pol.rule_json or {}).get("exits", {})}
    atr_hint = None
    try:
        n = tr.notes or {}
        atr_hint = float(n.get("atr14")) if n.get("atr14") is not None else None
    except Exception:
        atr_hint = None
    entry = int(tr.entry_price_hint or tr.close_price or 0) or None
    xt = compute_exit_targets(policy=rules, ticker=tr.ticker.upper(), entry_price=entry, days_held=None, atr14_hint=atr_hint)
    return {
        "tp_pct": rules["targets"].get("tp_pct"),
        "sl_pct": rules["targets"].get("sl_pct"),
        "tp_price": xt.tp_price,
        "sl_price": xt.sl_price,
        "trail_atr_mult": xt.trail_atr_mult,
        "time_exit_due": xt.time_exit_due,
        "_notes": xt.notes,
    }

class Command(BaseCommand):
    help = "監視銘柄を評価して、条件合致ならLINEのFlexカードで通知します。"

    def add_arguments(self, parser: argparse.ArgumentParser):
        parser.add_argument("--window", type=str, default="preopen")
        parser.add_argument("--user-id", type=int, default=None)
        parser.add_argument("--tickers", type=str, default="")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--why", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--relax", action="store_true")
        parser.add_argument("--cooldown-hours", type=int, default=24)

    def handle(self, *args, **opts):
        User = get_user_model()
        user = User.objects.filter(id=opts.get("user_id")).first() or User.objects.first()
        if not user:
            self.stdout.write("no user"); return

        tickers_filter: List[str] = []
        if opts.get("tickers"):
            tickers_filter = [t.strip().upper() for t in opts["tickers"].split(",") if t.strip()]

        status_active = getattr(WatchEntry, "STATUS_ACTIVE", "active")
        wqs = WatchEntry.objects.filter(user=user, status=status_active)
        if tickers_filter:
            wqs = wqs.filter(ticker__in=tickers_filter)
        watches = list(wqs.values_list("ticker", flat=True))

        policies = list(AdvisorPolicy.objects.filter(is_active=True).order_by("-priority"))
        if opts.get("relax"):
            for p in policies:
                r = p.rule_json or {}
                r["min_overall"] = 45; r["min_theme"] = 0.40
                r["allow_weekly"] = ["up", "flat", "down"]; r["min_slope_yr"] = -1.0
                p.rule_json = r

        today = date.today()
        sent = 0; skipped = 0

        if opts.get("why") or opts.get("dry_run"):
            push_line_message(alt_text="diag-only", text=None, flex=None)

        for t in watches:
            tr = (TrendResult.objects.filter(user=user, ticker=t, asof=today)
                  .order_by("-updated_at").first())
            if not tr:
                skipped += 1
                if opts["why"]:
                    self.stdout.write(f"⛔ {t}: 本日のTrendResultなし")
                continue

            hit, reasons_ng = [], []
            hit_policies: List[AdvisorPolicy] = []
            for p in policies:
                ok, why = _passes_policy(tr, p)
                if ok:
                    hit.append(p.name); hit_policies.append(p)
                else:
                    reasons_ng.append((p.name, why))

            if not hit:
                skipped += 1
                if opts["why"]:
                    why_text = " | ".join([f"{n}:{';'.join(w)}" for n, w in reasons_ng])
                    self.stdout.write(f"⛔ {t}: ポリシー不一致 → {why_text}")
                continue

            cd = _cooldown_blocked(user, t, hours=int(opts.get("cooldown_hours") or 24))
            if cd and not opts["force"]:
                skipped += 1
                if opts["why"]:
                    self.stdout.write(f"⛔ {t}: {cd}")
                continue

            # 代表ポリシー（最上位）でTP/SL計算
            exits = _calc_exits_by_policy(tr, hit_policies[0])

            alt = f"[{opts['window']}] {t} / " + " / ".join(hit)
            if opts.get("dry_run"):
                self.stdout.write(f"✅ {t}: 送信予定 (policies={hit}) TP={exits.get('tp_price')} SL={exits.get('sl_price')}")
            else:
                ActionLog.objects.create(
                    user=user, ticker=t, action="notify",
                    note=f"window={opts['window']}; policies={','.join(hit)}"
                )
                bubble = make_flex_from_tr(tr, hit, window=opts["window"], exits=exits)
                push_line_message(alt_text=alt, flex=bubble)
                self.stdout.write(f"📨 {t}: 送信済 (policies={hit})")
                sent += 1

        self.stdout.write(
            f"evaluate_triggers done: sent={sent}, skipped={skipped}, window={opts['window']}"
            + (" [dry-run]" if opts.get("dry_run") else "")
        )