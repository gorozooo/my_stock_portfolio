# =========================================================
# [FILE] autotrade/services/backtest/runner.py
# [PATH] <project_root>/autotrade/services/backtest/runner.py
#
# このファイルは何？
# - 詳細バックテストの司令塔（Execution基準）。
#
# 今回の変更（windows 20/40/60へ統一 + timeout安全装置）：
# - DEFAULT_BACKTEST_WINDOWS を (20,40,60) に変更
# - settings.AUTOTRADE_BT_WINDOWS があればそれを優先（既存ロジックは維持）
# - 1銘柄timeoutでも全体失敗にしない
# - 失敗銘柄を記録し、失敗が多すぎるwindowは比較対象から除外する
# =========================================================

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

# エンジン（Execution を吐く）
from autotrade.services.backtest.engine_breakout import run_breakout


# ★ windows を 20/40/60 に統一（settingsが無い場合の保険）
DEFAULT_BACKTEST_WINDOWS: Tuple[int, int, int] = (20, 40, 60)
STRATEGIES: Tuple[str, ...] = ("BREAKOUT",)


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


def _short_exc(e: Exception, max_len: int = 180) -> str:
    s = f"{e.__class__.__name__}: {e}"
    s = s.replace("\n", " ").strip()
    if len(s) > max_len:
        s = s[:max_len] + "…"
    return s


def _get_rr_breakout_from_snapshot(snapshot: AutoTradeSettingSnapshot) -> Optional[float]:
    """
    ★唯一の真実：snapshot内のRRを読む
    優先：
      1) snapshot.snapshot['lab']['BREAKOUT']['rr']   （UI由来・現状の実態に合わせる）
      2) snapshot.snapshot['tune']['rr_breakout']     （保険）
    """
    try:
        sdict = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}
    except Exception:
        sdict = {}

    lab = sdict.get("lab") if isinstance(sdict.get("lab"), dict) else {}
    bo = lab.get("BREAKOUT") if isinstance(lab.get("BREAKOUT"), dict) else {}
    rr1 = bo.get("rr", None)
    if rr1 is not None:
        return _safe_float(rr1, None)

    tune = sdict.get("tune") if isinstance(sdict.get("tune"), dict) else {}
    rr2 = tune.get("rr_breakout", None)
    if rr2 is not None:
        return _safe_float(rr2, None)

    return None


def _build_final_gate(
    *,
    gate_breakout: Dict[str, Any],
    rr_breakout: float,
    windows: Tuple[int, ...],
) -> Dict[str, Any]:
    """
    BREAKOUT一本運用の最終ゲートを組み立てる。
    - gate.py の reasons（日本語＋数値＋円）を尊重し、そのまま返す。
    """
    lv = str((gate_breakout or {}).get("gate_level") or "STOP")

    if lv == "FULL":
        final = "FULL"
        active = ["BREAKOUT"]
        disabled: List[str] = []
        head = "【最終判定】FULL（BREAKOUT 稼働）"
    elif lv == "LIGHT":
        final = "LIGHT"
        active = ["BREAKOUT"]
        disabled = []
        head = "【最終判定】LIGHT（BREAKOUT 軽稼働）"
    else:
        final = "STOP"
        active = []
        disabled = ["BREAKOUT"]
        head = "【最終判定】STOP（安全のため稼働しない）"

    reasons: List[str] = [head]
    reasons.append(f"【設定】windows={list(windows)} / RR(BREAKOUT)={rr_breakout:.2f}")

    rs = (gate_breakout or {}).get("reasons") or []
    if rs:
        reasons.append("【BREAKOUT判定】")
        reasons.extend([str(x) for x in rs if str(x).strip()])

    return {
        "gate_level": final,
        "reasons": reasons,
        "active_strategies": active,
        "disabled_strategies": disabled,
        "gate_breakout": gate_breakout,
    }


