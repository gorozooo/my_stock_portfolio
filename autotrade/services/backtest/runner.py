"""
[FILE] autotrade/services/backtest/runner.py
[PATH] <project_root>/autotrade/services/backtest/runner.py

詳細バックテストの司令塔。

役割：
- Snapshot（固定設定）を入力として
- Execution（事実ログ）を生成
- Execution を集計
- gate 判定（FULL / LIGHT / STOP）
- DailyState を更新

重要：
- 集計値は保存しない
- 事実（Execution）だけが真実
"""

from __future__ import annotations

from typing import Dict, List, Any, Optional, Tuple
from datetime import date as dt_date

from django.db import transaction
from django.utils import timezone
from django.conf import settings

from autotrade.models import AutoTradeDailyState, AutoTradeSettingSnapshot
from autotrade.models_backtest import (
    AutoTradeExecution,
    AutoTradeBacktestRunDetail,
)

from autotrade.services.backtest.execution_metrics import summarize_executions
from autotrade.services.backtest.gate import judge_multi_window
from autotrade.services.common.guards import is_emergency_stopped

# エンジン（Execution を吐く / 互換で旧集計も返せる）
from autotrade.services.backtest.engine_breakout import run_breakout
from autotrade.services.backtest.engine_vwap import run_vwap


DEFAULT_BACKTEST_WINDOWS: Tuple[int, int, int] = (20, 60, 120)
STRATEGIES: Tuple[str, str] = ("BREAKOUT", "VWAP")


