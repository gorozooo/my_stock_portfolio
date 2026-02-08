"""
[FILE] autotrade/views.py
[PATH] <project_root>/autotrade/views.py

このファイルは何？
- AutoTrade のダッシュボード表示（iPhone 1画面）を作る View です。
- DailyState（今日の状態）を読み、テンプレに渡す表示用データ（バックテスト行や理由文）を整形します。
- さらに「実験室（TuningProfile）」として、一覧・新規作成・編集・アーカイブ（安全削除）を提供します。

今回のポイント：
- runner.py が保存する新フォーマット（state.backtest = {meta/by_window/gate}）に追従。
- VWAP / BREAKOUT の両方の結果を表示できるように整形して返します。
- 実験室UIは「全パラメータ表示・全変更OK」を前提に、入口（一覧）だけでなく新規作成/編集/アーカイブまで対応。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    AutoTradeDailyState,
    AutoTradeTuningProfile,
)

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
    テンプレ側で key 参照に詰まらないように、
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
        # 既存の state.gate_reason（runner が合成した最終理由）は “最終まとめ” として活かす
        "final_reason_text": (state.gate_reason or "").strip(),
    }


@login_required
def dashboard(request: HttpRequest):
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    gate_bundle = _build_gate_bundle_for_template(state)
    bt_rows_by_strategy = _build_bt_rows_by_strategy_for_template(state)

    # 初期タブ（今日の戦略があればそれ / 無ければVWAP）
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
    - この段階では危険値の禁止はしない（UI側で警告に留める設計）
    """
    return {
        "VWAP": {
            "stop_pct": 0.25,        # %（0.25%）
            "rr": 1.5,
            "pullback_pct": 0.05,    # %（VWAP乖離許容 0.05%）
            "max_hold_min": 30,      # 分
        },
        "BREAKOUT": {
            "stop_pct": 0.30,        # %（0.30%）
            "rr": 2.0,
            "lookback_bars": 6,      # 5分足 本数
            "max_hold_min": 30,      # 分
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


# =========================================================
# TuningProfile List（入口）
# =========================================================
@login_required
def tuning_list(request: HttpRequest):
    """
    調整用プロファイルの一覧（入口）
    - ここでは数値編集しない（編集画面へ誘導）
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


# =========================================================
# TuningProfile New（新規作成）
# =========================================================
@login_required
def tuning_new(request: HttpRequest):
    """
    新規作成（自由入力）
    """
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip() or "New Tuning"

        # VWAP
        v_stop = _to_float(request.POST.get("vwap_stop_pct"), 0.25)
        v_rr = _to_float(request.POST.get("vwap_rr"), 1.5)
        v_pull = _to_float(request.POST.get("vwap_pullback_pct"), 0.05)
        v_hold = _to_int(request.POST.get("vwap_max_hold_min"), 30)

        # BREAKOUT
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


# =========================================================
# TuningProfile Edit（編集）
# =========================================================
@login_required
def tuning_edit(request: HttpRequest, pk: int):
    """
    編集（自由入力）
    """
    profile = get_object_or_404(AutoTradeTuningProfile, pk=pk, user=request.user)

    params = profile.params if isinstance(profile.params, dict) else _default_params()

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip() or profile.name

        # VWAP
        v_stop = _to_float(request.POST.get("vwap_stop_pct"), (params.get("VWAP") or {}).get("stop_pct", 0.25))
        v_rr = _to_float(request.POST.get("vwap_rr"), (params.get("VWAP") or {}).get("rr", 1.5))
        v_pull = _to_float(request.POST.get("vwap_pullback_pct"), (params.get("VWAP") or {}).get("pullback_pct", 0.05))
        v_hold = _to_int(request.POST.get("vwap_max_hold_min"), (params.get("VWAP") or {}).get("max_hold_min", 30))

        # BREAKOUT
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


# =========================================================
# TuningProfile Archive（安全削除）
# =========================================================
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

    # UIが即「停止」になるように倒す（理由は既存があれば追記）
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