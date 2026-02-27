"""
[FILE] autotrade/services/backtest/engine_breakout_detail.py
[PATH] <project_root>/autotrade/services/backtest/engine_breakout_detail.py

このファイルは何？
- 戦略①（レンジブレイク）の「詳細バックテスト」エンジンです。
- 1トレード=1件の辞書ログとして返します（DB保存は runner 側の役割）。

今回の変更（チューニング用）：
- 日足フィルタ（SMAトレンド）を追加しました。
  - breakout_daily_filter="OFF"（デフォルト）なら今まで通り。
  - breakout_daily_filter="SMA" で ON。
  - trendが上ならLONG優先、下ならSHORT優先にできます。
- これにより「同じスナップショットでも、相場環境で結果が変わる」ようになります。

初心者ポイント：
- ここは「ルール通りに売買を再現するだけ」。
- DB保存や判定（🟢🟡🔴）は別ファイルが担当します。
"""

from __future__ import annotations

from typing import Any, Dict, List

from django.conf import settings
from django.utils import timezone

from .data_fetcher import fetch_5m, fetch_daily


def _ensure_aware(dt):
    if dt is None:
        return None
    if timezone.is_aware(dt):
        return dt
    # dfのindexがnaiveな場合は「プロジェクトTZ」として扱う（JST想定）
    return timezone.make_aware(dt, timezone.get_current_timezone())


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def _decide_daily_trend_filter(
    *,
    snapshot_dict: Dict[str, Any],
    ticker: str,
    end_at,
) -> Dict[str, bool]:
    """
    日足トレンドでBREAKOUTの方向を制限する。
    返り値: {"allow_long": bool, "allow_short": bool, "mode": str}

    snapshot_dict のキー（今回追加）:
    - breakout_daily_filter: "OFF" | "SMA"
      - OFF: フィルタなし（従来通り）
      - SMA: SMAでトレンド判定して方向を絞る
    - breakout_daily_sma_days: int (default 20)
    - breakout_daily_bias: "BOTH" | "TREND_ONLY"
      - BOTH: トレンドでも逆方向も許す（弱め）
      - TREND_ONLY: トレンド方向だけ許す（強め）
    """
    mode = str(snapshot_dict.get("breakout_daily_filter") or "OFF").upper().strip()
    if mode not in ["OFF", "SMA"]:
        mode = "OFF"

    # デフォルトは従来互換（OFF）
    if mode == "OFF":
        return {"allow_long": True, "allow_short": True, "mode": "OFF"}

    sma_days = _safe_int(snapshot_dict.get("breakout_daily_sma_days"), 20)
    sma_days = max(5, min(sma_days, 60))

    bias = str(snapshot_dict.get("breakout_daily_bias") or "TREND_ONLY").upper().strip()
    if bias not in ["BOTH", "TREND_ONLY"]:
        bias = "TREND_ONLY"

    prefer_period = "180d" if sma_days <= 60 else "360d"
    dfd = fetch_daily(ticker, prefer_period=prefer_period)
    if dfd is None or getattr(dfd, "empty", True):
        # 日足が取れないなら安全に両方向OK
        return {"allow_long": True, "allow_short": True, "mode": "SMA_NO_DAILY"}

    # end_at までに絞る（5分足の終端に合わせる）
    try:
        if end_at is not None:
            dfd = dfd.loc[: end_at.date()].copy()
    except Exception:
        pass

    if len(dfd) < sma_days + 5:
        return {"allow_long": True, "allow_short": True, "mode": "SMA_TOO_SHORT"}

    close = dfd["close"].astype(float)
    sma = close.rolling(sma_days).mean()

    last_close = float(close.iloc[-1])
    last_sma = float(sma.iloc[-1]) if sma.iloc[-1] == sma.iloc[-1] else None  # NaN対策

    if last_sma is None or last_sma <= 0:
        return {"allow_long": True, "allow_short": True, "mode": "SMA_BAD"}

    # トレンド判定（超シンプル：終値がSMAより上/下）
    uptrend = last_close >= last_sma
    downtrend = last_close < last_sma

    if bias == "BOTH":
        # 弱め：トレンド方向を「優先」したいなら、ここは runner 側で重み付けに使うのが本筋。
        # ここでは安全に両方向OKのまま返す（将来拡張用）。
        return {"allow_long": True, "allow_short": True, "mode": "SMA_BOTH"}

    # 強め：トレンド方向だけ許可
    return {
        "allow_long": bool(uptrend),
        "allow_short": bool(downtrend),
        "mode": "SMA_TREND_ONLY_UP" if uptrend else "SMA_TREND_ONLY_DOWN",
    }


