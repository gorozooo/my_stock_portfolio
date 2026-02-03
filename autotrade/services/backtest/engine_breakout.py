"""
[FILE] autotrade/services/backtest/engine_breakout.py
[PATH] <project_root>/autotrade/services/backtest/engine_breakout.py

このファイルは何？
- 戦略①（レンジブレイク）のバックテストを回すエンジンです。

初心者ポイント：
- “ブレイクしたら入る→損切りor利確” を機械的に再現します。
"""

from __future__ import annotations

from typing import Optional
from django.conf import settings
from django.utils import timezone

from .data_fetcher import fetch_5m
from .metrics import max_drawdown, profit_factor

from autotrade.models import AutoTradeSettingSnapshot
from autotrade.models_backtest import AutoTradeExecution


def run_breakout(
    ticker: str,
    window_days: int,
    rr: float,
    *,
    snapshot: Optional[AutoTradeSettingSnapshot] = None,
    mode: Optional[str] = None,
    run_meta=None,
    target_date=None,
):
    """
    互換性のために2モードを持つ：

    1) 旧：簡易集計モード（aggregate用）
       run_breakout(ticker, window_days, rr) -> {"trades","pf","max_dd","pnl"}

    2) 新：詳細バックテストモード（Execution生成）
       run_breakout(..., snapshot=..., mode="BACKTEST", target_date=...) -> DBに AutoTradeExecution を作る
       ※戻り値は必須ではないが、簡単な件数だけ返す
    """
    stop_pct = 0.003  # 0.3%（初期値：現実寄り）
    slip = float(getattr(settings, "AUTOTRADE_SLIPPAGE_PCT", 0.0))

    df = fetch_5m(ticker, prefer_period="60d" if window_days > 20 else "20d")
    if df is None or getattr(df, "empty", True):
        return None

    # 5分足をざっくり日数分に切る（9:00〜14:30想定で 1日70本程度）
    bars_per_day = 70
    df = df.iloc[-window_days * bars_per_day :].copy()
    if len(df) < 200:
        return None

    # =========================
    # 詳細バックテストモード？
    # =========================
    detailed = (snapshot is not None) and (mode is not None)

    # 旧モード用の集計器
    equity = 1_000_000.0
    equity_curve = [equity]
    pnls = []
    trades = 0

    # 詳細モード用の準備
    if detailed:
        user = snapshot.user
        # base_equity は「固定値 or snapshot内の値」を優先
        base_equity = float(
            (snapshot.snapshot or {}).get(
                "base_equity_yen",
                getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000),
            )
        )
        equity = base_equity
        equity_curve = [equity]

        if target_date is None:
            target_date = timezone.localdate()

    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    idx = df.index

    i = 10
    while i < len(df) - 2:
        hh = max(highs[i - 6 : i])  # 直近30分の高値
        ll = min(lows[i - 6 : i])   # 直近30分の安値

        price = float(closes[i])
        nxt_open = float(opens[i + 1])

        # ロット計算（100株単位）
        risk_yen = float(equity) * float(getattr(settings, "AUTOTRADE_RISK_TRADE_PCT", 0.0015))
        stop_yen_per_share = price * stop_pct
        shares = int((risk_yen / max(stop_yen_per_share, 1e-9)) // 100) * 100
        if shares < 100:
            i += 1
            continue

        # ロング
        if price > hh:
            entry = nxt_open * (1 + slip)
            stop = entry * (1 - stop_pct)
            take = entry * (1 + stop_pct * rr)

            entry_at = idx[i + 1]
            exited = False

            for j in range(i + 1, min(i + 1 + 20, len(df))):
                h = float(highs[j])
                l = float(lows[j])

                # SL
                if l <= stop:
                    exitp = stop * (1 - slip)
                    pnl = (exitp - entry) * shares
                    equity += pnl
                    pnls.append(pnl)
                    trades += 1
                    equity_curve.append(equity)

                    if detailed:
                        exit_at = idx[j]
                        holding_minutes = int((exit_at - entry_at).total_seconds() // 60)
                        risk_per_share = entry * stop_pct
                        rr_real = (exitp - entry) / max(risk_per_share, 1e-9)

                        AutoTradeExecution.objects.create(
                            user=user,
                            snapshot=snapshot,
                            run_detail=run_meta,  # ★ 追加：run_metaに紐づける
                            mode=str(mode),
                            strategy="BREAKOUT",
                            ticker=ticker,
                            side="LONG",
                            entry_at=entry_at,
                            entry_price=float(entry),
                            size=int(shares),
                            exit_at=exit_at,
                            exit_price=float(exitp),
                            exit_reason="SL",
                            pnl_yen=int(round(pnl)),
                            rr=float(rr_real),
                            holding_minutes=int(max(0, holding_minutes)),
                        )

                    i = j + 1
                    exited = True
                    break

                # TP
                if h >= take:
                    exitp = take * (1 - slip)
                    pnl = (exitp - entry) * shares
                    equity += pnl
                    pnls.append(pnl)
                    trades += 1
                    equity_curve.append(equity)

                    if detailed:
                        exit_at = idx[j]
                        holding_minutes = int((exit_at - entry_at).total_seconds() // 60)
                        risk_per_share = entry * stop_pct
                        rr_real = (exitp - entry) / max(risk_per_share, 1e-9)

                        AutoTradeExecution.objects.create(
                            user=user,
                            snapshot=snapshot,
                            run_detail=run_meta,  # ★ 追加
                            mode=str(mode),
                            strategy="BREAKOUT",
                            ticker=ticker,
                            side="LONG",
                            entry_at=entry_at,
                            entry_price=float(entry),
                            size=int(shares),
                            exit_at=exit_at,
                            exit_price=float(exitp),
                            exit_reason="TP",
                            pnl_yen=int(round(pnl)),
                            rr=float(rr_real),
                            holding_minutes=int(max(0, holding_minutes)),
                        )

                    i = j + 1
                    exited = True
                    break

            if exited:
                continue

        # ショート
        elif price < ll:
            entry = nxt_open * (1 - slip)
            stop = entry * (1 + stop_pct)
            take = entry * (1 - stop_pct * rr)

            entry_at = idx[i + 1]
            exited = False

            for j in range(i + 1, min(i + 1 + 20, len(df))):
                h = float(highs[j])
                l = float(lows[j])

                # SL
                if h >= stop:
                    exitp = stop * (1 + slip)
                    pnl = (entry - exitp) * shares
                    equity += pnl
                    pnls.append(pnl)
                    trades += 1
                    equity_curve.append(equity)

                    if detailed:
                        exit_at = idx[j]
                        holding_minutes = int((exit_at - entry_at).total_seconds() // 60)
                        risk_per_share = entry * stop_pct
                        rr_real = (entry - exitp) / max(risk_per_share, 1e-9)

                        AutoTradeExecution.objects.create(
                            user=user,
                            snapshot=snapshot,
                            run_detail=run_meta,  # ★ 追加
                            mode=str(mode),
                            strategy="BREAKOUT",
                            ticker=ticker,
                            side="SHORT",
                            entry_at=entry_at,
                            entry_price=float(entry),
                            size=int(shares),
                            exit_at=exit_at,
                            exit_price=float(exitp),
                            exit_reason="SL",
                            pnl_yen=int(round(pnl)),
                            rr=float(rr_real),
                            holding_minutes=int(max(0, holding_minutes)),
                        )

                    i = j + 1
                    exited = True
                    break

                # TP
                if l <= take:
                    exitp = take * (1 + slip)
                    pnl = (entry - exitp) * shares
                    equity += pnl
                    pnls.append(pnl)
                    trades += 1
                    equity_curve.append(equity)

                    if detailed:
                        exit_at = idx[j]
                        holding_minutes = int((exit_at - entry_at).total_seconds() // 60)
                        risk_per_share = entry * stop_pct
                        rr_real = (entry - exitp) / max(risk_per_share, 1e-9)

                        AutoTradeExecution.objects.create(
                            user=user,
                            snapshot=snapshot,
                            run_detail=run_meta,  # ★ 追加
                            mode=str(mode),
                            strategy="BREAKOUT",
                            ticker=ticker,
                            side="SHORT",
                            entry_at=entry_at,
                            entry_price=float(entry),
                            size=int(shares),
                            exit_at=exit_at,
                            exit_price=float(exitp),
                            exit_reason="TP",
                            pnl_yen=int(round(pnl)),
                            rr=float(rr_real),
                            holding_minutes=int(max(0, holding_minutes)),
                        )

                    i = j + 1
                    exited = True
                    break

            if exited:
                continue

        i += 1

    # 詳細モード：Executionを作ったので件数だけ返す
    if detailed:
        return {"executions": int(trades)}

    # 旧モード：集計辞書を返す
    if trades == 0:
        return {"trades": 0, "pf": 0.0, "max_dd": 1.0, "pnl": 0.0}

    return {
        "trades": int(trades),
        "pf": float(profit_factor(pnls)),
        "max_dd": float(max_drawdown(equity_curve)),
        "pnl": float(sum(pnls)),
    }