# -*- coding: utf-8 -*-
"""
ファイル: scripts/daytrade_analyze_trades_detail.py

これは何？
- daytrade_backtest_multi_simple.py が保存した trades_detail.json を解析する。
- 運用に役立つ「負け方・取り逃し・銘柄別の偏り」をワンタップで可視化する。
- 既存バックテストは一切触らず、“分析だけ”を進化させるための補助スクリプト。

前提
- trades_detail.json が存在すること
  例: media/aiapp/daytrade/reports/20260125/trades_detail.json

主な出力（ターミナル）
- exit_reason 別の集計（件数/勝率/合計PnL/平均R/平均保有分/平均MFE/平均MAE）
- stop_loss の「銘柄別」ランキング（件数/勝率/合計PnL/avgR/avgMAE）
- time_limit の「取り逃し」抽出（MFEが出たのに負け/微益で終わった）
- time_limit_guard の効果確認（guardがプラスになっているか）

保存
- media/aiapp/daytrade/reports/YYYYMMDD/analysis_report.json

使い方
  PYTHONPATH=. DJANGO_SETTINGS_MODULE=config.settings python scripts/daytrade_analyze_trades_detail.py
  PYTHONPATH=. DJANGO_SETTINGS_MODULE=config.settings python scripts/daytrade_analyze_trades_detail.py --date 20260125
  PYTHONPATH=. DJANGO_SETTINGS_MODULE=config.settings python scripts/daytrade_analyze_trades_detail.py --date 20260125 --reason stop_loss

注意
- trades_detail.json の entry_dt/exit_dt が "Z" 付きでも壊れないようにパースを強化。
- held_minutes が 0/欠損でも entry_dt/exit_dt から再計算して埋める。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from django.conf import settings


# =========================
# utils
# =========================
def _safe_int(x, default: int = 0) -> int:
    try:
        if x is None:
            return int(default)
        return int(x)
    except Exception:
        return int(default)


def _safe_float(x, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _safe_str(x, default: str = "") -> str:
    try:
        if x is None:
            return str(default)
        return str(x)
    except Exception:
        return str(default)


def _fmt_pct(x: float) -> str:
    try:
        return f"{x*100:.1f}%"
    except Exception:
        return "0.0%"


def _mean(xs: List[float]) -> float:
    try:
        if not xs:
            return 0.0
        return float(np.mean(np.array(xs, dtype="float64")))
    except Exception:
        return 0.0


def _percentile(xs: List[float], p: int) -> float:
    try:
        if not xs:
            return 0.0
        return float(np.percentile(np.array(xs, dtype="float64"), p))
    except Exception:
        return 0.0


def _normalize_iso(s: str) -> str:
    """
    datetime.fromisoformat が受け付けない形を補正する。
    - "Z" 終端 -> "+00:00"
    """
    t = (s or "").strip()
    if not t:
        return t
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    return t


def _parse_dt(s: Any) -> Optional[datetime]:
    """
    ISO文字列をdatetimeにする（Z/offset対応）
    """
    try:
        if s is None:
            return None
        if isinstance(s, datetime):
            return s
        txt = _safe_str(s, "").strip()
        if not txt:
            return None
        txt = _normalize_iso(txt)
        return datetime.fromisoformat(txt)
    except Exception:
        return None


def _report_dir(date_yyyymmdd: str) -> Path:
    p = Path(settings.MEDIA_ROOT) / "aiapp" / "daytrade" / "reports" / date_yyyymmdd
    p.mkdir(parents=True, exist_ok=True)
    return p


def _default_date_str() -> str:
    return date.today().strftime("%Y%m%d")


def _calc_held_minutes(entry_dt: Optional[datetime], exit_dt: Optional[datetime]) -> float:
    try:
        if entry_dt is None or exit_dt is None:
            return 0.0
        return float((exit_dt - entry_dt).total_seconds() / 60.0)
    except Exception:
        return 0.0


# =========================
# data model (loose)
# =========================
@dataclass
class TradeRow:
    ticker: str
    trade_date: str
    entry_dt: Optional[datetime]
    exit_dt: Optional[datetime]
    entry_price: float
    exit_price: float
    qty: int
    pnl_yen: int
    r: float
    exit_reason: str
    held_minutes: float
    mfe_r: float
    mae_r: float


def _normalize_reason(r: Any) -> str:
    s = _safe_str(r, "").strip()
    return s if s else "unknown"


def _load_trades_detail(path: Path) -> Tuple[Dict[str, Any], List[TradeRow]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    trades_raw = raw.get("trades", [])
    meta = raw.get("meta", {}) or {}

    rows: List[TradeRow] = []
    for tr in trades_raw:
        if not isinstance(tr, dict):
            continue

        ticker = _safe_str(tr.get("ticker", ""), "").strip()
        trade_date = _safe_str(tr.get("date_str", ""), "").strip()

        entry_dt = _parse_dt(tr.get("entry_dt"))
        exit_dt = _parse_dt(tr.get("exit_dt"))

        entry_price = _safe_float(tr.get("entry_price"), 0.0)
        exit_price = _safe_float(tr.get("exit_price"), 0.0)
        qty = _safe_int(tr.get("qty"), 0)

        pnl = _safe_int(tr.get("pnl_yen"), 0)
        r = _safe_float(tr.get("r"), 0.0)

        exit_reason = _normalize_reason(tr.get("exit_reason"))

        held_minutes = _safe_float(tr.get("held_minutes"), 0.0)

        # ★ held_minutes が 0/欠損でも、entry/exit から復元（Zパース失敗対策込み）
        if (held_minutes <= 0.0) and (entry_dt is not None) and (exit_dt is not None):
            held_minutes = _calc_held_minutes(entry_dt, exit_dt)

        mfe_r = _safe_float(tr.get("mfe_r"), 0.0)
        mae_r = _safe_float(tr.get("mae_r"), 0.0)

        rows.append(
            TradeRow(
                ticker=ticker,
                trade_date=trade_date,
                entry_dt=entry_dt,
                exit_dt=exit_dt,
                entry_price=entry_price,
                exit_price=exit_price,
                qty=qty,
                pnl_yen=pnl,
                r=r,
                exit_reason=exit_reason,
                held_minutes=held_minutes,
                mfe_r=mfe_r,
                mae_r=mae_r,
            )
        )

    return meta, rows


# =========================
# analysis
# =========================
def _group_by_reason(rows: List[TradeRow]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for tr in rows:
        key = tr.exit_reason
        slot = out.setdefault(
            key,
            {
                "trades": 0,
                "wins": 0,
                "pnl": 0,
                "sum_r": 0.0,
                "held_minutes": [],
                "mfe_r": [],
                "mae_r": [],
            },
        )
        slot["trades"] += 1
        slot["pnl"] += int(tr.pnl_yen)
        slot["sum_r"] += float(tr.r)
        if tr.pnl_yen >= 0:
            slot["wins"] += 1
        slot["held_minutes"].append(float(tr.held_minutes))
        slot["mfe_r"].append(float(tr.mfe_r))
        slot["mae_r"].append(float(tr.mae_r))
    return out


def _group_by_ticker_for_reason(rows: List[TradeRow], reason_prefix: str) -> List[Dict[str, Any]]:
    """
    reason_prefix で startswith するものを対象に銘柄別集計
    例: "stop_loss", "time_limit", "strategy_exit"
    """
    per: Dict[str, Dict[str, Any]] = {}
    for tr in rows:
        if not tr.exit_reason.startswith(reason_prefix):
            continue
        key = tr.ticker or "UNKNOWN"
        slot = per.setdefault(
            key,
            {
                "ticker": key,
                "trades": 0,
                "wins": 0,
                "pnl": 0,
                "sum_r": 0.0,
                "avg_mae_r": 0.0,
                "avg_mfe_r": 0.0,
                "avg_hold_min": 0.0,
                "_mae_r": [],
                "_mfe_r": [],
                "_hold": [],
            },
        )
        slot["trades"] += 1
        slot["pnl"] += int(tr.pnl_yen)
        slot["sum_r"] += float(tr.r)
        if tr.pnl_yen >= 0:
            slot["wins"] += 1
        slot["_mae_r"].append(float(tr.mae_r))
        slot["_mfe_r"].append(float(tr.mfe_r))
        slot["_hold"].append(float(tr.held_minutes))

    out = []
    for _, st in per.items():
        tcnt = int(st["trades"])
        wins = int(st["wins"])
        st["winrate"] = (wins / tcnt) if tcnt > 0 else 0.0
        st["avg_r"] = (float(st["sum_r"]) / tcnt) if tcnt > 0 else 0.0
        st["avg_mae_r"] = _mean(st["_mae_r"])
        st["avg_mfe_r"] = _mean(st["_mfe_r"])
        st["avg_hold_min"] = _mean(st["_hold"])
        st.pop("_mae_r", None)
        st.pop("_mfe_r", None)
        st.pop("_hold", None)
        out.append(st)

    # trades多い順 → pnl悪い順（見やすさ重視）
    out.sort(key=lambda x: (int(x.get("trades", 0)), -int(x.get("pnl", 0))), reverse=True)
    return out


def _extract_time_limit_missed(rows: List[TradeRow]) -> Dict[str, List[Dict[str, Any]]]:
    """
    time_limit系の「取り逃し」を抽出して返す。
    - missed_big_mfe_loss: mfe_r >= 0.30 なのに pnl<0
    - missed_big_mfe_small: mfe_r >= 0.50 なのに pnl<=0
    """
    missed_big_mfe_loss = []
    missed_big_mfe_small = []

    for tr in rows:
        if not tr.exit_reason.startswith("time_limit"):
            continue

        if tr.mfe_r >= 0.30 and tr.pnl_yen < 0:
            missed_big_mfe_loss.append(tr)
        if tr.mfe_r >= 0.50 and tr.pnl_yen <= 0:
            missed_big_mfe_small.append(tr)

    def to_dict(tr: TradeRow) -> Dict[str, Any]:
        return {
            "ticker": tr.ticker,
            "date": tr.trade_date,
            "exit_reason": tr.exit_reason,
            "pnl_yen": tr.pnl_yen,
            "r": tr.r,
            "held_min": tr.held_minutes,
            "mfe_r": tr.mfe_r,
            "mae_r": tr.mae_r,
            "entry_price": tr.entry_price,
            "exit_price": tr.exit_price,
            "qty": tr.qty,
            "entry_dt": tr.entry_dt.isoformat() if tr.entry_dt else None,
            "exit_dt": tr.exit_dt.isoformat() if tr.exit_dt else None,
        }

    missed_big_mfe_loss_d = [to_dict(x) for x in missed_big_mfe_loss]
    missed_big_mfe_small_d = [to_dict(x) for x in missed_big_mfe_small]

    # 最も痛い順（pnlが小さい＝マイナスが大きい）
    missed_big_mfe_loss_d.sort(key=lambda x: int(x.get("pnl_yen", 0)))
    missed_big_mfe_small_d.sort(key=lambda x: int(x.get("pnl_yen", 0)))

    return {
        "missed_big_mfe_loss": missed_big_mfe_loss_d[:50],
        "missed_big_mfe_small": missed_big_mfe_small_d[:50],
    }


def _print_reason_table(reason_stats: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = []
    for reason, st in reason_stats.items():
        tcnt = int(st.get("trades", 0))
        if tcnt <= 0:
            continue
        items.append((tcnt, reason, st))
    items.sort(reverse=True, key=lambda x: x[0])

    print("---- exit_reason summary ----")
    rows_out = []

    for tcnt, reason, st in items:
        wins = int(st.get("wins", 0))
        pnl = int(st.get("pnl", 0))
        sum_r = float(st.get("sum_r", 0.0))
        winrate = (wins / tcnt) if tcnt > 0 else 0.0
        avg_r = (sum_r / tcnt) if tcnt > 0 else 0.0

        held = list(st.get("held_minutes", [])) or []
        mfe = list(st.get("mfe_r", [])) or []
        mae = list(st.get("mae_r", [])) or []

        avg_hold = _mean(held)
        avg_mfe = _mean(mfe)
        avg_mae = _mean(mae)

        p50_hold = _percentile(held, 50)
        p90_hold = _percentile(held, 90)

        print(
            f"{reason:28s} trades={tcnt:4d} winrate={winrate*100:5.1f}% "
            f"pnl={pnl:9d} avg_r={avg_r:7.4f} "
            f"avg_hold_min={avg_hold:5.1f} p50={p50_hold:5.1f} p90={p90_hold:5.1f} "
            f"avg_mfe_r={avg_mfe:6.3f} avg_mae_r={avg_mae:6.3f}"
        )

        rows_out.append(
            {
                "exit_reason": reason,
                "trades": tcnt,
                "wins": wins,
                "winrate": winrate,
                "pnl": pnl,
                "avg_r": avg_r,
                "avg_hold_min": avg_hold,
                "p50_hold_min": p50_hold,
                "p90_hold_min": p90_hold,
                "avg_mfe_r": avg_mfe,
                "avg_mae_r": avg_mae,
            }
        )

    return rows_out


def _print_ticker_ranking(title: str, rows: List[Dict[str, Any]], topn: int = 15) -> None:
    print("")
    print(title)
    if not rows:
        print("(no rows)")
        return
    for i, st in enumerate(rows[:topn], 1):
        ticker = _safe_str(st.get("ticker", "UNKNOWN"))
        tcnt = _safe_int(st.get("trades", 0))
        pnl = _safe_int(st.get("pnl", 0))
        winrate = _safe_float(st.get("winrate", 0.0))
        avg_r = _safe_float(st.get("avg_r", 0.0))
        avg_hold = _safe_float(st.get("avg_hold_min", 0.0))
        avg_mfe = _safe_float(st.get("avg_mfe_r", 0.0))
        avg_mae = _safe_float(st.get("avg_mae_r", 0.0))
        print(
            f"{i:2d}. [{ticker}] trades={tcnt:3d} winrate={_fmt_pct(winrate):>6s} pnl={pnl:9d} "
            f"avg_r={avg_r:7.4f} avg_hold={avg_hold:5.1f} avg_mfe_r={avg_mfe:6.3f} avg_mae_r={avg_mae:6.3f}"
        )


# =========================
# main
# =========================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="report date YYYYMMDD (default: today)")
    ap.add_argument("--reason", default="", help="filter by exit_reason prefix (e.g. stop_loss/time_limit)")
    args = ap.parse_args()

    date_str = _safe_str(args.date, "").strip()
    if not date_str:
        date_str = _default_date_str()

    report_dir = _report_dir(date_str)
    in_path = report_dir / "trades_detail.json"
    if not in_path.exists():
        raise SystemExit(f"trades_detail.json not found: {in_path}")

    meta, rows = _load_trades_detail(in_path)

    reason_prefix = _safe_str(args.reason, "").strip()
    if reason_prefix:
        rows = [r for r in rows if r.exit_reason.startswith(reason_prefix)]

    print("=== daytrade analyze trades_detail ===")
    print("date =", date_str)
    print("input =", str(in_path))
    print("trades =", len(rows))
    if meta:
        pid = meta.get("policy_id")
        if pid:
            print("policy_id =", pid)
    if reason_prefix:
        print("filter_reason_prefix =", reason_prefix)
    print("")

    reason_stats = _group_by_reason(rows)
    summary_rows = _print_reason_table(reason_stats)

    stop_rank = _group_by_ticker_for_reason(rows, "stop_loss")
    tl_rank = _group_by_ticker_for_reason(rows, "time_limit")
    guard_rank = _group_by_ticker_for_reason(rows, "time_limit_guard")

    _print_ticker_ranking("---- ticker ranking (stop_loss) ----", stop_rank, topn=15)
    _print_ticker_ranking("---- ticker ranking (time_limit) ----", tl_rank, topn=15)
    _print_ticker_ranking("---- ticker ranking (time_limit_guard) ----", guard_rank, topn=15)

    missed = _extract_time_limit_missed(rows)
    print("")
    print("---- time_limit missed (quick view) ----")
    print("missed_big_mfe_loss  =", len(missed.get("missed_big_mfe_loss", [])))
    print("missed_big_mfe_small =", len(missed.get("missed_big_mfe_small", [])))
    if missed.get("missed_big_mfe_loss"):
        x = missed["missed_big_mfe_loss"][0]
        print(
            f"worst_missed_loss: ticker={x.get('ticker')} date={x.get('date')} pnl={x.get('pnl_yen')} "
            f"mfe_r={_safe_float(x.get('mfe_r')):.3f} mae_r={_safe_float(x.get('mae_r')):.3f} "
            f"held={_safe_float(x.get('held_min')):.1f}m reason={x.get('exit_reason')}"
        )

    out_path = report_dir / "analysis_report.json"
    payload = {
        "generated_at": datetime.now().isoformat(),
        "date": date_str,
        "input": str(in_path),
        "meta": meta,
        "filter_reason_prefix": reason_prefix or None,
        "summary_by_reason": summary_rows,
        "ticker_rankings": {
            "stop_loss": stop_rank[:50],
            "time_limit": tl_rank[:50],
            "time_limit_guard": guard_rank[:50],
        },
        "time_limit_missed": missed,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("")
    print("saved analysis report =", str(out_path))
    print("=== done ===")


if __name__ == "__main__":
    main()