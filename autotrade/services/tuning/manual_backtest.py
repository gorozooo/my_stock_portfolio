"""
[FILE] autotrade/services/tuning/manual_backtest.py
[PATH] <project_root>/autotrade/services/tuning/manual_backtest.py

このファイルは何？
- 「実験室（TuningProfile）」の検証（BACKTEST）を実行するサービスです。
- ACTIVEは触らず、DRAFT Snapshot を作って Execution を生成し、gate.py の日本語理由（数値＋円）付きで evidence を焼き付けます。

重要：
- UIは自由操作。安全は「ACTIVE昇格しない」「ロールバックで戻せる」で担保。
- 集計値は保存しない原則は維持：保存するのは Execution と RunDetail と “evidence（表示用）” のみ。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import date as dt_date

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from autotrade.models import AutoTradeTuningProfile, AutoTradeSettingSnapshot
from autotrade.models_backtest import AutoTradeBacktestRunDetail, AutoTradeExecution

from autotrade.services.backtest.execution_metrics import summarize_executions
from autotrade.services.backtest.gate import judge_multi_window

# ★ “詳細”エンジン：stop_pct を snapshot から読む想定のやつ
from autotrade.services.backtest.engine_vwap_detail import run_vwap_detail
from autotrade.services.backtest.engine_breakout_detail import run_breakout_detail


def _pct_to_ratio(pct: float) -> float:
    """
    UIは「0.25 (=0.25%)」で入力する設計なので、
    エンジン用に 0.0025 に変換する。
    """
    try:
        return float(pct) / 100.0
    except Exception:
        return 0.0


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def _build_snapshot_dict_from_profile(profile: AutoTradeTuningProfile) -> Dict[str, Any]:
    """
    profile.params（自由）を、エンジンが読む snapshot dict に落とす。
    ここは “実験室の翻訳層”。
    """
    p = profile.params if isinstance(profile.params, dict) else {}
    v = p.get("VWAP") if isinstance(p.get("VWAP"), dict) else {}
    b = p.get("BREAKOUT") if isinstance(p.get("BREAKOUT"), dict) else {}

    # UIは%表記（0.25）→ ratio（0.0025）
    v_stop = _pct_to_ratio(_safe_float(v.get("stop_pct"), 0.25))
    b_stop = _pct_to_ratio(_safe_float(b.get("stop_pct"), 0.30))

    # RRはそのまま
    rr_v = _safe_float(v.get("rr"), 1.5)
    rr_b = _safe_float(b.get("rr"), 2.0)

    # 追加ノブ（今は表示/UI用。エンジン側で参照するなら後で接続）
    pullback_pct = _pct_to_ratio(_safe_float(v.get("pullback_pct"), 0.05))  # ratio
    lookback_bars = _safe_int(b.get("lookback_bars"), 6)
    max_hold_v = _safe_int(v.get("max_hold_min"), 30)
    max_hold_b = _safe_int(b.get("max_hold_min"), 30)

    snap = {
        "base_equity_yen": int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)),

        # エンジン側が読むキー（detailエンジンに合わせる）
        "vwap_stop_pct": float(v_stop),
        "breakout_stop_pct": float(b_stop),

        # 実験室の“人間向け”原本も残す（UIで表示に使える）
        "lab": {
            "VWAP": {
                "stop_pct_ui": float(_safe_float(v.get("stop_pct"), 0.25)),
                "rr": float(rr_v),
                "pullback_pct_ui": float(_safe_float(v.get("pullback_pct"), 0.05)),
                "pullback_pct_ratio": float(pullback_pct),
                "max_hold_min": int(max_hold_v),
            },
            "BREAKOUT": {
                "stop_pct_ui": float(_safe_float(b.get("stop_pct"), 0.30)),
                "rr": float(rr_b),
                "lookback_bars": int(lookback_bars),
                "max_hold_min": int(max_hold_b),
            },
        },
    }

    return snap


def _merge_gate_results(*, gate_vwap: Dict[str, Any], gate_breakout: Dict[str, Any]) -> Dict[str, Any]:
    """
    runner / auto_promote と同じ思想の統合（安全優先）
    - VWAPがSTOPなら最終STOP
    - VWAPが生きていて、BREAKOUTがFULLなら最終FULL
    - それ以外はLIGHT（VWAPのみ）
    """
    lv_v = str((gate_vwap or {}).get("gate_level") or "STOP")
    lv_b = str((gate_breakout or {}).get("gate_level") or "STOP")

    active: List[str] = []
    disabled: List[str] = []

    if lv_v == "STOP":
        final = "STOP"
        disabled = ["VWAP", "BREAKOUT"]
    else:
        active.append("VWAP")
        if lv_b == "FULL":
            final = "FULL"
            active.append("BREAKOUT")
        else:
            final = "LIGHT"
            disabled.append("BREAKOUT")

    return {
        "gate_level": final,
        "active": active,
        "disabled": disabled,
        "gate_vwap": gate_vwap,
        "gate_breakout": gate_breakout,
    }


@transaction.atomic
def run_backtest_for_tuning_profile(
    *,
    profile: AutoTradeTuningProfile,
    target_date: dt_date,
    picks: List[str],
    windows: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    実験室の検証を1回実行する。
    - DRAFT Snapshot を作る
    - window×strategy の run_detail + Execution を生成する
    - evidence を snapshot に焼き付ける
    - 結果ページ表示用のデータを返す
    """
    if windows is None:
        windows = [20, 60]

    windows = [int(x) for x in (windows or [])]
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]

    # 1) DRAFT Snapshot 作成（固定）
    snap_dict = _build_snapshot_dict_from_profile(profile)

    label = f"LAB {target_date} {profile.name}".strip()[:100]
    snap = AutoTradeSettingSnapshot.objects.create(
        user=profile.user,
        source_profile=profile,
        label=label,
        status="DRAFT",
        snapshot=snap_dict,
    )

    base_equity = int(snap_dict.get("base_equity_yen", 1_000_000))

    # 2) 実行：window×strategy
    metrics_by_window: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for w in windows:
        metrics_by_window[int(w)] = {}

        for strat in ["VWAP", "BREAKOUT"]:
            # 既存の同日同条件があっても、実験室は「毎回新しいDRAFT」なので衝突しない設計
            rd = AutoTradeBacktestRunDetail.objects.create(
                user=profile.user,
                snapshot=snap,
                strategy=str(strat),
                window_days=int(w),
                start_date=target_date,
                end_date=target_date,
                trade_date=target_date,  # ★ あなたの現行DBに合わせて trade_date を使う
            )

            # Execution生成
            for ticker in picks:
                if strat == "VWAP":
                    run_vwap_detail(
                        ticker=ticker,
                        window_days=int(w),
                        rr=float((snap_dict.get("lab") or {}).get("VWAP", {}).get("rr", 1.5)),
                        snapshot=snap,
                        mode="BACKTEST",
                        run_meta=rd,
                        target_date=target_date,
                    )
                else:
                    run_breakout_detail(
                        ticker=ticker,
                        window_days=int(w),
                        rr=float((snap_dict.get("lab") or {}).get("BREAKOUT", {}).get("rr", 2.0)),
                        snapshot=snap,
                        mode="BACKTEST",
                        run_meta=rd,
                        target_date=target_date,
                    )

            # 集計（保存しない原則）
            qs = AutoTradeExecution.objects.filter(run_detail=rd).order_by("exit_at")
            m = summarize_executions(qs=qs, base_equity=base_equity)
            metrics_by_window[int(w)][str(strat)] = m

    # 3) gate 判定（gate.pyを正として使う）
    metrics_vwap = {int(w): (metrics_by_window[int(w)].get("VWAP") or {}) for w in windows}
    metrics_breakout = {int(w): (metrics_by_window[int(w)].get("BREAKOUT") or {}) for w in windows}

    gate_vwap = judge_multi_window(metrics_vwap)
    gate_breakout = judge_multi_window(metrics_breakout)
    merged = _merge_gate_results(gate_vwap=gate_vwap, gate_breakout=gate_breakout)

    # 4) evidence を焼き付け（UI表示の主役）
    sdict = snap.snapshot if isinstance(snap.snapshot, dict) else {}
    sdict["manual_eval"] = {
        "evaluated_at": timezone.localtime(timezone.now()).isoformat(),
        "date": str(target_date),
        "windows": list(windows),
        "note": "manual_backtest_from_ui",
    }
    sdict["evidence"] = {
        "date": str(target_date),
        "base_equity_yen": int(base_equity),
        "gate": merged,
        "by_window": metrics_by_window,
    }
    snap.snapshot = sdict
    snap.save(update_fields=["snapshot"])

    return {
        "ok": True,
        "snapshot_id": int(snap.id),
        "gate": merged,
        "metrics_by_window": metrics_by_window,
    }