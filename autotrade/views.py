"""
[FILE] autotrade/views.py
[PATH] <project_root>/autotrade/views.py

このファイルは何？
- AutoTrade のダッシュボード表示（iPhone 1画面）を作る View です。
- DailyState（今日の状態）を読み、テンプレに渡す表示用データ（バックテスト行や理由文）を整形します。
- さらに「実験室（TuningProfile）」として、一覧・新規作成・編集・アーカイブ（安全削除）・検証実行・結果表示を提供します。
- 今回追加：CANDIDATE保存 / ACTIVE昇格 / ロールバック（監査ログ付き）

今回のポイント（UI改善）：
- tuning_result（結果ページ）を「読みやすさ重視」に変更：
  1) 理由（日本語＋数字＋円）はデフォルト折りたたみ（テンプレ側）
  2) どのチューニングで検証したかをヘッダで明示（tune / windows / picks）
  3) 一覧に戻らず “その場で再検証” できる導線（同条件 / picks更新）
  4) ACTIVE比 / 直前比 の差分（Δ）を表示（文章ではなく数値）
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse, HttpRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    AutoTradeDailyState,
    AutoTradeTuningProfile,
    AutoTradeSettingSnapshot,
    AutoTradePromotionLog,
)

from autotrade.services.universe.service import build_daily_universe
from autotrade.services.tuning.manual_backtest import run_backtest_for_tuning_profile

# ★ 追加：結果ページで “ACTIVE比 / 直前比” を作るための読み取り
from autotrade.models_backtest import AutoTradeBacktestRunDetail, AutoTradeExecution
from autotrade.services.backtest.execution_metrics import summarize_executions
from autotrade.services.backtest.gate import judge_multi_window

# ★ 追加：結果ページの再検証（同条件でやり直し）
from autotrade.services.backtest.runner import run_detailed_backtests_for_universe


# =========================================================
# Dashboard
# =========================================================

def _get_nested_dict(d: dict, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        if k in cur:
            cur = cur.get(k)
            continue
        # JSONField経由で intキーが str になる対策
        ks = str(k)
        if ks in cur:
            cur = cur.get(ks)
            continue
        return default
    return cur if cur is not None else default


def _build_bt_rows_by_strategy_for_template(state: AutoTradeDailyState):
    """
    backtest の新フォーマット（meta/by_window/gate）から
    「戦略ごと × windowごと」の表示行を整形して返す。

    返り値:
    {
      "VWAP": [ {window, trades, win_rate, pf, dd_pct, dd_yen, pnl}, ... ],
      "BREAKOUT": [ ... ],
    }
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    by_window = bt.get("by_window") if isinstance(bt.get("by_window"), dict) else {}

    windows = ["20", "60", "120"]
    out = {"VWAP": [], "BREAKOUT": []}

    for strat in ["VWAP", "BREAKOUT"]:
        rows = []
        for w in windows:
            m = _get_nested_dict(by_window, int(w), strat, default={})
            if not isinstance(m, dict):
                m = {}

            rows.append({
                "window": w,
                "trades": m.get("trades"),
                "win_rate": m.get("win_rate"),
                "pf": m.get("profit_factor"),
                "dd_pct": m.get("max_drawdown_pct"),
                "dd_yen": m.get("max_drawdown_yen"),
                "pnl": m.get("total_pnl"),
                "wins": m.get("wins"),
                "losses": m.get("losses"),
                "sum_win_yen": m.get("sum_win_yen"),
                "sum_loss_yen": m.get("sum_loss_yen"),
            })
        out[strat] = rows

    return out