def run_breakout_detail(
    *,
    snapshot_dict: Dict[str, Any],
    ticker: str,
    window_days: int,
    rr: float,
    base_equity_yen: int = 1_000_000,
) -> Dict[str, Any]:
    """
    戻り値:
      {
        "trades": [ {execution_dict}, ... ],
        "pnls": [int, ...],
        "equity_curve": [float, ...],
        "start_at": datetime,
        "end_at": datetime,
      }

    execution_dict（runnerがそのままAutoTradeExecutionへ流し込める形）:
      {
        "ticker": "...",
        "side": "LONG" or "SHORT",
        "entry_at": datetime,
        "entry_price": float,
        "exit_at": datetime,
        "exit_price": float,
        "exit_reason": "TP/SL/TIME/EOD",
        "size": int,
        "pnl_yen": int,
        "rr": float,
        "holding_minutes": int,
      }
    """

    # v1は既存簡易版と同じ固定値（現実寄り）
    stop_pct = float(snapshot_dict.get("breakout_stop_pct") or 0.003)  # 0.3%
    slip = float(getattr(settings, "AUTOTRADE_SLIPPAGE_PCT", 0.0))
    risk_trade_pct = float(getattr(settings, "AUTOTRADE_RISK_TRADE_PCT", 0.0015))

    prefer_period = "60d" if window_days > 20 else "20d"
    df = fetch_5m(ticker, prefer_period=prefer_period)
    if df is None or getattr(df, "empty", True):
        return {"trades": [], "pnls": [], "equity_curve": [float(base_equity_yen)], "start_at": None, "end_at": None}

    bars_per_day = 70
    df = df.iloc[-window_days * bars_per_day :].copy()
    if len(df) < 200:
        return {"trades": [], "pnls": [], "equity_curve": [float(base_equity_yen)], "start_at": None, "end_at": None}

    # indexがdatetime前提
    idx = df.index
    start_at = _ensure_aware(idx[0].to_pydatetime() if hasattr(idx[0], "to_pydatetime") else idx[0])
    end_at = _ensure_aware(idx[-1].to_pydatetime() if hasattr(idx[-1], "to_pydatetime") else idx[-1])

    # ★ 追加：日足フィルタで方向制限
    filt = _decide_daily_trend_filter(snapshot_dict=snapshot_dict, ticker=ticker, end_at=end_at)
    allow_long = bool(filt.get("allow_long", True))
    allow_short = bool(filt.get("allow_short", True))

    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    equity = float(base_equity_yen)
    equity_curve = [equity]
    pnls: List[int] = []
    trades_out: List[Dict[str, Any]] = []

    # 最大保有（5分足20本=約100分）※既存簡易版踏襲
    max_hold_bars = int(snapshot_dict.get("max_hold_bars") or 20)

    # i: 現在バー、エントリーは次バーopen
    for i in range(10, len(df) - 2):
        # 直近30分（6本）の高値/安値
        hh = float(max(highs[i - 6 : i]))
        ll = float(min(lows[i - 6 : i]))

        price = float(closes[i])
        nxt_open = float(opens[i + 1])

        # ロット計算（100株単位）
        risk_yen = float(equity) * risk_trade_pct
        stop_yen_per_share = price * stop_pct
        shares = int((risk_yen / max(stop_yen_per_share, 1e-9)) // 100) * 100
        if shares < 100:
            continue

        # ---------- ロング（ブレイク上） ----------
        if price > hh:
            # ★ 追加：日足フィルタでロング禁止ならスキップ
            if not allow_long:
                continue

            entry = nxt_open * (1.0 + slip)
            stop = entry * (1.0 - stop_pct)
            take = entry * (1.0 + stop_pct * rr)
            entry_at = _ensure_aware(idx[i + 1].to_pydatetime() if hasattr(idx[i + 1], "to_pydatetime") else idx[i + 1])

            exited = False
            j_end = min(i + 1 + max_hold_bars, len(df) - 1)
            for j in range(i + 1, j_end + 1):
                h = float(highs[j])
                l = float(lows[j])

                # SL優先（事故防止）
                if l <= stop:
                    exitp = stop * (1.0 - slip)
                    exit_reason = "SL"
                    exited = True
                elif h >= take:
                    exitp = take * (1.0 - slip)
                    exit_reason = "TP"
                    exited = True
                else:
                    continue

                exit_at = _ensure_aware(idx[j].to_pydatetime() if hasattr(idx[j], "to_pydatetime") else idx[j])
                pnl = int(round((exitp - entry) * shares))
                equity += float(pnl)
                equity_curve.append(equity)
                pnls.append(pnl)

                hold_min = int(round((exit_at - entry_at).total_seconds() / 60.0))
                rr_real = (exitp - entry) / max(entry * stop_pct, 1e-9)

                trades_out.append({
                    "ticker": ticker,
                    "side": "LONG",
                    "entry_at": entry_at,
                    "entry_price": float(entry),
                    "exit_at": exit_at,
                    "exit_price": float(exitp),
                    "exit_reason": exit_reason,
                    "size": int(shares),
                    "pnl_yen": int(pnl),
                    "rr": float(rr_real),
                    "holding_minutes": int(hold_min),
                })
                break

            if not exited:
                # TIME（保有上限）でクローズ
                exit_at = _ensure_aware(idx[j_end].to_pydatetime() if hasattr(idx[j_end], "to_pydatetime") else idx[j_end])
                exitp = float(closes[j_end]) * (1.0 - slip)
                pnl = int(round((exitp - entry) * shares))
                equity += float(pnl)
                equity_curve.append(equity)
                pnls.append(pnl)

                hold_min = int(round((exit_at - entry_at).total_seconds() / 60.0))
                rr_real = (exitp - entry) / max(entry * stop_pct, 1e-9)

                trades_out.append({
                    "ticker": ticker,
                    "side": "LONG",
                    "entry_at": entry_at,
                    "entry_price": float(entry),
                    "exit_at": exit_at,
                    "exit_price": float(exitp),
                    "exit_reason": "TIME",
                    "size": int(shares),
                    "pnl_yen": int(pnl),
                    "rr": float(rr_real),
                    "holding_minutes": int(hold_min),
                })

        # ---------- ショート（ブレイク下） ----------
        elif price < ll:
            # ★ 追加：日足フィルタでショート禁止ならスキップ
            if not allow_short:
                continue

            entry = nxt_open * (1.0 - slip)
            stop = entry * (1.0 + stop_pct)
            take = entry * (1.0 - stop_pct * rr)
            entry_at = _ensure_aware(idx[i + 1].to_pydatetime() if hasattr(idx[i + 1], "to_pydatetime") else idx[i + 1])

            exited = False
            j_end = min(i + 1 + max_hold_bars, len(df) - 1)
            for j in range(i + 1, j_end + 1):
                h = float(highs[j])
                l = float(lows[j])

                if h >= stop:
                    exitp = stop * (1.0 + slip)
                    exit_reason = "SL"
                    exited = True
                elif l <= take:
                    exitp = take * (1.0 + slip)
                    exit_reason = "TP"
                    exited = True
                else:
                    continue

                exit_at = _ensure_aware(idx[j].to_pydatetime() if hasattr(idx[j], "to_pydatetime") else idx[j])
                pnl = int(round((entry - exitp) * shares))
                equity += float(pnl)
                equity_curve.append(equity)
                pnls.append(pnl)

                hold_min = int(round((exit_at - entry_at).total_seconds() / 60.0))
                rr_real = (entry - exitp) / max(entry * stop_pct, 1e-9)

                trades_out.append({
                    "ticker": ticker,
                    "side": "SHORT",
                    "entry_at": entry_at,
                    "entry_price": float(entry),
                    "exit_at": exit_at,
                    "exit_price": float(exitp),
                    "exit_reason": exit_reason,
                    "size": int(shares),
                    "pnl_yen": int(pnl),
                    "rr": float(rr_real),
                    "holding_minutes": int(hold_min),
                })
                break

            if not exited:
                exit_at = _ensure_aware(idx[j_end].to_pydatetime() if hasattr(idx[j_end], "to_pydatetime") else idx[j_end])
                exitp = float(closes[j_end]) * (1.0 + slip)
                pnl = int(round((entry - exitp) * shares))
                equity += float(pnl)
                equity_curve.append(equity)
                pnls.append(pnl)

                hold_min = int(round((exit_at - entry_at).total_seconds() / 60.0))
                rr_real = (entry - exitp) / max(entry * stop_pct, 1e-9)

                trades_out.append({
                    "ticker": ticker,
                    "side": "SHORT",
                    "entry_at": entry_at,
                    "entry_price": float(entry),
                    "exit_at": exit_at,
                    "exit_price": float(exitp),
                    "exit_reason": "TIME",
                    "size": int(shares),
                    "pnl_yen": int(pnl),
                    "rr": float(rr_real),
                    "holding_minutes": int(hold_min),
                })

    return {
        "trades": trades_out,
        "pnls": pnls,
        "equity_curve": equity_curve,
        "start_at": start_at,
        "end_at": end_at,
    }