"""
[FILE] autotrade/services/tuning/manual_backtest.py
[PATH] <project_root>/autotrade/services/tuning/manual_backtest.py

このファイルは何？
- 「実験室（TuningProfile）」の検証（BACKTEST）を実行するサービスです。
- ★ BREAKOUT一本運用に固定（VWAP関連を完全撤去）
- ACTIVEは触らず、DRAFT Snapshot を作って、BREAKOUT の詳細バックテストを回します。
- detailエンジン（engine_*_detail.py）は「DB保存しない」設計なので、
  返ってくる trades を集計して metrics を作り、gate判定・UI表示用の evidence を snapshot に焼き付けます。

重要：
- UIは自由操作。安全は「ACTIVE昇格しない」「ロールバックで戻せる」で担保。
- この段階（UI優先）では Execution/RunDetail のDB保存は必須ではない扱い。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import date as dt_date

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from autotrade.models import AutoTradeTuningProfile, AutoTradeSettingSnapshot
from autotrade.models_backtest import AutoTradeBacktestRunDetail

from autotrade.services.backtest.gate import judge_multi_window

# ★ “詳細”エンジン：snapshot_dict を受け取り、trades を返す（DB保存しない）
from autotrade.services.backtest.engine_breakout_detail import run_breakout_detail


def _pct_to_ratio(pct: float) -> float:
    """
    UIは「0.30 (=0.30%)」で入力する設計なので、エンジン用に 0.0030 に変換する。
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

    ★ BREAKOUTのみ
    """
    p = profile.params if isinstance(profile.params, dict) else {}
    b = p.get("BREAKOUT") if isinstance(p.get("BREAKOUT"), dict) else {}

    # UIは%表記（0.30）→ ratio（0.0030）
    b_stop = _pct_to_ratio(_safe_float(b.get("stop_pct"), 0.30))

    # RRはそのまま
    rr_b = _safe_float(b.get("rr"), 2.0)

    lookback_bars = _safe_int(b.get("lookback_bars"), 6)
    max_hold_b = _safe_int(b.get("max_hold_min"), 30)

    snap = {
        "base_equity_yen": int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)),

        # detailエンジンが読むキー
        "breakout_stop_pct": float(b_stop),

        # detailエンジン側の max_hold_bars（5分足換算）
        # 30分なら 5分足×6本。少し余裕で +1。
        "max_hold_bars": int(max_hold_b // 5 + 1),

        # 実験室の“人間向け”原本（UI表示用）
        "lab": {
            "BREAKOUT": {
                "stop_pct_ui": float(_safe_float(b.get("stop_pct"), 0.30)),
                "rr": float(rr_b),
                "lookback_bars": int(lookback_bars),
                "max_hold_min": int(max_hold_b),
            },
        },
    }
    return snap


def _sort_key_exit_at(t: Dict[str, Any]):
    dt = t.get("exit_at")
    return dt or timezone.now()


def _summarize_trades(trades: List[Dict[str, Any]], base_equity_yen: int) -> Dict[str, Any]:
    """
    detailエンジンが返す trades（辞書配列）から、UI＆gate用のmetricsを作る。
    """
    trades = [t for t in (trades or []) if isinstance(t, dict)]
    trades_sorted = sorted(trades, key=_sort_key_exit_at)

    wins = 0
    losses = 0
    sum_win = 0
    sum_loss = 0
    total_pnl = 0

    equity = float(base_equity_yen)
    peak = float(base_equity_yen)
    max_dd_yen = 0.0  # マイナス方向の最大（負値）

    for t in trades_sorted:
        pnl = t.get("pnl_yen")
        try:
            pnl_i = int(pnl)
        except Exception:
            pnl_i = 0

        total_pnl += pnl_i
        equity += float(pnl_i)

        if pnl_i >= 0:
            wins += 1
            sum_win += pnl_i
        else:
            losses += 1
            sum_loss += pnl_i  # 負値のまま

        if equity > peak:
            peak = equity
        dd = equity - peak
        if dd < max_dd_yen:
            max_dd_yen = dd

    trades_n = len(trades_sorted)
    win_rate = None
    if trades_n > 0:
        win_rate = round((wins / trades_n) * 100.0, 1)

    pf = None
    if sum_loss != 0:
        pf = round(float(sum_win) / max(abs(float(sum_loss)), 1e-9), 3)

    max_dd_pct = None
    if base_equity_yen > 0:
        max_dd_pct = round(abs(float(max_dd_yen)) / float(base_equity_yen), 4)

    return {
        "trades": trades_n,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "sum_win_yen": int(sum_win),
        "sum_loss_yen": int(sum_loss),  # 負値
        "profit_factor": pf,
        "max_drawdown_yen": int(round(max_dd_yen)),  # 負値
        "max_drawdown_pct": max_dd_pct,
        "total_pnl": int(total_pnl),
    }


def _collect_trades_for_breakout(
    *,
    snap_dict: Dict[str, Any],
    ticker: str,
    window_days: int,
    rr: float,
    base_equity_yen: int,
) -> List[Dict[str, Any]]:
    try:
        out = run_breakout_detail(
            snapshot_dict=snap_dict,
            ticker=ticker,
            window_days=int(window_days),
            rr=float(rr),
            base_equity_yen=int(base_equity_yen),
        )
    except Exception:
        return []

    if not isinstance(out, dict):
        return []
    trades = out.get("trades")
    if not isinstance(trades, list):
        return []
    return [t for t in trades if isinstance(t, dict)]


@transaction.atomic
def run_backtest_for_tuning_profile(
    *,
    profile: AutoTradeTuningProfile,
    target_date: dt_date,
    picks: List[str],
    windows: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    実験室の検証を1回実行する（BREAKOUTのみ）。
    - DRAFT Snapshot を作る（固定）
    - window×BREAKOUT の detailバックテストを回し、trades を集計して metrics を作る
    - gate 判定（judge_multi_window）を通す
    - evidence を snapshot に焼き付ける
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
    rr_breakout = float((snap_dict.get("lab") or {}).get("BREAKOUT", {}).get("rr", 2.0))

    # 2) 実行：window×BREAKOUT
    metrics_by_window: Dict[int, Dict[str, Dict[str, Any]]] = {}

    for w in windows:
        metrics_by_window[int(w)] = {}

        rd = AutoTradeBacktestRunDetail.objects.create(
            user=profile.user,
            snapshot=snap,
            strategy="BREAKOUT",
            window_days=int(w),
            start_date=target_date,
            end_date=target_date,
            trade_date=target_date,
        )

        all_trades: List[Dict[str, Any]] = []
        for ticker in picks:
            ts = _collect_trades_for_breakout(
                snap_dict=snap_dict,
                ticker=ticker,
                window_days=int(w),
                rr=rr_breakout,
                base_equity_yen=base_equity,
            )
            all_trades.extend(ts)

        m = _summarize_trades(all_trades, base_equity_yen=base_equity)

        try:
            rd.note = f"manual_backtest breakout trades={m.get('trades')}"
            rd.save(update_fields=["note"])
        except Exception:
            pass

        metrics_by_window[int(w)]["BREAKOUT"] = m

    # 3) gate 判定（BREAKOUTのみ）
    metrics_breakout = {int(w): (metrics_by_window[int(w)].get("BREAKOUT") or {}) for w in windows}
    gate_breakout = judge_multi_window(metrics_breakout)

    final_level = str((gate_breakout or {}).get("gate_level") or "STOP")

    merged = {
        "gate_level": final_level,
        "active": (["BREAKOUT"] if final_level in ["FULL", "LIGHT"] else []),
        "disabled": ([] if final_level in ["FULL", "LIGHT"] else ["BREAKOUT"]),
        "gate_breakout": gate_breakout,
    }

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