def _build_gate_bundle_for_template(state: AutoTradeDailyState):
    """
    gate の日本語理由（数値＋円入り）をテンプレ向けに整形する。
    runner.py が backtest['gate'] に入れている情報をそのまま使う（再計算しない）。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    gate = bt.get("gate") if isinstance(bt.get("gate"), dict) else {}

    final = gate.get("final") if isinstance(gate.get("final"), dict) else {}
    gv = gate.get("VWAP") if isinstance(gate.get("VWAP"), dict) else {}
    gb = gate.get("BREAKOUT") if isinstance(gate.get("BREAKOUT"), dict) else {}

    return {
        "final": {
            "gate_level": str(final.get("gate_level") or state.gate_level or "STOP"),
            "active": list(final.get("active") or []),
            "disabled": list(final.get("disabled") or []),
        },
        "VWAP": {
            "gate_level": str(gv.get("gate_level") or "STOP"),
            "reasons": [str(x) for x in (gv.get("reasons") or []) if str(x).strip()],
        },
        "BREAKOUT": {
            "gate_level": str(gb.get("gate_level") or "STOP"),
            "reasons": [str(x) for x in (gb.get("reasons") or []) if str(x).strip()],
        },
        "final_reason_text": (state.gate_reason or "").strip(),
    }


@login_required
def dashboard(request: HttpRequest):
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    gate_bundle = _build_gate_bundle_for_template(state)
    bt_rows_by_strategy = _build_bt_rows_by_strategy_for_template(state)

    initial_tab = (state.strategy or "VWAP").strip() or "VWAP"
    if initial_tab not in ["VWAP", "BREAKOUT"]:
        initial_tab = "VWAP"

    ctx = {
        "state": state,
        "gate": gate_bundle,
        "bt_rows_by_strategy": bt_rows_by_strategy,
        "initial_tab": initial_tab,
    }
    return render(request, "autotrade/dashboard.html", ctx)


# =========================================================
# TuningProfile（実験室）
# =========================================================

def _default_params() -> Dict[str, Any]:
    """
    実験室の初期パラメータ（自由入力の土台）
    - UIは「%」表記（例: 0.25 = 0.25%）
    """
    return {
        "VWAP": {
            "stop_pct": 0.25,
            "rr": 1.5,
            "pullback_pct": 0.05,
            "max_hold_min": 30,
        },
        "BREAKOUT": {
            "stop_pct": 0.30,
            "rr": 2.0,
            "lookback_bars": 6,
            "max_hold_min": 30,
        },
    }


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v).strip())
    except Exception:
        return float(default)


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(str(v).strip()))
    except Exception:
        return int(default)


@login_required
def tuning_list(request: HttpRequest):
    """
    調整用プロファイルの一覧（入口）
    """
    profiles = (
        AutoTradeTuningProfile.objects
        .filter(user=request.user, is_archived=False)
        .order_by("-updated_at", "-id")
    )

    ctx = {
        "profiles": profiles,
    }
    return render(request, "autotrade/tuning_list.html", ctx)


@login_required
def tuning_new(request: HttpRequest):
    """
    新規作成（自由入力）
    """
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip() or "New Tuning"

        v_stop = _to_float(request.POST.get("vwap_stop_pct"), 0.25)
        v_rr = _to_float(request.POST.get("vwap_rr"), 1.5)
        v_pull = _to_float(request.POST.get("vwap_pullback_pct"), 0.05)
        v_hold = _to_int(request.POST.get("vwap_max_hold_min"), 30)

        b_stop = _to_float(request.POST.get("breakout_stop_pct"), 0.30)
        b_rr = _to_float(request.POST.get("breakout_rr"), 2.0)
        b_lb = _to_int(request.POST.get("breakout_lookback_bars"), 6)
        b_hold = _to_int(request.POST.get("breakout_max_hold_min"), 30)

        params = {
            "VWAP": {
                "stop_pct": float(v_stop),
                "rr": float(v_rr),
                "pullback_pct": float(v_pull),
                "max_hold_min": int(v_hold),
            },
            "BREAKOUT": {
                "stop_pct": float(b_stop),
                "rr": float(b_rr),
                "lookback_bars": int(b_lb),
                "max_hold_min": int(b_hold),
            },
        }

        AutoTradeTuningProfile.objects.create(
            user=request.user,
            name=name,
            params=params,
        )
        return redirect("autotrade:tuning_list")

    ctx = {
        "mode": "new",
        "profile": None,
        "params": _default_params(),
    }
    return render(request, "autotrade/tuning_edit.html", ctx)


@login_required
def tuning_edit(request: HttpRequest, pk: int):
    """
    編集（自由入力）
    """
    profile = get_object_or_404(AutoTradeTuningProfile, pk=pk, user=request.user)

    params = profile.params if isinstance(profile.params, dict) else _default_params()

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip() or profile.name

        v_stop = _to_float(request.POST.get("vwap_stop_pct"), (params.get("VWAP") or {}).get("stop_pct", 0.25))
        v_rr = _to_float(request.POST.get("vwap_rr"), (params.get("VWAP") or {}).get("rr", 1.5))
        v_pull = _to_float(request.POST.get("vwap_pullback_pct"), (params.get("VWAP") or {}).get("pullback_pct", 0.05))
        v_hold = _to_int(request.POST.get("vwap_max_hold_min"), (params.get("VWAP") or {}).get("max_hold_min", 30))

        b_stop = _to_float(request.POST.get("breakout_stop_pct"), (params.get("BREAKOUT") or {}).get("stop_pct", 0.30))
        b_rr = _to_float(request.POST.get("breakout_rr"), (params.get("BREAKOUT") or {}).get("rr", 2.0))
        b_lb = _to_int(request.POST.get("breakout_lookback_bars"), (params.get("BREAKOUT") or {}).get("lookback_bars", 6))
        b_hold = _to_int(request.POST.get("breakout_max_hold_min"), (params.get("BREAKOUT") or {}).get("max_hold_min", 30))

        new_params = {
            "VWAP": {
                "stop_pct": float(v_stop),
                "rr": float(v_rr),
                "pullback_pct": float(v_pull),
                "max_hold_min": int(v_hold),
            },
            "BREAKOUT": {
                "stop_pct": float(b_stop),
                "rr": float(b_rr),
                "lookback_bars": int(b_lb),
                "max_hold_min": int(b_hold),
            },
        }

        profile.name = name
        profile.params = new_params
        profile.updated_at = timezone.now()
        profile.save(update_fields=["name", "params", "updated_at"])

        return redirect("autotrade:tuning_list")

    ctx = {
        "mode": "edit",
        "profile": profile,
        "params": params,
    }
    return render(request, "autotrade/tuning_edit.html", ctx)


@login_required
@require_POST
def tuning_archive(request: HttpRequest, pk: int):
    """
    安全のための「削除」＝アーカイブ
    """
    profile = get_object_or_404(AutoTradeTuningProfile, pk=pk, user=request.user)
    profile.is_archived = True
    profile.updated_at = timezone.now()
    profile.save(update_fields=["is_archived", "updated_at"])
    return redirect("autotrade:tuning_list")


# =========================================================
# ★ 検証実行（BACKTEST）
# =========================================================
@login_required
@require_POST
def tuning_run_backtest(request: HttpRequest, pk: int):
    """
    TuningProfile の params を使って「検証用DRAFT Snapshot」を作り、
    詳細バックテストを回して evidence を焼き付け、結果ページへ遷移する。
    """
    profile = get_object_or_404(AutoTradeTuningProfile, pk=pk, user=request.user)

    today = timezone.localdate()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    # picks：無ければここで作る（実験室は自由実行OK）
    uni = state.universe if isinstance(state.universe, dict) else {}
    picks = [x.get("ticker") for x in (uni.get("picks") or []) if isinstance(x, dict) and x.get("ticker")]
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]

    if not picks:
        # まだ朝ジョブ前でも、ここで生成してOK（実験室）
        u = build_daily_universe(limit=10)
        picks = [x.get("ticker") for x in (u.get("picks") or []) if isinstance(x, dict) and x.get("ticker")]
        picks = [str(x).strip() for x in (picks or []) if str(x).strip()]

    # 実行（ACTIVEは触らない）
    result = run_backtest_for_tuning_profile(
        profile=profile,
        target_date=today,
        picks=picks,
        windows=[20, 60],
    )

    snap_id = int(result.get("snapshot_id") or 0)
    if snap_id <= 0:
        # 失敗：一覧へ戻す（最低限）
        return redirect("autotrade:tuning_list")

    return redirect("autotrade:tuning_result", snapshot_id=snap_id)


def _get_current_active_snapshot(user):
    return (
        AutoTradeSettingSnapshot.objects
        .filter(user=user, status="ACTIVE")
        .order_by("-id")
        .first()
    )


# =========================================================
# ★ 追加：結果ページから再検証（同条件 / picks更新）
# =========================================================

def _get_today_picks(user) -> List[str]:
    """
    今日の DailyState.universe から picks を取る。
    無ければ build_daily_universe で作る（実験室は自由実行OK）。
    """
    today = timezone.localdate()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    uni = state.universe if isinstance(state.universe, dict) else {}
    picks = [x.get("ticker") for x in (uni.get("picks") or []) if isinstance(x, dict) and x.get("ticker")]
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]

    if picks:
        return picks

    u = build_daily_universe(limit=10)
    picks = [x.get("ticker") for x in (u.get("picks") or []) if isinstance(x, dict) and x.get("ticker")]
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]

    # stateへも保存（次回から速く＆再現性）
    state.universe = u
    state.updated_at = timezone.now()
    state.save(update_fields=["universe", "updated_at"])

    return picks


def _collect_evidence_for_snapshot(
    *,
    snapshot: AutoTradeSettingSnapshot,
    target_date: date,
    windows: List[int],
) -> Dict[str, Any]:
    """
    既に作られている Execution を読み取り、window×strategyの metrics と gate を作る。
    （再計算ではなく “事実(Execution)” を読む）
    """
    by_window: Dict[int, Dict[str, Dict[str, Any]]] = {}

    snap_dict = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}
    base_equity = int(snap_dict.get("base_equity_yen", 1_000_000))

    strategies: Tuple[str, str] = ("VWAP", "BREAKOUT")

    for w in windows:
        w = int(w)
        by_window[w] = {}
        for strat in strategies:
            rd = (
                AutoTradeBacktestRunDetail.objects
                .filter(snapshot=snapshot, strategy=str(strat), window_days=w, trade_date=target_date)
                .order_by("-id")
                .first()
            )
            if not rd:
                by_window[w][strat] = {"trades": 0}
                continue

            qs = AutoTradeExecution.objects.filter(run_detail=rd).order_by("exit_at")
            m = summarize_executions(qs=qs, base_equity=base_equity)
            by_window[w][strat] = m

    metrics_vwap = {int(w): (by_window[int(w)].get("VWAP") or {}) for w in windows}
    metrics_breakout = {int(w): (by_window[int(w)].get("BREAKOUT") or {}) for w in windows}

    gate_vwap = judge_multi_window(metrics_vwap)
    gate_breakout = judge_multi_window(metrics_breakout)

    lv_v = str((gate_vwap or {}).get("gate_level") or "STOP")
    lv_b = str((gate_breakout or {}).get("gate_level") or "STOP")

    if lv_v == "STOP":
        final = "STOP"
        active = []
        disabled = ["VWAP", "BREAKOUT"]
    else:
        active = ["VWAP"]
        if lv_b == "FULL":
            final = "FULL"
            active.append("BREAKOUT")
            disabled = []
        else:
            final = "LIGHT"
            disabled = ["BREAKOUT"]

    return {
        "date": str(target_date),
        "windows": list(windows),
        "base_equity_yen": base_equity,
        "by_window": by_window,
        "gate": {
            "gate_level": final,
            "active": active,
            "disabled": disabled,
            "gate_vwap": gate_vwap,
            "gate_breakout": gate_breakout,
        },
    }


def _aggregate_score(metrics_by_window: Dict[int, Dict[str, Dict[str, Any]]], *, windows: List[int]) -> Dict[str, Any]:
    """
    UI比較用の “ざっくりスコア”。
    - VWAP中心（あなたの思想：VWAP STOPなら最終STOP）
    """
    def _safe_int(x: Any, default: int = 0) -> int:
        try:
            return int(x)
        except Exception:
            return int(default)

    def _safe_float(x: Any, default: float = 0.0) -> float:
        try:
            return float(x)
        except Exception:
            return float(default)

    pnl = 0
    dd_pct = 0.0
    pf_sum = 0.0
    trades = 0

    for w in windows:
        m = ((metrics_by_window.get(int(w)) or {}).get("VWAP") or {})
        pnl += _safe_int(m.get("total_pnl"), 0)
        dd_pct = max(dd_pct, _safe_float(m.get("max_drawdown_pct"), 0.0))
        pf_sum += _safe_float(m.get("profit_factor"), 0.0)
        trades += _safe_int(m.get("trades"), 0)

    pf_avg = pf_sum / max(1, len(windows))

    return {
        "pnl_sum_yen": int(pnl),
        "max_dd_pct": float(dd_pct),
        "pf_avg": float(pf_avg),
        "trades_sum": int(trades),
    }


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """
    a - b を返す。片方NoneならNone。
    """
    if a is None or b is None:
        return None
    try:
        return float(a) - float(b)
    except Exception:
        return None


def _int_delta(a: Any, b: Any) -> Optional[int]:
    if a is None or b is None:
        return None
    try:
        return int(a) - int(b)
    except Exception:
        return None


def _build_diff_rows(
    *,
    base_by_window: Dict[int, Dict[str, Dict[str, Any]]],
    cand_by_window: Dict[int, Dict[str, Dict[str, Any]]],
    windows: List[int],
) -> List[Dict[str, Any]]:
    """
    戦略×期間の差分（Δ）を作る。UIでそのまま出せる形。
    """
    rows: List[Dict[str, Any]] = []
    strategies = ["VWAP", "BREAKOUT"]

    for strat in strategies:
        for w in windows:
            b = ((base_by_window.get(int(w)) or {}).get(strat) or {})
            c = ((cand_by_window.get(int(w)) or {}).get(strat) or {})

            rows.append({
                "strategy": strat,
                "window": str(w),
                "base": {
                    "pnl": b.get("total_pnl"),
                    "pf": b.get("profit_factor"),
                    "dd_yen": b.get("max_drawdown_yen"),
                    "dd_pct": b.get("max_drawdown_pct"),
                    "trades": b.get("trades"),
                },
                "cand": {
                    "pnl": c.get("total_pnl"),
                    "pf": c.get("profit_factor"),
                    "dd_yen": c.get("max_drawdown_yen"),
                    "dd_pct": c.get("max_drawdown_pct"),
                    "trades": c.get("trades"),
                },
                "delta": {
                    "pnl": _int_delta(c.get("total_pnl"), b.get("total_pnl")),
                    "pf": _delta(c.get("profit_factor"), b.get("profit_factor")),
                    "dd_yen": _int_delta(c.get("max_drawdown_yen"), b.get("max_drawdown_yen")),
                    "dd_pct": _delta(c.get("max_drawdown_pct"), b.get("max_drawdown_pct")),
                    "trades": _int_delta(c.get("trades"), b.get("trades")),
                }
            })

    return rows


@login_required
@require_POST
def tuning_rerun_snapshot_force(request: HttpRequest, snapshot_id: int):
    """
    結果ページから：
    - “同条件（picksは今日のpicks）” で snapshot の検証をやり直す
    - Executionは force=True で作り直す（増殖防止）
    """
    snap = get_object_or_404(AutoTradeSettingSnapshot, pk=snapshot_id, user=request.user)

    today = timezone.localdate()
    picks = _get_today_picks(request.user)

    if not picks:
        return redirect("autotrade:tuning_result", snapshot_id=snapshot_id)

    windows = [20, 60]

    sdict = snap.snapshot if isinstance(snap.snapshot, dict) else {}
    tune = sdict.get("tune") if isinstance(sdict.get("tune"), dict) else {}

    rr_b = tune.get("rr_breakout", None)
    rr_v = tune.get("rr_vwap", None)

    run_detailed_backtests_for_universe(
        snapshot=snap,
        picks=picks,
        target_date=today,
        windows=tuple(int(x) for x in windows),
        rr_breakout=float(rr_b) if rr_b is not None else None,
        rr_vwap=float(rr_v) if rr_v is not None else None,
        base_equity_yen=None,
        force=True,
    )

    return redirect("autotrade:tuning_result", snapshot_id=snapshot_id)


@login_required
@require_POST
def tuning_rerun_snapshot_refresh_picks(request: HttpRequest, snapshot_id: int):
    """
    結果ページから：
    - picksを作り直して（universe再生成）
    - その上で snapshot の検証をやり直す
    """
    snap = get_object_or_404(AutoTradeSettingSnapshot, pk=snapshot_id, user=request.user)

    today = timezone.localdate()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    u = build_daily_universe(limit=10)
    state.universe = u
    state.updated_at = timezone.now()
    state.save(update_fields=["universe", "updated_at"])

    picks = [x.get("ticker") for x in (u.get("picks") or []) if isinstance(x, dict) and x.get("ticker")]
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]

    if not picks:
        return redirect("autotrade:tuning_result", snapshot_id=snapshot_id)

    windows = [20, 60]

    sdict = snap.snapshot if isinstance(snap.snapshot, dict) else {}
    tune = sdict.get("tune") if isinstance(sdict.get("tune"), dict) else {}

    rr_b = tune.get("rr_breakout", None)
    rr_v = tune.get("rr_vwap", None)

    run_detailed_backtests_for_universe(
        snapshot=snap,
        picks=picks,
        target_date=today,
        windows=tuple(int(x) for x in windows),
        rr_breakout=float(rr_b) if rr_b is not None else None,
        rr_vwap=float(rr_v) if rr_v is not None else None,
        base_equity_yen=None,
        force=True,
    )

    return redirect("autotrade:tuning_result", snapshot_id=snapshot_id)


@login_required
def tuning_result(request: HttpRequest, snapshot_id: int):
    """
    検証結果（読みやすさ重視）
    - Snapshot.snapshot['evidence'] を表示する
    - 理由（日本語＋数字＋円）は折りたたみ（テンプレ側）
    - 追加：どのtuningかが分かるヘッダ用の summary 情報
    - 追加：ACTIVE比 / 直前比 の差分（Δ）
    - 追加：その場で再検証ボタン用の情報
    """
    snap = get_object_or_404(AutoTradeSettingSnapshot, pk=snapshot_id, user=request.user)

    sdict = snap.snapshot if isinstance(snap.snapshot, dict) else {}
    evidence = sdict.get("evidence") if isinstance(sdict.get("evidence"), dict) else {}
    gate = evidence.get("gate") if isinstance(evidence.get("gate"), dict) else {}
    by_window = evidence.get("by_window") if isinstance(evidence.get("by_window"), dict) else {}

    windows = [20, 60]
    strategies = ["VWAP", "BREAKOUT"]

    cards = []
    for strat in strategies:
        for w in windows:
            m = _get_nested_dict(by_window, int(w), strat, default={})
            if not isinstance(m, dict):
                m = {}
            cards.append({
                "strategy": strat,
                "window": str(w),
                "trades": m.get("trades"),
                "wins": m.get("wins"),
                "losses": m.get("losses"),
                "win_rate": m.get("win_rate"),
                "sum_win_yen": m.get("sum_win_yen"),
                "sum_loss_yen": m.get("sum_loss_yen"),
                "pf": m.get("profit_factor"),
                "dd_yen": m.get("max_drawdown_yen"),
                "dd_pct": m.get("max_drawdown_pct"),
                "pnl": m.get("total_pnl"),
            })

    active = _get_current_active_snapshot(request.user)
    last_promo = (
        AutoTradePromotionLog.objects
        .filter(user=request.user)
        .order_by("-id")
        .first()
    )

    # ---------------------------
    # ヘッダ：何を検証したか
    # ---------------------------
    tune = sdict.get("tune") if isinstance(sdict.get("tune"), dict) else {}
    today = timezone.localdate()
    picks_today = _get_today_picks(request.user)
    picks_head = picks_today[:3]

    header = {
        "status": snap.status,
        "snapshot_id": snap.id,
        "label": snap.label,
        "profile_name": (snap.source_profile.name if snap.source_profile else None),
        "date": evidence.get("date") or str(today),
        "windows": evidence.get("windows") or windows,
        "picks_n": len(picks_today),
        "picks_head": picks_head,
        "tune": {
            "knob": tune.get("knob"),
            "delta": tune.get("delta"),
            "rr_breakout": tune.get("rr_breakout"),
            "rr_vwap": tune.get("rr_vwap"),
            "stop_pct_vwap": tune.get("stop_pct_vwap"),
            "based_on_active_id": tune.get("based_on_active_id"),
            "target_date": tune.get("target_date"),
        } if tune else None,
    }

    # ---------------------------
    # 比較：ACTIVE比
    # ---------------------------
    diff_vs_active = None
    if active and active.id != snap.id:
        ev_active = _collect_evidence_for_snapshot(snapshot=active, target_date=today, windows=list(windows))
        base_by_window = ev_active.get("by_window") if isinstance(ev_active.get("by_window"), dict) else {}
        cand_by_window = {}

        for w in windows:
            d = by_window.get(int(w)) if isinstance(by_window.get(int(w)), dict) else by_window.get(str(int(w)))
            d = d if isinstance(d, dict) else {}
            cand_by_window[int(w)] = {
                "VWAP": d.get("VWAP") if isinstance(d.get("VWAP"), dict) else {},
                "BREAKOUT": d.get("BREAKOUT") if isinstance(d.get("BREAKOUT"), dict) else {},
            }

        diff_vs_active = {
            "title": f"比較：ACTIVE（id={active.id}）→ この検証（id={snap.id}）",
            "rows": _build_diff_rows(base_by_window=base_by_window, cand_by_window=cand_by_window, windows=list(windows)),
            "base_score": _aggregate_score(base_by_window, windows=list(windows)),
            "cand_score": _aggregate_score(cand_by_window, windows=list(windows)),
        }

    # ---------------------------
    # 比較：直前の同ノブ試行
    # ---------------------------
    diff_vs_prev = None
    if isinstance(tune, dict) and tune.get("knob"):
        knob = str(tune.get("knob"))
        based_on = tune.get("based_on_active_id", None)

        qs = AutoTradeSettingSnapshot.objects.filter(
            user=request.user,
            snapshot__tune__knob=knob,
        ).exclude(id=snap.id).order_by("-id")

        if based_on is not None:
            qs = qs.filter(snapshot__tune__based_on_active_id=int(based_on))

        prev = qs.first()

        if prev:
            prev_dict = prev.snapshot if isinstance(prev.snapshot, dict) else {}
            prev_ev = prev_dict.get("evidence") if isinstance(prev_dict.get("evidence"), dict) else {}
            prev_bw = prev_ev.get("by_window") if isinstance(prev_ev.get("by_window"), dict) else {}

            prev_by_window: Dict[int, Dict[str, Dict[str, Any]]] = {}
            cand_by_window: Dict[int, Dict[str, Dict[str, Any]]] = {}

            for w in windows:
                pd = prev_bw.get(int(w)) if isinstance(prev_bw.get(int(w)), dict) else prev_bw.get(str(int(w)))
                pd = pd if isinstance(pd, dict) else {}
                prev_by_window[int(w)] = {
                    "VWAP": pd.get("VWAP") if isinstance(pd.get("VWAP"), dict) else {},
                    "BREAKOUT": pd.get("BREAKOUT") if isinstance(pd.get("BREAKOUT"), dict) else {},
                }

                cd = by_window.get(int(w)) if isinstance(by_window.get(int(w)), dict) else by_window.get(str(int(w)))
                cd = cd if isinstance(cd, dict) else {}
                cand_by_window[int(w)] = {
                    "VWAP": cd.get("VWAP") if isinstance(cd.get("VWAP"), dict) else {},
                    "BREAKOUT": cd.get("BREAKOUT") if isinstance(cd.get("BREAKOUT"), dict) else {},
                }

            diff_vs_prev = {
                "title": f"比較：直前の同ノブ（id={prev.id}）→ 今回（id={snap.id}）",
                "prev_id": prev.id,
                "rows": _build_diff_rows(base_by_window=prev_by_window, cand_by_window=cand_by_window, windows=list(windows)),
                "base_score": _aggregate_score(prev_by_window, windows=list(windows)),
                "cand_score": _aggregate_score(cand_by_window, windows=list(windows)),
            }

    ctx = {
        "snapshot": snap,
        "profile": snap.source_profile,
        "evidence": evidence,
        "gate": gate,
        "cards": cards,
        "active_snapshot": active,
        "last_promo": last_promo,
        "header": header,
        "diff_vs_active": diff_vs_active,
        "diff_vs_prev": diff_vs_prev,
    }
    return render(request, "autotrade/tuning_result.html", ctx)


# =========================================================
# ★ CANDIDATE保存（DRAFT → CANDIDATE）
# =========================================================
@login_required
@require_POST
@transaction.atomic
def tuning_make_candidate(request: HttpRequest, snapshot_id: int):
    snap = get_object_or_404(AutoTradeSettingSnapshot, pk=snapshot_id, user=request.user)

    # すでにACTIVE/RETIREDなら触らない（安全）
    if snap.status in ["ACTIVE", "RETIRED"]:
        return redirect("autotrade:tuning_result", snapshot_id=snapshot_id)

    if snap.status != "CANDIDATE":
        snap.status = "CANDIDATE"
        snap.save(update_fields=["status"])

    AutoTradePromotionLog.objects.create(
        user=request.user,
        action="MAKE_CANDIDATE",
        from_snapshot=None,
        to_snapshot=snap,
        note="manual_from_result",
    )
    return redirect("autotrade:tuning_result", snapshot_id=snapshot_id)


# =========================================================
# ★ ACTIVE昇格（旧ACTIVEはRETIREDへ）
# =========================================================
@login_required
@require_POST
@transaction.atomic
def tuning_promote_active(request: HttpRequest, snapshot_id: int):
    target = get_object_or_404(AutoTradeSettingSnapshot, pk=snapshot_id, user=request.user)

    # RETIREDは昇格不可（安全）
    if target.status == "RETIRED":
        return redirect("autotrade:tuning_result", snapshot_id=snapshot_id)

    old_active = (
        AutoTradeSettingSnapshot.objects
        .select_for_update()
        .filter(user=request.user, status="ACTIVE")
        .order_by("-id")
        .first()
    )

    # 旧ACTIVEを退役
    if old_active and old_active.id != target.id:
        old_active.status = "RETIRED"
        old_active.save(update_fields=["status"])

    # ターゲットをACTIVEへ（DRAFTでもCANDIDATEでもOK）
    target.status = "ACTIVE"
    target.save(update_fields=["status"])

    AutoTradePromotionLog.objects.create(
        user=request.user,
        action="PROMOTE",
        from_snapshot=old_active,
        to_snapshot=target,
        note="manual_promote_from_result",
    )

    return redirect("autotrade:tuning_result", snapshot_id=snapshot_id)


# =========================================================
# ★ ロールバック（直前PROMOTEの from_snapshot に戻す）
# =========================================================
@login_required
@require_POST
@transaction.atomic
def tuning_rollback_active(request: HttpRequest, snapshot_id: int):
    # 画面にいるsnapshot_idは「戻る」先ではなく、操作地点の目印として受け取るだけ
    # 実際は最後のPROMOTEログに従う（安全）
    last_promote = (
        AutoTradePromotionLog.objects
        .select_for_update()
        .filter(user=request.user, action="PROMOTE")
        .order_by("-id")
        .first()
    )
    if not last_promote:
        return redirect("autotrade:tuning_result", snapshot_id=snapshot_id)

    prev_active = last_promote.from_snapshot
    promoted = last_promote.to_snapshot

    # prevが無い＝「初回昇格」だった場合はロールバック不可（安全）
    if not prev_active:
        return redirect("autotrade:tuning_result", snapshot_id=snapshot_id)

    # DB上の最新状態をロックして取得（整合性）
    prev_active = get_object_or_404(AutoTradeSettingSnapshot, pk=prev_active.id, user=request.user)
    promoted = get_object_or_404(AutoTradeSettingSnapshot, pk=promoted.id, user=request.user)

    # 現ACTIVEをRETIRED、prevをACTIVEへ
    # promotedがACTIVEでない場合もあり得るが、事故らないように一律RETIRED寄せ
    promoted.status = "RETIRED"
    promoted.save(update_fields=["status"])

    prev_active.status = "ACTIVE"
    prev_active.save(update_fields=["status"])

    AutoTradePromotionLog.objects.create(
        user=request.user,
        action="ROLLBACK",
        from_snapshot=promoted,
        to_snapshot=prev_active,
        note="manual_rollback_from_result",
    )

    return redirect("autotrade:tuning_result", snapshot_id=prev_active.id)


# =========================================================
# ★ 非常停止 API（ワンタップ）
# =========================================================
@login_required
@require_POST
def api_emergency_stop(request: HttpRequest):
    """
    今日の AutoTradeDailyState を非常停止にする。
    - emergency_stop=True
    - 理由・時刻を保存
    - gate_level も STOP に倒して UI でも即わかるようにする
    """
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    if state.emergency_stop:
        return JsonResponse({
            "ok": True,
            "already": True,
            "message": "already_stopped",
        })

    state.emergency_stop = True
    state.emergency_stopped_at = timezone.now()
    state.emergency_stop_reason = "manual"

    state.gate_level = "STOP"
    add_reason = "非常停止（手動）"
    if state.gate_reason:
        if add_reason not in state.gate_reason:
            state.gate_reason = (state.gate_reason.rstrip() + "\n" + add_reason)
    else:
        state.gate_reason = add_reason

    state.updated_at = timezone.now()
    state.save()

    return JsonResponse({
        "ok": True,
        "already": False,
        "message": "stopped",
        "stopped_at": timezone.localtime(state.emergency_stopped_at).isoformat() if state.emergency_stopped_at else None,
    })