def _build_window_fetch_quality(
    *,
    total_picks: int,
    success_tickers: List[str],
    failed_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    取得品質を評価する。
    - 1銘柄落ちても即NGにはしない
    - 失敗比率が高すぎるwindowだけ比較対象から外す
    """
    success_unique = list(dict.fromkeys([str(x) for x in (success_tickers or []) if str(x).strip()]))
    failed_rows = [x for x in (failed_rows or []) if isinstance(x, dict)]

    success_count = len(success_unique)
    failed_count = len(failed_rows)

    total = int(total_picks or 0)
    success_ratio = (success_count / total) if total > 0 else 0.0
    failed_ratio = (failed_count / total) if total > 0 else 0.0

    min_success_ratio = float(getattr(settings, "AUTOTRADE_FETCH_MIN_SUCCESS_RATIO", 0.60))
    max_failed_ratio = float(getattr(settings, "AUTOTRADE_FETCH_MAX_FAILED_RATIO", 0.40))

    invalid = False
    reason = "ok"

    if total <= 0:
        invalid = True
        reason = "no_picks"
    elif success_count <= 0:
        invalid = True
        reason = "all_failed"
    elif success_ratio < min_success_ratio:
        invalid = True
        reason = f"success_ratio_too_low({success_ratio:.2f}<{min_success_ratio:.2f})"
    elif failed_ratio > max_failed_ratio:
        invalid = True
        reason = f"failed_ratio_too_high({failed_ratio:.2f}>{max_failed_ratio:.2f})"

    return {
        "total_picks": total,
        "success_count": int(success_count),
        "failed_count": int(failed_count),
        "success_ratio": float(round(success_ratio, 4)),
        "failed_ratio": float(round(failed_ratio, 4)),
        "success_tickers": success_unique[:50],
        "failed_rows": failed_rows[:50],
        "invalid_for_comparison": bool(invalid),
        "reason": str(reason),
    }


def _build_invalid_metrics_for_fetch(
    *,
    base_equity: int,
    quality: Dict[str, Any],
) -> Dict[str, Any]:
    """
    取得失敗が多すぎる window は、昇格比較や gate 判定で不利になるよう
    明示的に STOP 相当のメトリクスに落とす。
    """
    return {
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "sum_win_yen": 0,
        "sum_loss_yen": 0,
        "profit_factor": 0.0,
        "max_drawdown_yen": -int(base_equity),
        "max_drawdown_pct": 1.0,
        "total_pnl": 0,
        "data_fetch_invalid": True,
        "data_fetch_reason": str(quality.get("reason") or ""),
        "data_fetch": quality,
    }


def _build_fetch_warning_lines(fetch_meta_by_window: Dict[str, Dict[str, Any]]) -> List[str]:
    lines: List[str] = []

    for w_key in sorted(fetch_meta_by_window.keys(), key=lambda x: int(x)):
        q = fetch_meta_by_window.get(w_key) or {}
        failed_rows = q.get("failed_rows") or []
        if not failed_rows:
            continue

        head = (
            f"【取得警告 {w_key}日】"
            f"成功 {q.get('success_count', 0)}/{q.get('total_picks', 0)} "
            f"/ 失敗 {q.get('failed_count', 0)}"
        )
        lines.append(head)

        for row in failed_rows[:5]:
            ticker = str(row.get("ticker") or "-")
            err = str(row.get("error") or "-")
            lines.append(f" - {ticker}: {err}")

        if len(failed_rows) > 5:
            lines.append(f" - ... 他 {len(failed_rows) - 5} 件")

    return lines


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
    base_equity_yen: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    詳細バックテストを実行し、DailyState を更新する（BREAKOUTのみ）。

    - snapshot: ACTIVE Snapshot（必須）
    - picks: 今日の銘柄リスト
    - windows: 実行期間（例: (20,40,60)）
    - rr_breakout: 互換用（原則使わない。snapshot内が唯一の真実）
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
    bt_windows = tuple(
        int(x) for x in (
            windows or tuple(getattr(settings, "AUTOTRADE_BT_WINDOWS", DEFAULT_BACKTEST_WINDOWS))
        )
    )

    # ★ RR(BREAKOUT) は snapshot 内を最優先（唯一の真実）
    rr_from_snap = _get_rr_breakout_from_snapshot(snapshot)
    if rr_from_snap is not None:
        rr_b = float(rr_from_snap)
    elif rr_breakout is not None:
        # 互換用の保険
        rr_b = float(rr_breakout)
    else:
        rr_b = float(getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0))

    snap_dict = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}
    if base_equity_yen is not None:
        base_equity = int(base_equity_yen)
    else:
        base_equity = int(
            snap_dict.get("base_equity_yen", getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000))
        )

    user = snapshot.user

    # -----------------------------------------------------
    # 既存データ削除（force）
    #   - trade_date基準で掃除（UTC/JST混線を根絶）
    # -----------------------------------------------------
    if force:
        details = AutoTradeBacktestRunDetail.objects.filter(
            snapshot=snapshot,
            trade_date=target_date,
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
            "disabled": ["BREAKOUT"],
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

    metrics_by_window_strategy: Dict[int, Dict[str, Dict[str, Any]]] = {}
    fetch_meta_by_window: Dict[str, Dict[str, Any]] = {}
    all_fetch_errors: List[Dict[str, Any]] = []

    for window in bt_windows:
        metrics_by_window_strategy[int(window)] = {}

        window_failed_rows: List[Dict[str, Any]] = []
        window_success_tickers: List[str] = []

        # -------------------------------------------------
        # 既存 run_detail を再利用（同日再実行で暴増殖を防ぐ）
        # -------------------------------------------------
        run_detail = AutoTradeBacktestRunDetail.objects.filter(
            snapshot=snapshot,
            strategy="BREAKOUT",
            window_days=int(window),
            trade_date=target_date,
        ).order_by("-id").first()

        if (run_detail is None) or force:
            run_detail = AutoTradeBacktestRunDetail.objects.create(
                user=user,
                snapshot=snapshot,
                strategy="BREAKOUT",
                window_days=int(window),
                trade_date=target_date,
                start_date=target_date,
                end_date=target_date,
            )

        # -------------------------------------------------
        # 既に Execution があるなら（force=False）再生成しない
        # -------------------------------------------------
        exists_exec = AutoTradeExecution.objects.filter(run_detail=run_detail).exists()
        if (not exists_exec) or force:
            if force and exists_exec:
                AutoTradeExecution.objects.filter(run_detail=run_detail).delete()

            for ticker in picks:
                try:
                    # 銘柄ごとにsavepointを切る
                    with transaction.atomic():
                        run_breakout(
                            ticker=ticker,
                            window_days=int(window),
                            rr=rr_b,
                            snapshot=snapshot,
                            mode="BACKTEST",
                            run_meta=run_detail,
                            target_date=target_date,
                        )
                    window_success_tickers.append(str(ticker))
                except Exception as e:
                    row = {
                        "ticker": str(ticker),
                        "window": int(window),
                        "error": _short_exc(e),
                    }
                    window_failed_rows.append(row)
                    all_fetch_errors.append(row)
        else:
            # 再利用時は「既存結果を使った」扱い
            window_success_tickers = list(picks)

        quality = _build_window_fetch_quality(
            total_picks=len(picks),
            success_tickers=window_success_tickers,
            failed_rows=window_failed_rows,
        )
        fetch_meta_by_window[str(int(window))] = quality

        qs = AutoTradeExecution.objects.filter(run_detail=run_detail).order_by("exit_at")
        metrics = summarize_executions(qs=qs, base_equity=base_equity)

        if quality.get("invalid_for_comparison"):
            metrics = _build_invalid_metrics_for_fetch(
                base_equity=base_equity,
                quality=quality,
            )
        else:
            metrics["data_fetch"] = quality

        metrics_by_window_strategy[int(window)]["BREAKOUT"] = metrics

    metrics_breakout: Dict[int, Dict[str, Any]] = {
        int(w): (metrics_by_window_strategy.get(int(w)) or {}).get("BREAKOUT") or {}
        for w in bt_windows
    }
    gate_breakout = judge_multi_window(metrics_breakout)

    merged = _build_final_gate(
        gate_breakout=gate_breakout,
        rr_breakout=rr_b,
        windows=bt_windows,
    )

    fetch_warning_lines = _build_fetch_warning_lines(fetch_meta_by_window)
    if fetch_warning_lines:
        reasons = list(merged.get("reasons") or [])
        reasons.append("【データ取得メモ】")
        reasons.extend(fetch_warning_lines)
        merged["reasons"] = reasons

    final_level = str(merged.get("gate_level") or "STOP")
    active = list(merged.get("active_strategies") or [])
    disabled = list(merged.get("disabled_strategies") or [])

    # strategy表示はBREAKOUT固定（停止時は空でOK）
    if final_level in ["FULL", "LIGHT"]:
        state.strategy = "BREAKOUT"
    else:
        state.strategy = ""

    state.gate_level = final_level
    state.gate_reason = "\n".join([str(x) for x in (merged.get("reasons") or []) if str(x).strip()])
    state.strategy_decided_at = timezone.now()
    state.strategy_decision = {
        "mode": final_level,
        "active": active,
        "disabled": disabled,
        "rr": {"BREAKOUT": rr_b},
        "windows": list(bt_windows),
        "data_fetch_invalid_windows": [
            int(w) for w, q in fetch_meta_by_window.items()
            if bool((q or {}).get("invalid_for_comparison"))
        ],
    }

    state.backtest = {
        "meta": {
            "date": str(target_date),
            "base_equity_yen": int(base_equity),
            "rr_breakout": float(rr_b),
            "windows": list(bt_windows),
            "data_fetch": {
                "windows": fetch_meta_by_window,
                "error_count": len(all_fetch_errors),
                "errors": all_fetch_errors[:100],
            },
        },
        "by_window": metrics_by_window_strategy,
        "gate": {
            "final": {"gate_level": final_level, "active": active, "disabled": disabled},
            "BREAKOUT": gate_breakout,
        },
    }

    state.updated_at = timezone.now()
    state.save()

    return {
        "ok": True,
        "gate": {
            "final": final_level,
            "breakout": gate_breakout,
            "active": active,
            "disabled": disabled,
        },
        "metrics": metrics_by_window_strategy,
        "data_fetch": {
            "windows": fetch_meta_by_window,
            "error_count": len(all_fetch_errors),
            "errors": all_fetch_errors[:100],
        },
    }