def _merge_gate_results(
    *,
    gate_vwap: Dict[str, Any],
    gate_breakout: Dict[str, Any],
    rr_breakout: float,
    rr_vwap: float,
    windows: Tuple[int, ...],
) -> Dict[str, Any]:
    """
    戦略別ゲートを統合して、最終の gate_level / 理由 / 稼働戦略 を決める。

    ルール（おすすめ・確定）：
    - VWAPがSTOPなら最終STOP（安全第一）
    - VWAPがLIGHT/FULLで、BREAKOUTがFULLなら最終FULL（両方OK）
    - それ以外は最終LIGHT（VWAPのみ稼働）

    重要：
    - “理由文の中身（数字/円など）”は gate.py に一本化する。
      runner.py は見出し（構造）だけ足す。
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

    # 理由文：
    # - gate.py の reasons（円入り）をそのまま表示する
    # - runner は構造だけ提供
    reasons: List[str] = []

    # 最終サマリ（ここは“数字を入れない”＝gate.pyの主役化）
    if final == "FULL":
        reasons.append("【最終判定】FULL（VWAP + BREAKOUT 稼働）")
    elif final == "LIGHT":
        reasons.append("【最終判定】LIGHT（VWAP のみ稼働）")
    else:
        reasons.append("【最終判定】STOP（安全のため稼働しない）")

    reasons.append(f"【設定】windows={list(windows)} / RR(BREAKOUT)={rr_breakout:.2f} / RR(VWAP)={rr_vwap:.2f}")

    # VWAP（gate.pyの理由をそのまま）
    if gate_vwap:
        rs = gate_vwap.get("reasons") or []
        if rs:
            reasons.append("【VWAP判定】")
            reasons.extend([str(x) for x in rs if str(x).strip()])

    # BREAKOUT（gate.pyの理由をそのまま）
    if gate_breakout:
        rs = gate_breakout.get("reasons") or []
        if rs:
            reasons.append("【BREAKOUT判定】")
            reasons.extend([str(x) for x in rs if str(x).strip()])

    return {
        "gate_level": final,
        "reasons": reasons,
        "active_strategies": active,
        "disabled_strategies": disabled,
        "gate_vwap": gate_vwap,
        "gate_breakout": gate_breakout,
    }


# =========================================================
# 詳細バックテスト（Execution基準）
# =========================================================
@transaction.atomic
def run_detailed_backtests_for_universe(
    *,
    snapshot: AutoTradeSettingSnapshot,
    picks: List[str],
    target_date: Optional[dt_date] = None,
    windows: Optional[Tuple[int, ...]] = None,
    rr_breakout: Optional[float] = None,
    rr_vwap: Optional[float] = None,
    base_equity_yen: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    詳細バックテストを実行し、DailyState を更新する。

    - snapshot: ACTIVE Snapshot（必須）
    - picks: 今日の銘柄リスト
    - windows: 実行期間（例: (20,60,120)）
    - rr_breakout / rr_vwap: RR（設定値）
    - base_equity_yen: 基準資産（Snapshot優先、なければsettings）
    - force: True の場合、既存 Execution を削除して再実行
    """

    if target_date is None:
        target_date = timezone.localdate()

    state, _ = AutoTradeDailyState.objects.get_or_create(date=target_date)

    # -----------------------------------------------------
    # 非常停止ガード
    # -----------------------------------------------------
    if is_emergency_stopped(state):
        return {"skipped": True, "reason": "emergency_stop"}

    if snapshot is None:
        raise ValueError("ACTIVE snapshot が存在しません。")

    # -----------------------------------------------------
    # 入力の正規化
    # -----------------------------------------------------
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]
    bt_windows = tuple(int(x) for x in (windows or tuple(getattr(settings, "AUTOTRADE_BT_WINDOWS", DEFAULT_BACKTEST_WINDOWS))))
    rr_b = float(rr_breakout if rr_breakout is not None else getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0))
    rr_v = float(rr_vwap if rr_vwap is not None else getattr(settings, "AUTOTRADE_RR_VWAP", 1.5))

    snap_dict = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}
    if base_equity_yen is not None:
        base_equity = int(base_equity_yen)
    else:
        base_equity = int(snap_dict.get("base_equity_yen", getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)))

    user = snapshot.user

    # -----------------------------------------------------
    # 既存データ削除（force）
    #   - run_detail基準で掃除（created_at/date混在事故を避ける）
    # -----------------------------------------------------
    if force:
        # 同日の run_detail を拾ってまとめて消す
        details = AutoTradeBacktestRunDetail.objects.filter(
            snapshot=snapshot,
            executed_at__date=target_date,
        )
        AutoTradeExecution.objects.filter(run_detail__in=details).delete()
        details.delete()

    # picks が空なら何もしない（ただし状態は残す）
    if not picks:
        state.gate_level = "STOP"
        state.gate_reason = "銘柄が0件のため、詳細バックテストを実行できません。"
        state.strategy = ""
        state.strategy_decision = {
            "mode": "STOP",
            "active": [],
            "disabled": ["VWAP", "BREAKOUT"],
            "note": "no_picks",
        }
        state.backtest = {
            "meta": {"date": str(target_date), "note": "no_picks"},
            "by_window": {},
            "gate": {},
        }
        state.updated_at = timezone.now()
        state.save()
        return {"ok": False, "reason": "no_picks"}

    # =====================================================
    # window × strategy のメトリクス
    #   metrics_by_window_strategy[window][strategy] = metrics
    # =====================================================
    metrics_by_window_strategy: Dict[int, Dict[str, Dict[str, Any]]] = {}

    for window in bt_windows:
        metrics_by_window_strategy[int(window)] = {}

        for strategy in STRATEGIES:
            # -------------------------------------------------
            # 既存 run_detail を再利用（同日再実行で暴増殖を防ぐ）
            # -------------------------------------------------
            run_detail = AutoTradeBacktestRunDetail.objects.filter(
                snapshot=snapshot,
                strategy=str(strategy),
                window_days=int(window),
                executed_at__date=target_date,
            ).order_by("-id").first()

            if (run_detail is None) or force:
                run_detail = AutoTradeBacktestRunDetail.objects.create(
                    user=user,
                    snapshot=snapshot,
                    strategy=str(strategy),
                    window_days=int(window),
                    start_date=target_date,
                    end_date=target_date,
                )

            # -------------------------------------------------
            # 既に Execution があるなら（force=False）再生成しない
            # -------------------------------------------------
            exists_exec = AutoTradeExecution.objects.filter(run_detail=run_detail).exists()
            if (not exists_exec) or force:
                # 念のため、同run_detailのExecutionが残ってたら掃除（force時）
                if force and exists_exec:
                    AutoTradeExecution.objects.filter(run_detail=run_detail).delete()

                for ticker in picks:
                    if strategy == "BREAKOUT":
                        run_breakout(
                            ticker=ticker,
                            window_days=int(window),
                            rr=rr_b,
                            snapshot=snapshot,
                            mode="BACKTEST",
                            run_meta=run_detail,
                            target_date=target_date,
                        )
                    else:
                        run_vwap(
                            ticker=ticker,
                            window_days=int(window),
                            rr=rr_v,
                            snapshot=snapshot,
                            mode="BACKTEST",
                            run_meta=run_detail,
                            target_date=target_date,
                        )

            # -------------------------------------------------
            # strategy × window の Execution を run_detail 基準で集計
            # -------------------------------------------------
            qs = AutoTradeExecution.objects.filter(run_detail=run_detail).order_by("exit_at")
            metrics = summarize_executions(qs=qs, base_equity=base_equity)
            metrics_by_window_strategy[int(window)][str(strategy)] = metrics

    # =====================================================
    # 戦略別ゲート（gate.pyはそのまま使う）
    # =====================================================
    metrics_vwap: Dict[int, Dict[str, Any]] = {}
    metrics_breakout: Dict[int, Dict[str, Any]] = {}

    for w in bt_windows:
        w = int(w)
        metrics_vwap[w] = (metrics_by_window_strategy.get(w) or {}).get("VWAP") or {}
        metrics_breakout[w] = (metrics_by_window_strategy.get(w) or {}).get("BREAKOUT") or {}

    gate_vwap = judge_multi_window(metrics_vwap)
    gate_breakout = judge_multi_window(metrics_breakout)

    merged = _merge_gate_results(
        gate_vwap=gate_vwap,
        gate_breakout=gate_breakout,
        rr_breakout=rr_b,
        rr_vwap=rr_v,
        windows=bt_windows,
    )

    # =====================================================
    # DailyState 更新（iPhone 1画面の意思決定をここで確定）
    # =====================================================
    final_level = str(merged.get("gate_level") or "STOP")
    active = list(merged.get("active_strategies") or [])
    disabled = list(merged.get("disabled_strategies") or [])

    # LIGHT時は VWAPのみを明示
    if final_level == "FULL":
        state.strategy = "MIXED"
    elif final_level == "LIGHT":
        state.strategy = "VWAP"
    else:
        state.strategy = ""

    state.gate_level = final_level
    state.gate_reason = "\n".join([str(x) for x in (merged.get("reasons") or []) if str(x).strip()])
    state.strategy_decided_at = timezone.now()
    state.strategy_decision = {
        "mode": final_level,
        "active": active,
        "disabled": disabled,
        "rr": {"BREAKOUT": rr_b, "VWAP": rr_v},
        "windows": list(bt_windows),
    }

    # 可視化用（Bで使う）
    state.backtest = {
        "meta": {
            "date": str(target_date),
            "base_equity_yen": int(base_equity),
            "rr_breakout": float(rr_b),
            "rr_vwap": float(rr_v),
        },
        "by_window": metrics_by_window_strategy,
        "gate": {
            "final": {"gate_level": final_level, "active": active, "disabled": disabled},
            "VWAP": gate_vwap,
            "BREAKOUT": gate_breakout,
        },
    }

    state.updated_at = timezone.now()
    state.save()

    return {
        "ok": True,
        "gate": {
            "final": final_level,
            "vwap": gate_vwap,
            "breakout": gate_breakout,
            "active": active,
            "disabled": disabled,
        },
        "metrics": metrics_by_window_strategy,
    }