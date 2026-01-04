from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Optional, Dict

from django.db.models import Max
from django.conf import settings
from django.contrib.auth import get_user_model

from advisor.models import WatchEntry
from advisor.models_trend import TrendResult
from advisor.models_policy import AdvisorPolicy
from advisor.models_notify import NotificationLog
from advisor.services.notifier import send_line_text

JST = timezone(timedelta(hours=9))
User = get_user_model()

@dataclass
class Trigger:
    ticker: str
    name: str
    policy_name: str
    reason_key: str   # 重複抑止キー
    summary: str
    quick: List[Dict[str, str]]

def _cooldown_minutes(window: str) -> int:
    # 攻め：場中は短く、引け後/日次は長く
    return 120 if window == "intraday" else 24*60  # intraday=2h, 他=24h

def _already_notified(user, ticker: str, reason_key: str, window: str) -> bool:
    cd_min = _cooldown_minutes(window)
    since = datetime.now(JST) - timedelta(minutes=cd_min)
    return NotificationLog.objects.filter(
        user=user, ticker=ticker, reason_key=reason_key, window=window, sent_at__gte=since
    ).exists()

def _policy_ok(tr: TrendResult, pol: AdvisorPolicy) -> bool:
    r = pol.rule_json or {}
    # しっかり守る：overall / theme / weekly / slope
    if tr.overall_score is not None and int(tr.overall_score) < int(r.get("min_overall", 0)):
        return False
    if tr.theme_score is not None and float(tr.theme_score) < float(r.get("min_theme", 0.0)):
        return False
    allow_weekly = r.get("allow_weekly", [])
    if tr.weekly_trend and allow_weekly and tr.weekly_trend not in allow_weekly:
        return False
    if r.get("min_slope_yr") is not None:
        if float(tr.slope_annual or 0.0) < float(r["min_slope_yr"]):
            return False
    return True

def _latest_tr(trs):
    # 同一ティッカーで最新を1本
    seen = set()
    out = []
    for r in trs.order_by("-asof", "-updated_at"):
        t = r.ticker.upper()
        if t in seen: continue
        seen.add(t); out.append(r)
    return out

def evaluate_watchlist(*, window: str = "daily") -> Tuple[int, int]:
    """
    returns: (sent, skipped)
    """
    user = User.objects.first()
    if not user:
        return 0, 0

    # アクティブWatch
    status_active = getattr(WatchEntry, "STATUS_ACTIVE", "active")
    watches = WatchEntry.objects.filter(user=user, status=status_active)

    # 有効ポリシーをMap（名前で紐づけやすく）
    policies = {p.name: p for p in AdvisorPolicy.objects.filter(is_active=True)}

    # TrendResultの最新群
    trs = TrendResult.objects.filter(user=user)
    latest_rows = _latest_tr(trs)

    sent, skipped = 0, 0
    for w in watches:
        tkr = (w.ticker or "").upper().strip()
        if not tkr:
            continue
        # 株価/スコアは最新1本
        tr = next((r for r in latest_rows if r.ticker.upper() == tkr), None)
        if not tr:
            skipped += 1
            continue

        # Watchに紐づくポリシー（ない場合は全ポリシー対象でもOK）
        target_pols = []
        if getattr(w, "policy_names", None):
            for nm in (w.policy_names or []):
                p = policies.get(nm)
                if p: target_pols.append(p)
        else:
            target_pols = list(policies.values())

        for pol in target_pols:
            if not _policy_ok(tr, pol):
                continue

            # 理由キー（重複抑止）：ポリシー名＋週足＋丸めたoverall
            reason_key = f"{pol.name}|{tr.weekly_trend}|{int(tr.overall_score or 0)//5*5}"
            if _already_notified(user, tkr, reason_key, window):
                skipped += 1
                continue

            # メッセージを生成（短く・要点だけ）
            name = (tr.name or tkr)
            tp = int(round((pol.rule_json or {}).get("targets", {}).get("tp_pct", 0.10) * 100))
            sl = int(round((pol.rule_json or {}).get("targets", {}).get("sl_pct", 0.05) * 100))
            overall = int(tr.overall_score or 0)
            wk = {"up":"↗️上向き","flat":"➡️横ばい","down":"↘️下向き"}.get((tr.weekly_trend or "flat").lower(), "➡️横ばい")

            summary = (
                f"[IN候補] {name} ({tkr}) / {pol.name}\n"
                f"・相場: {wk}  ・総合: {overall}点\n"
                f"・TP:+{tp}%  SL:-{sl}%\n"
                f"・根拠: テーマ{int((tr.theme_score or 0)*100)}点, slope≈{round((tr.slope_annual or 0)*100,1)}%/yr"
            )

            quick = [
                {"label":"📝発注メモ", "text": f"/save {tkr} {pol.name}"},
                {"label":"⏰2h後",   "text": f"/remind {tkr} 120"},
                {"label":"❌却下",   "text": f"/reject {tkr}"},
            ]

            ok = send_line_text(summary, quick_actions=quick)
            if ok:
                NotificationLog.objects.create(
                    user=user, ticker=tkr, reason_key=reason_key, window=window, message=summary
                )
                sent += 1
            else:
                skipped += 1

    return sent, skipped