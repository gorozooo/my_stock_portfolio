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
    eval_exit_reason の表示ラベル（PRO公式 / ai_sim_eval準拠）
    """
    if exit_reason == "hit_tp":
        return "利確"
    if exit_reason == "hit_sl":
        return "損切"
    if exit_reason == "time_stop":
        return "期限決済"
    if exit_reason == "carry":
        return "持ち越し（未確定）"
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
    if exit_reason in ("bad_entry_state",):
        return "約定状態不正"
    if exit_reason in ("no_close_for_time_stop",):
        return "終値取得不可"
    if exit_reason in ("exception",):
        return "評価エラー"
    return ""


def _get_pro_last_eval(v: VirtualTrade) -> Dict[str, Any]:
    """
    replay.pro.last_eval を安全に取り出す
    """
    replay = v.replay if isinstance(v.replay, dict) else {}
    pro = replay.get("pro") if isinstance(replay.get("pro"), dict) else {}
    last_eval = pro.get("last_eval") if isinstance(pro.get("last_eval"), dict) else {}
    return last_eval


def _combined_label_pro(v: VirtualTrade) -> str:
    """
    PRO公式の「勝ち/負け/引き分け/持ち越し/見送り」統一ラベル

    最優先：replay.pro.last_eval.label（ai_sim_evalが確定させる）
    フォールバック：eval_exit_reason + 状態
    """
    last_eval = _get_pro_last_eval(v)
    label = str(last_eval.get("label") or "").strip().lower()
    if label in ("win", "lose", "flat", "carry", "skip"):
        return label

    exit_reason = str(v.eval_exit_reason or "").strip()

    if exit_reason == "hit_tp":
        return "win"
    if exit_reason == "hit_sl":
        return "lose"
    if exit_reason == "time_stop":
        # time_stop の勝敗は本来 last_eval.label で決まるが、
        # 無い場合は pl_per_share で推定
        plps = last_eval.get("pl_per_share")
        try:
            if plps is not None:
                plps_f = float(plps)
                if plps_f > 0:
                    return "win"
                if plps_f < 0:
                    return "lose"
                return "flat"
        except Exception:
            pass
        return "skip"
    if exit_reason == "carry":
        return "carry"
    if exit_reason == "no_position":
        return "skip"

    # 例外・データ不足系は見送り
    if exit_reason:
        return "skip"

    # 未評価：entry済み＆未クローズなら carry、それ以外は見送り
    if v.closed_at is None and (v.eval_entry_px is not None):
        return "carry"

    return "skip"


def _get_pro_pl(v: VirtualTrade) -> Optional[float]:
    """
    PRO実績PL（円）
    - DBに eval_pl_pro が無くても replay.pro.last_eval.pl_pro で表示できる
    - carry の間は None（"—"表示）
    """
    last_eval = _get_pro_last_eval(v)
    try:
        pl = last_eval.get("pl_pro")
        if pl is None:
            return None
        return float(pl)
    except Exception:
        return None


def _accumulate_pro(summary: Dict[str, Any], v: VirtualTrade) -> None:
    """
    KPI集計（PRO公式）
    - total_pl: replay.pro.last_eval.pl_pro を足し上げ（carryは除外）
    """
    combined = _combined_label_pro(v)

    if combined == "win":
        summary["win"] = summary.get("win", 0) + 1
    elif combined == "lose":
        summary["lose"] = summary.get("lose", 0) + 1
    elif combined == "flat":
        summary["flat"] = summary.get("flat", 0) + 1
    else:
        summary["skip"] = summary.get("skip", 0) + 1

    pl = _get_pro_pl(v)
    if pl is not None:
        summary["total_pl"] = float(summary.get("total_pl", 0.0)) + float(pl)


@login_required
def simulate_list(request: HttpRequest) -> HttpResponse:
    """
    シミュレ一覧（DB版 / PRO公式記録）

    - 対象は PRO accepted のみ（replay.pro.status='accepted'）
    - opened_at（JST）を基準に日付フィルタ & 表示時刻を作る
    - mode / date / q でフィルタ
    - KPI:
        * 選択日の成績（PROのみ）
        * 通算（全期間）（PROのみ）
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

    # ---- PRO公式ベースQS（KPI/日付候補/一覧の母体）----
    base_qs = (
        VirtualTrade.objects
        .filter(user=user, replay__pro__status="accepted")
        .filter(qty_pro__gt=0)
        .order_by("-opened_at", "-id")
    )

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

    # date_param が無い時は「最新日」を自動選択（PROのみ）
    if selected_date is None and date_list:
        selected_date = max(date_list)

    selected_date_str = selected_date.isoformat() if selected_date is not None else ""

    # ---- KPI集計（PROのみ）----
    summary_selected: Dict[str, Any] = {
        "win": 0, "lose": 0, "flat": 0, "skip": 0,
        "total_pl": 0.0, "has_data": False,
    }
    summary_total: Dict[str, Any] = {
        "win": 0, "lose": 0, "flat": 0, "skip": 0,
        "total_pl": 0.0, "has_data": False,
    }

    for v in base_qs:
        _accumulate_pro(summary_total, v)

        try:
            opened_local = timezone.localtime(v.opened_at) if v.opened_at else None
        except Exception:
            opened_local = None

        if selected_date is not None and opened_local is not None and opened_local.date() == selected_date:
            _accumulate_pro(summary_selected, v)

    summary_selected["has_data"] = (summary_selected["win"] + summary_selected["lose"] + summary_selected["flat"] + summary_selected["skip"]) > 0
    summary_total["has_data"] = (summary_total["win"] + summary_total["lose"] + summary_total["flat"] + summary_total["skip"]) > 0

    # ---- 一覧用QS（ここからフィルタ適用 / 母体はPRO acceptedのみ） ----
    qs = (
        VirtualTrade.objects
        .filter(user=user, replay__pro__status="accepted")
        .filter(qty_pro__gt=0)
        .order_by("-opened_at", "-id")
    )

    if mode == "live":
        qs = qs.filter(mode__iexact="live")
    elif mode == "demo":
        qs = qs.filter(mode__iexact="demo")

    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))

    # ---- entries 作成 ----
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

        exit_reason = str(v.eval_exit_reason or "").strip()
        exit_reason_label = _label_exit_reason(exit_reason)
        combined_label = _combined_label_pro(v)

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

        # PRO実績PL（carry中は None）
        eval_pl_pro = _get_pro_pl(v)

        e: Dict[str, Any] = {
            "id": v.id,
            "code": str(v.code or ""),
            "name": str(v.name or ""),
            "mode": str(v.mode or "").lower(),
            "price_date": price_date,

            "ts": opened_local.isoformat() if opened_local else "",
            "ts_label": ts_label,
            "_dt": opened_local,

            # AIスナップ（PRO）
            "entry": v.entry_px,
            "tp": v.tp_px,
            "sl": v.sl_px,
            "qty_pro": int(v.qty_pro or 0),
            "required_cash_pro": v.required_cash_pro,
            "est_pl_pro": v.est_pl_pro,
            "est_loss_pro": v.est_loss_pro,

            # 評価結果
            "eval_entry_px": v.eval_entry_px,
            "eval_entry_ts": v.eval_entry_ts,
            "eval_exit_px": v.eval_exit_px,
            "eval_exit_ts": v.eval_exit_ts,
            "eval_exit_reason": exit_reason,

            "entry_label": entry_label,
            "exit_label": exit_label,
            "exit_reason_label": exit_reason_label,

            # PRO実績（replay由来）
            "eval_pl_pro": eval_pl_pro,

            "eval_horizon_days": v.eval_horizon_days,

            "combined_label": combined_label,
        }
        entries_all.append(e)

    # 最大100件
    entries = entries_all[:100]

    ctx = {
        "entries": entries,
        "mode": mode,
        "q": q,
        "summary_selected": summary_selected,
        "summary_total": summary_total,
        "selected_date": selected_date,
        "selected_date_str": selected_date_str,
    }
    return render(request, "aiapp/simulate_list.html", ctx)