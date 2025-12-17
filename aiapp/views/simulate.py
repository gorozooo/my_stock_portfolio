# aiapp/views/simulate.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from datetime import date as _date

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade


def _label_exit_reason(exit_reason: str) -> str:
    """
    eval_exit_reason の表示ラベル
    """
    if exit_reason == "hit_tp":
        return "利確"
    if exit_reason == "hit_sl":
        return "損切"
    if exit_reason == "horizon_close":
        return "持ち越し"
    if exit_reason == "no_position":
        return "指値に刺さらなかった"
    if exit_reason in ("no_bars_after_active",):
        return "場後起票のため当日評価不可"
    if exit_reason in ("no_bars",):
        return "5分足データなし"
    if exit_reason in ("bad_ts", "no_ts"):
        return "時刻データ不正"
    if exit_reason in ("no_ohlc",):
        return "価格カラム不正"
    if exit_reason in ("no_opened_at",):
        return "opened_at不正"
    if exit_reason in ("no_entry",):
        return "entry不正"
    if exit_reason in ("exception",):
        return "評価エラー"
    return ""


def _combined_label_from_exit_reason(exit_reason: str, qty_total: float) -> str:
    """
    勝ち/負け/持ち越し/見送り の統一ラベル
    """
    if exit_reason == "hit_tp":
        return "win"
    if exit_reason == "hit_sl":
        return "lose"
    if exit_reason == "horizon_close":
        return "carry"
    if exit_reason in ("no_position", "no_touch", "no_fill"):
        return "skip"

    # no_bars_* / exception なども見送り枠に倒す
    if exit_reason:
        return "skip"

    # exit_reason が空（まだ評価されてない想定）
    if qty_total > 0:
        return "carry"
    return "skip"


