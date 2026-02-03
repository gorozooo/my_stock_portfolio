"""
[FILE] autotrade/services/backtest/runner.py
[PATH] <project_root>/autotrade/services/backtest/runner.py

このファイルは何？
- 詳細バックテストの「司令塔」です。
- Snapshot（固定された設定）と Universe（対象銘柄）を入力として、
  1) 5分足でシミュレーション（戦略別）
  2) 1トレード = 1レコードで AutoTradeExecution に保存
  3) 期間別のメタ（実行情報）を AutoTradeBacktestRunDetail に保存
  4) 指標（PF/DD/回数）を算出して gate 判定に使える形で返す
  を一気にやります。

設計原則（あなたの確定方針に合わせる）：
- 1トレード = 1レコード（AutoTradeExecution）
- 集計値（PF/DD等）は “保存しない” のが本筋
  - ただし gate 判定のために「計算して返す」のはOK（保存はしない）
- 再実行でログが増殖しないように、同条件の RunDetail がある場合はスキップ（force=False）

初心者ポイント：
- “詳細バックテストを回す” = このファイルの関数を呼ぶだけ
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from autotrade.models import AutoTradeSettingSnapshot
from autotrade.models_backtest import AutoTradeBacktestRunDetail, AutoTradeExecution
from autotrade.services.backtest.data_fetcher import fetch_5m


# =========================================================
# 内部ユーティリティ（メトリクス計算：保存しない）
# =========================================================
def _profit_factor(pnls: List[float]) -> float:
    gains = sum(x for x in pnls if x > 0)
    losses = -sum(x for x in pnls if x < 0)
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def _max_drawdown_pct(equity_curve: List[float]) -> float:
    if not equity_curve:
        return 1.0
    peak = equity_curve[0]
    max_dd = 0.0
    for x in equity_curve:
        if x > peak:
            peak = x
        dd = (peak - x) / max(peak, 1e-9)
        if dd > max_dd:
            max_dd = dd
    return float(max_dd)


def _summarize_metrics(pnls: List[float], equity_curve: List[float]) -> Dict[str, Any]:
    trades = int(len(pnls))
    pf = float(_profit_factor(pnls))
    dd = float(_max_drawdown_pct(equity_curve))
    total = float(sum(pnls)) if pnls else 0.0
    return {
        "trades": trades,
        "profit_factor": pf,
        "max_drawdown_pct": dd,
        "total_pnl_yen": int(round(total)),
    }


def _safe_dt(dt) -> timezone.datetime:
    """
    fetch_5m が返す index が naive の場合に備えて、timezone-aware に揃える。
    """
    if dt is None:
        return timezone.now()
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


# =========================================================
# バックテスト（詳細）用の単発トレード表現
# =========================================================
@dataclass
class _Trade:
    ticker: str
    strategy: str
    side: str  # "LONG" / "SHORT"
    entry_at: timezone.datetime
    entry_price: float
    exit_at: timezone.datetime
    exit_price: float
    exit_reason: str  # "TP"/"SL"/"TIME"/"EOD"/"FORCE"
    size: int
    pnl_yen: int
    rr: float
    holding_minutes: int


# =========================================================
# シミュレーション（戦略別）
# - ここで「1トレード=1レコード」を作れる形で trades を返す
# =========================================================
def _simulate_breakout(
    *,
    ticker: str,
    window_days: int,
    rr: float,
    slip_pct: float,
    stop_pct: float,
    risk_trade_pct: float,
    base_equity_yen: int,
    hold_bars: int = 20,
    bars_per_day: int = 70,
) -> Tuple[List[_Trade], List[float], List[float]]:
    df = fetch_5m(ticker, prefer_period="60d" if window_days > 20 else "20d")
    if df.empty:
        return [], [], []

    df = df.iloc[-window_days * bars_per_day :].copy()
    if len(df) < 200:
        return [], [], []

    equity = float(base_equity_yen)
    equity_curve: List[float] = [equity]
    pnls: List[float] = []
    trades: List[_Trade] = []

    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    idx = df.index

    for i in range(10, len(df) - 1):
        hh = max(highs[i - 6 : i])  # 直近30分高値
        ll = min(lows[i - 6 : i])   # 直近30分安値

        price = float(closes[i])
        nxt_open = float(opens[i + 1])

        # ロット計算（100株単位）
        risk_yen = equity * float(risk_trade_pct)
        stop_yen_per_share = price * float(stop_pct)
        shares = int((risk_yen / max(stop_yen_per_share, 1e-9)) // 100) * 100
        if shares < 100:
            continue

        # ロング（ブレイク）
        if price > float(hh):
            entry_at = _safe_dt(idx[i + 1])
            entry = nxt_open * (1.0 + float(slip_pct))
            stop = entry * (1.0 - float(stop_pct))
            take = entry * (1.0 + float(stop_pct) * float(rr))

            exit_reason = "TIME"
            exit_at = _safe_dt(idx[min(i + hold_bars, len(df) - 1)])
            exit_price = float(opens[min(i + hold_bars, len(df) - 1)])

            for j in range(i + 1, min(i + 1 + hold_bars, len(df))):
                h = float(highs[j])
                l = float(lows[j])

                if l <= stop:
                    exit_reason = "SL"
                    exit_at = _safe_dt(idx[j])
                    exit_price = stop * (1.0 - float(slip_pct))
                    break
                if h >= take:
                    exit_reason = "TP"
                    exit_at = _safe_dt(idx[j])
                    exit_price = take * (1.0 - float(slip_pct))
                    break

            pnl = (exit_price - entry) * shares
            equity += pnl
            equity_curve.append(equity)
            pnls.append(pnl)

            holding_minutes = int(round((exit_at - entry_at).total_seconds() / 60.0))
            trades.append(
                _Trade(
                    ticker=ticker,
                    strategy="BREAKOUT",
                    side="LONG",
                    entry_at=entry_at,
                    entry_price=float(entry),
                    exit_at=exit_at,
                    exit_price=float(exit_price),
                    exit_reason=exit_reason,
                    size=int(shares),
                    pnl_yen=int(round(pnl)),
                    rr=float(rr),
                    holding_minutes=max(0, holding_minutes),
                )
            )
            continue

        # ショート（ブレイク）
        if price < float(ll):
            entry_at = _safe_dt(idx[i + 1])
            entry = nxt_open * (1.0 - float(slip_pct))
            stop = entry * (1.0 + float(stop_pct))
            take = entry * (1.0 - float(stop_pct) * float(rr))

            exit_reason = "TIME"
            exit_at = _safe_dt(idx[min(i + hold_bars, len(df) - 1)])
            exit_price = float(opens[min(i + hold_bars, len(df) - 1)])

            for j in range(i + 1, min(i + 1 + hold_bars, len(df))):
                h = float(highs[j])
                l = float(lows[j])

                if h >= stop:
                    exit_reason = "SL"
                    exit_at = _safe_dt(idx[j])
                    exit_price = stop * (1.0 + float(slip_pct))
                    break
                if l <= take:
                    exit_reason = "TP"
                    exit_at = _safe_dt(idx[j])
                    exit_price = take * (1.0 + float(slip_pct))
                    break

            pnl = (entry - exit_price) * shares
            equity += pnl
            equity_curve.append(equity)
            pnls.append(pnl)

            holding_minutes = int(round((exit_at - entry_at).total_seconds() / 60.0))
            trades.append(
                _Trade(
                    ticker=ticker,
                    strategy="BREAKOUT",
                    side="SHORT",
                    entry_at=entry_at,
                    entry_price=float(entry),
                    exit_at=exit_at,
                    exit_price=float(exit_price),
                    exit_reason=exit_reason,
                    size=int(shares),
                    pnl_yen=int(round(pnl)),
                    rr=float(rr),
                    holding_minutes=max(0, holding_minutes),
                )
            )
            continue

    return trades, pnls, equity_curve


def _simulate_vwap(
    *,
    ticker: str,
    window_days: int,
    rr: float,
    slip_pct: float,
    stop_pct: float,
    risk_trade_pct: float,
    base_equity_yen: int,
    hold_bars: int = 20,
    bars_per_day: int = 70,
) -> Tuple[List[_Trade], List[float], List[float]]:
    df = fetch_5m(ticker, prefer_period="60d" if window_days > 20 else "20d")
    if df.empty:
        return [], [], []

    df = df.iloc[-window_days * bars_per_day :].copy()
    if len(df) < 200:
        return [], [], []

    # 簡易VWAP（期間累積）
    pv = (df["close"] * df["volume"]).cumsum()
    vv = (df["volume"]).cumsum().replace(0, 1)
    df["vwap"] = pv / vv

    equity = float(base_equity_yen)
    equity_curve: List[float] = [equity]
    pnls: List[float] = []
    trades: List[_Trade] = []

    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    vwaps = df["vwap"].values
    idx = df.index

    for i in range(5, len(df) - 2):
        price = float(closes[i])
        vwap = float(vwaps[i])
        nxt_open = float(opens[i + 1])

        risk_yen = equity * float(risk_trade_pct)
        stop_yen_per_share = price * float(stop_pct)
        shares = int((risk_yen / max(stop_yen_per_share, 1e-9)) // 100) * 100
        if shares < 100:
            continue

        # 買いのみ（VWAP上）: 触れたら入る
        if price > vwap and float(lows[i]) <= vwap * 1.0005:
            entry_at = _safe_dt(idx[i + 1])
            entry = nxt_open * (1.0 + float(slip_pct))
            stop = entry * (1.0 - float(stop_pct))
            take = entry * (1.0 + float(stop_pct) * float(rr))

            exit_reason = "TIME"
            exit_at = _safe_dt(idx[min(i + hold_bars, len(df) - 1)])
            exit_price = float(opens[min(i + hold_bars, len(df) - 1)])

            for j in range(i + 1, min(i + 1 + hold_bars, len(df))):
                h = float(highs[j])
                l = float(lows[j])
                if l <= stop:
                    exit_reason = "SL"
                    exit_at = _safe_dt(idx[j])
                    exit_price = stop * (1.0 - float(slip_pct))
                    break
                if h >= take:
                    exit_reason = "TP"
                    exit_at = _safe_dt(idx[j])
                    exit_price = take * (1.0 - float(slip_pct))
                    break

            pnl = (exit_price - entry) * shares
            equity += pnl
            equity_curve.append(equity)
            pnls.append(pnl)

            holding_minutes = int(round((exit_at - entry_at).total_seconds() / 60.0))
            trades.append(
                _Trade(
                    ticker=ticker,
                    strategy="VWAP",
                    side="LONG",
                    entry_at=entry_at,
                    entry_price=float(entry),
                    exit_at=exit_at,
                    exit_price=float(exit_price),
                    exit_reason=exit_reason,
                    size=int(shares),
                    pnl_yen=int(round(pnl)),
                    rr=float(rr),
                    holding_minutes=max(0, holding_minutes),
                )
            )
            continue

        # 売りのみ（VWAP下）: 触れたら入る
        if price < vwap and float(highs[i]) >= vwap * 0.9995:
            entry_at = _safe_dt(idx[i + 1])
            entry = nxt_open * (1.0 - float(slip_pct))
            stop = entry * (1.0 + float(stop_pct))
            take = entry * (1.0 - float(stop_pct) * float(rr))

            exit_reason = "TIME"
            exit_at = _safe_dt(idx[min(i + hold_bars, len(df) - 1)])
            exit_price = float(opens[min(i + hold_bars, len(df) - 1)])

            for j in range(i + 1, min(i + 1 + hold_bars, len(df))):
                h = float(highs[j])
                l = float(lows[j])
                if h >= stop:
                    exit_reason = "SL"
                    exit_at = _safe_dt(idx[j])
                    exit_price = stop * (1.0 + float(slip_pct))
                    break
                if l <= take:
                    exit_reason = "TP"
                    exit_at = _safe_dt(idx[j])
                    exit_price = take * (1.0 + float(slip_pct))
                    break

            pnl = (entry - exit_price) * shares
            equity += pnl
            equity_curve.append(equity)
            pnls.append(pnl)

            holding_minutes = int(round((exit_at - entry_at).total_seconds() / 60.0))
            trades.append(
                _Trade(
                    ticker=ticker,
                    strategy="VWAP",
                    side="SHORT",
                    entry_at=entry_at,
                    entry_price=float(entry),
                    exit_at=exit_at,
                    exit_price=float(exit_price),
                    exit_reason=exit_reason,
                    size=int(shares),
                    pnl_yen=int(round(pnl)),
                    rr=float(rr),
                    holding_minutes=max(0, holding_minutes),
                )
            )
            continue

    return trades, pnls, equity_curve


# =========================================================
# 公開API：詳細バックテストを Universe（複数銘柄）で回す
# =========================================================
@transaction.atomic
def run_detailed_backtests_for_universe(
    *,
    snapshot: AutoTradeSettingSnapshot,
    picks: List[str],
    windows: Optional[List[int]] = None,
    strategies: Optional[List[str]] = None,
    asof_date=None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    詳細バックテストを「複数銘柄」に対して実行し、
    - AutoTradeBacktestRunDetail（実行メタ）を作る
    - AutoTradeExecution（1トレード=1レコード）を作る
    - gate判定に使える metrics を “返す”（保存しない）
    を行う。

    返り値（例）:
    {
      "BREAKOUT": {
        "20": {"metrics": {...}, "trades": 123},
        "60": {"metrics": {...}, "trades": 300},
        "120":{"metrics": {...}, "trades": 600}
      },
      "VWAP": {...}
    }
    """

    if asof_date is None:
        asof_date = timezone.localdate()

    windows = windows or list(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 60, 120]))
    strategies = strategies or ["BREAKOUT", "VWAP"]

    # Snapshot（固定設定）から優先して読む（無ければ settings フォールバック）
    snap = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}

    slip_pct = float(snap.get("slippage_pct", getattr(settings, "AUTOTRADE_SLIPPAGE_PCT", 0.0008)))
    risk_trade_pct = float(snap.get("risk_trade_pct", getattr(settings, "AUTOTRADE_RISK_TRADE_PCT", 0.0015)))

    rr_breakout = float(snap.get("rr_breakout", getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0)))
    rr_vwap = float(snap.get("rr_vwap", getattr(settings, "AUTOTRADE_RR_VWAP", 1.5)))

    # stop_pct は “将来パラメータ化” 前提でここに置く（今は初期値）
    stop_pct_breakout = float(snap.get("stop_pct_breakout", 0.0030))
    stop_pct_vwap = float(snap.get("stop_pct_vwap", 0.0025))

    base_equity_yen = int(snap.get("base_equity_yen", getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)))

    user = snapshot.user

    result: Dict[str, Dict[str, Any]] = {s: {} for s in strategies}

    for strategy in strategies:
        for window_days in windows:
            # -------------------------------------------------
            # 再実行の増殖を防ぐ（同条件があればスキップ）
            # -------------------------------------------------
            if not force:
                exists = AutoTradeBacktestRunDetail.objects.filter(
                    user=user,
                    snapshot=snapshot,
                    strategy=strategy,
                    window_days=int(window_days),
                    end_date=asof_date,
                ).exists()
                if exists:
                    # 既に同条件の実行がある → 返り値は最低限のダミー
                    result[strategy][str(window_days)] = {
                        "metrics": {"trades": 0, "profit_factor": 0.0, "max_drawdown_pct": 1.0, "total_pnl_yen": 0},
                        "trades": 0,
                        "skipped": True,
                    }
                    continue

            # -------------------------------------------------
            # 実行メタ（RunDetail）を作る
            # -------------------------------------------------
            # start/end は “厳密に営業日” に寄せるのは次の強化。
            # 今は window_days をざっくり「過去window日」として保存する。
            start_date = asof_date - timezone.timedelta(days=int(window_days) * 2)  # バッファ（休日を吸収）
            end_date = asof_date

            run_detail = AutoTradeBacktestRunDetail.objects.create(
                user=user,
                snapshot=snapshot,
                strategy=strategy,
                window_days=int(window_days),
                start_date=start_date,
                end_date=end_date,
                executed_at=timezone.now(),
            )

            # -------------------------------------------------
            # 各銘柄をシミュレーション → trades を溜める
            # -------------------------------------------------
            all_trades: List[_Trade] = []
            all_pnls: List[float] = []
            equity_curve_agg: List[float] = [float(base_equity_yen)]

            for ticker in picks:
                if strategy == "BREAKOUT":
                    trades, pnls, eq = _simulate_breakout(
                        ticker=ticker,
                        window_days=int(window_days),
                        rr=rr_breakout,
                        slip_pct=slip_pct,
                        stop_pct=stop_pct_breakout,
                        risk_trade_pct=risk_trade_pct,
                        base_equity_yen=base_equity_yen,
                    )
                else:  # VWAP
                    trades, pnls, eq = _simulate_vwap(
                        ticker=ticker,
                        window_days=int(window_days),
                        rr=rr_vwap,
                        slip_pct=slip_pct,
                        stop_pct=stop_pct_vwap,
                        risk_trade_pct=risk_trade_pct,
                        base_equity_yen=base_equity_yen,
                    )

                all_trades.extend(trades)
                all_pnls.extend(pnls)

                # 合成の equity_curve は厳密じゃないが DD の目安には使える
                # （次フェーズで “ポートフォリオ同時制約” を入れると改善される）
                if eq:
                    equity_curve_agg.append(float(eq[-1]))

            # -------------------------------------------------
            # Execution をDBへ保存（1トレード=1レコード）
            # -------------------------------------------------
            exec_objs: List[AutoTradeExecution] = []
            for t in all_trades:
                exec_objs.append(
                    AutoTradeExecution(
                        user=user,
                        snapshot=snapshot,
                        mode="BACKTEST",
                        strategy=t.strategy,
                        ticker=t.ticker,
                        side=t.side,
                        entry_at=t.entry_at,
                        entry_price=float(t.entry_price),
                        size=int(t.size),
                        exit_at=t.exit_at,
                        exit_price=float(t.exit_price),
                        exit_reason=t.exit_reason,
                        pnl_yen=int(t.pnl_yen),
                        rr=float(t.rr),
                        holding_minutes=int(t.holding_minutes),
                        created_at=timezone.now(),
                    )
                )

            if exec_objs:
                AutoTradeExecution.objects.bulk_create(exec_objs, batch_size=1000)

            # -------------------------------------------------
            # メトリクス算出（保存しない / 返すだけ）
            # -------------------------------------------------
            metrics = _summarize_metrics(all_pnls, equity_curve_agg)

            result[strategy][str(window_days)] = {
                "metrics": metrics,
                "trades": int(metrics.get("trades") or 0),
                "run_detail_id": run_detail.id,
                "skipped": False,
            }

    return result