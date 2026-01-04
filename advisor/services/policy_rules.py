# advisor/services/policy_rules.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
from datetime import date, datetime, timezone, timedelta

import math
import pandas as pd
import yfinance as yf

JST = timezone(timedelta(hours=9))

@dataclass
class ExitTargets:
    tp_price: Optional[int]
    sl_price: Optional[int]
    trail_atr_mult: Optional[float]
    time_exit_due: bool
    notes: Dict[str, Any]

# ---- ATRの取得（軽量化のため直近60営業日程度）----
def _fetch_atr14(ticker: str, lookback_days: int = 90) -> Optional[float]:
    end = (datetime.now(JST).date() + timedelta(days=1)).isoformat()
    start = (datetime.now(JST).date() - timedelta(days=max(65, lookback_days))).isoformat()
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True, group_by="column")
    if df is None or len(df) < 40:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        # 単一化（列名は 'Open','High','Low','Close',... に統一）
        try:
            if ticker in df.columns.get_level_values(-1):
                df = df.xs(ticker, axis=1, level=-1)
            elif ticker in df.columns.get_level_values(0):
                df = df.xs(ticker, axis=1, level=0)
            else:
                df.columns = [c[0] if isinstance(c, tuple) else str(c) for c in df.columns]
        except Exception:
            df.columns = [c[0] if isinstance(c, tuple) else str(c) for c in df.columns]
    df.columns = [str(c).strip().title() for c in df.columns]
    if not set(["High","Low","Close"]).issubset(df.columns):
        return None
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high-low).abs(), (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    atr14 = tr.ewm(alpha=1/14, adjust=False).mean().iloc[-1]
    try:
        return float(atr14)
    except Exception:
        return None

def _safe_int(x: Optional[float]) -> Optional[int]:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return int(round(float(x)))

def _pick(d: Dict[str, Any], *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def compute_exit_targets(
    *,
    policy: Dict[str, Any],
    ticker: str,
    entry_price: Optional[int],
    days_held: Optional[int] = None,
    atr14_hint: Optional[float] = None,
) -> ExitTargets:
    """
    ポリシー内の数値ルール（exits）から、TP/SL/時間切れ/トレーリング情報を決定。
    - policy.rule_json の想定:
      {
        "targets": {"tp_pct": 0.12, "sl_pct": 0.05},      # 既存互換（%指定・オプション）
        "exits": {
          "tp_r_multiple": 2.0,     # 利確: R倍（R=entry - sl）
          "sl_atr_multiple": 1.5,   # 損切: ATRの倍率（価格=entry - 1.5*ATR）
          "trail_atr_multiple": 3.0,# 伸ばすときのトレーリングATR倍率（表示用目安）
          "time_exit_days": 20      # 日数での時間切れ（>=でTrue）
        }
      }
    優先順位：
      1) exits.* があればそちら優先（数値ルール固定の趣旨）
      2) 無い箇所は targets.tp_pct / sl_pct をフォールバックで使用
    """
    exits = dict(_pick(policy, "exits", default={}) or {})
    targets = dict(_pick(policy, "targets", default={}) or {})

    tp_r = exits.get("tp_r_multiple")  # 例: 2.0
    sl_atr_mult = exits.get("sl_atr_multiple")  # 例: 1.5
    trail_mult = exits.get("trail_atr_multiple")  # 例: 3.0
    time_days = exits.get("time_exit_days")  # 例: 20

    tp_pct = targets.get("tp_pct")
    sl_pct = targets.get("sl_pct")

    if entry_price is None or entry_price <= 0:
        return ExitTargets(
            tp_price=None, sl_price=None, trail_atr_mult=trail_mult,
            time_exit_due=False, notes={"reason": "no_entry_price"}
        )

    # ATR（ヒント無ければ取得）
    atr14 = atr14_hint if atr14_hint is not None else _fetch_atr14(ticker)
    notes = {"atr14": atr14}

    # --- 損切り（優先：ATR倍数 → 次点：%） ---
    sl_price = None
    used_sl = None
    if isinstance(sl_atr_mult, (int, float)) and atr14 and atr14 > 0:
        sl_price = _safe_int(entry_price - sl_atr_mult * atr14)
        used_sl = ("atr", sl_atr_mult)
    elif isinstance(sl_pct, (int, float)) and sl_pct > 0:
        sl_price = _safe_int(entry_price * (1 - float(sl_pct)))
        used_sl = ("pct", sl_pct)

    # --- 利確（優先：R倍 → 次点：%） ---
    tp_price = None
    if isinstance(tp_r, (int, float)) and tp_r > 0 and sl_price and sl_price < entry_price:
        R = entry_price - sl_price
        tp_price = _safe_int(entry_price + tp_r * R)
        notes["tp_from"] = ("R", tp_r)
    elif isinstance(tp_pct, (int, float)) and tp_pct > 0:
        tp_price = _safe_int(entry_price * (1 + float(tp_pct)))
        notes["tp_from"] = ("pct", tp_pct)

    # --- 時間切れ（>=） ---
    time_exit_due = False
    if isinstance(time_days, int) and time_days > 0 and isinstance(days_held, int):
        time_exit_due = days_held >= time_days

    # 付記
    notes["sl_from"] = used_sl
    notes["trail_atr_mult"] = trail_mult

    return ExitTargets(
        tp_price=tp_price,
        sl_price=sl_price,
        trail_atr_mult=trail_mult,
        time_exit_due=bool(time_exit_due),
        notes=notes,
    )