@login_required
def simulate_list(request: HttpRequest) -> HttpResponse:
    """
    シミュレ一覧（DB版）
    - VirtualTrade を読む
    - opened_at（JST）を基準に日付フィルタ & 表示時刻を作る
    - mode / date / q でフィルタ
    - KPIは全件ベース（フィルタ無関係）
    """
    user = request.user

    # ---- フィルタ値（クエリパラメータ） ----
    mode = (request.GET.get("mode") or "all").lower()
    if mode not in ("all", "live", "demo"):
        mode = "all"

    date_param = (request.GET.get("date") or "").strip()
    q = (request.GET.get("q") or "").strip()

    selected_date: Optional[_date] = None
    if date_param:
        try:
            selected_date = _date.fromisoformat(date_param)
        except Exception:
            selected_date = None

    now_local = timezone.localtime()
    today_date = now_local.date()

    # ---- KPI & 日付候補は「全件」から作る（フィルタ無関係） ----
    base_qs = VirtualTrade.objects.filter(user=user).order_by("-opened_at", "-id")

    # 日付候補（opened_at基準）
    date_list: List[_date] = []
    for v in base_qs.only("opened_at"):
        if not v.opened_at:
            continue
        try:
            d = timezone.localtime(v.opened_at).date()
            date_list.append(d)
        except Exception:
            continue

    # date_param が無い時は「最新日」を自動選択
    if selected_date is None and date_list:
        selected_date = max(date_list)

    selected_date_str = selected_date.isoformat() if selected_date is not None else ""

    # ---- KPI集計（全件） ----
    def _accumulate(summary: Dict[str, Any], v: VirtualTrade) -> None:
        # 合計損益
        total_pl = float(summary.get("total_pl", 0.0))
        for key in ("eval_pl_rakuten", "eval_pl_matsui"):
            val = getattr(v, key, None)
            try:
                if val is not None:
                    total_pl += float(val)
            except (TypeError, ValueError):
                pass
        summary["total_pl"] = total_pl

        # combined_label
        qty_r = int(v.qty_rakuten or 0)
        qty_m = int(v.qty_matsui or 0)
        qty_total = float(qty_r + qty_m)

        exit_reason = str(v.eval_exit_reason or "").strip()
        combined = _combined_label_from_exit_reason(exit_reason, qty_total)

        if combined == "win":
            summary["win"] = summary.get("win", 0) + 1
        elif combined == "lose":
            summary["lose"] = summary.get("lose", 0) + 1
        elif combined == "flat":
            summary["flat"] = summary.get("flat", 0) + 1
        else:
            summary["skip"] = summary.get("skip", 0) + 1

    summary_today: Dict[str, Any] = {
        "win": 0, "lose": 0, "flat": 0, "skip": 0,
        "total_pl": 0.0, "has_data": False,
    }
    summary_total: Dict[str, Any] = {
        "win": 0, "lose": 0, "flat": 0, "skip": 0,
        "total_pl": 0.0, "has_data": False,
    }

    for v in base_qs:
        try:
            opened_local = timezone.localtime(v.opened_at) if v.opened_at else None
        except Exception:
            opened_local = None

        if opened_local is not None and opened_local.date() == today_date:
            _accumulate(summary_today, v)

        _accumulate(summary_total, v)

    summary_today["has_data"] = (summary_today["win"] + summary_today["lose"] + summary_today["flat"] + summary_today["skip"]) > 0
    summary_total["has_data"] = (summary_total["win"] + summary_total["lose"] + summary_total["flat"] + summary_total["skip"]) > 0

    # ---- 一覧用QS（ここからフィルタ適用） ----
    qs = VirtualTrade.objects.filter(user=user).order_by("-opened_at", "-id")

    if mode == "live":
        qs = qs.filter(mode__iexact="live")
    elif mode == "demo":
        qs = qs.filter(mode__iexact="demo")

    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))

    # ---- entries 作成（テンプレが期待する形へ） ----
    entries_all: List[Dict[str, Any]] = []

    for v in qs:
        try:
            opened_local = timezone.localtime(v.opened_at) if v.opened_at else None
        except Exception:
            opened_local = None

        # 日付フィルタ（opened_at基準）
        if selected_date is not None:
            if opened_local is None or opened_local.date() != selected_date:
                continue

        qty_r = int(v.qty_rakuten or 0)
        qty_m = int(v.qty_matsui or 0)
        qty_total = float(qty_r + qty_m)

        exit_reason = str(v.eval_exit_reason or "").strip()
        exit_reason_label = _label_exit_reason(exit_reason)
        combined_label = _combined_label_from_exit_reason(exit_reason, qty_total)

        ts_label = opened_local.strftime("%Y/%m/%d %H:%M") if opened_local else ""

        entry_label = ""
        if v.eval_entry_ts:
            try:
                entry_label = timezone.localtime(v.eval_entry_ts).strftime("%Y/%m/%d %H:%M")
            except Exception:
                entry_label = ""

        exit_label = ""
        if v.eval_exit_ts:
            try:
                exit_label = timezone.localtime(v.eval_exit_ts).strftime("%Y/%m/%d %H:%M")
            except Exception:
                exit_label = ""

        price_date = None
        try:
            if v.trade_date:
                price_date = v.trade_date.isoformat()
        except Exception:
            price_date = None

        e: Dict[str, Any] = {
            "id": v.id,
            "code": str(v.code or ""),
            "name": str(v.name or ""),
            "mode": str(v.mode or "").lower(),
            "price_date": price_date,

            "ts": opened_local.isoformat() if opened_local else "",
            "ts_label": ts_label,
            "_dt": opened_local,

            # AIスナップ
            "entry": v.entry_px,
            "tp": v.tp_px,
            "sl": v.sl_px,
            "qty_rakuten": qty_r,
            "qty_matsui": qty_m,
            "est_pl_rakuten": v.est_pl_rakuten,
            "est_pl_matsui": v.est_pl_matsui,
            "est_loss_rakuten": v.est_loss_rakuten,
            "est_loss_matsui": v.est_loss_matsui,

            # 評価結果
            "eval_entry_px": v.eval_entry_px,
            "eval_entry_ts": v.eval_entry_ts,
            "eval_exit_px": v.eval_exit_px,
            "eval_exit_ts": v.eval_exit_ts,
            "eval_exit_reason": exit_reason,

            "entry_label": entry_label,
            "exit_label": exit_label,

            "exit_reason": exit_reason,
            "exit_reason_label": exit_reason_label,

            "eval_label_rakuten": str(v.eval_label_rakuten or ""),
            "eval_label_matsui": str(v.eval_label_matsui or ""),
            "eval_pl_rakuten": v.eval_pl_rakuten,
            "eval_pl_matsui": v.eval_pl_matsui,

            "eval_horizon_days": None,

            "combined_label": combined_label,
        }
        entries_all.append(e)

    # 最大100件
    entries = entries_all[:100]

    ctx = {
        "entries": entries,
        "mode": mode,
        "q": q,
        "summary_today": summary_today,
        "summary_total": summary_total,
        "selected_date": selected_date,
        "selected_date_str": selected_date_str,
    }
    return render(request, "aiapp/simulate_list.html", ctx)