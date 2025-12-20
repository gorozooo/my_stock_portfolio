# aiapp/management/commands/ai_sim_eval.py
# -*- coding: utf-8 -*-
"""
ai_sim_eval

AI紙シミュ評価（5分足）→ VirtualTrade に eval_* / R / EV_true_pro / rank_pro を反映

重要方針（あなたのルール）：
- 評価開始時刻は固定しない
- opened_at（注文を作った時刻＝現実世界で注文を出した時刻）から評価開始
- ただし時刻比較は必ず tz を揃える（DBはUTC保持、5分足の ts はJST）

今回の修正点（要点）：
1) 対象抽出を trade_date 基準に統一
2) 評価開始 = opened_at を JST に localtime した上で 5分足 ts と比較
3) 例外時ログの self.verbosity AttributeError を潰す（options['verbosity'] 参照）
4) options.get("days") が 0 のとき `or 5` 等で化ける事故を防止
5) df["ts"] の tz を必ず Asia/Tokyo に寄せて比較（UTC/naive混在で no_bars を防ぐ）

★プロ仕様（9020の件の本丸）：
- BUYの指値は「上限価格」なので、寄り/直後の open が entry 以下なら entry 到達を待たずに open で約定する（marketable limit）
- SELLの指値は「下限価格」なので、寄り/直後の open が entry 以上なら open で約定する

★プロ仕様：寄り値(09:00)は別取得して寄り約定判定だけに使う
- 評価開始が 09:00 のときのみ、日足Open（=寄り値）を取得して marketable 判定に使う

★プロ仕様：評価期間（休日除外の営業日ベース）
- --horizon で「何営業日」評価するかを指定（デフォルト 3）
- 休日判定は「5分足が取れる日を営業日としてカウント」する（= 休日は自動スキップ）
- entry は起票日当日のみ（trade_date のみで約定判定）
  → 刺さらなければ即CLOSED(no_position)
- TP/SL ヒットで即CLOSED
- horizon営業日目の最後の足（15:30相当の最後のバー）で未達なら強制CLOSED（exit_reason="time_stop"）
- ★未来で horizon 最終営業日データが無い＝ carry（未確定）

★A案（Rを本質にしたプロ仕様）：
- 約定価格（exec_entry_px）基準で評価する
- 起票時に想定した R = |entry_plan - sl_plan|
- tp_ratio = |tp_plan - entry_plan| / R
- 実際の約定が「有利」にズレた場合のみ、TP/SL を exec_entry_px から同じR幅で再配置
  - BUYで exec < entry_plan のとき：SL=exec-R, TP=exec+R*tp_ratio
  - SELLで exec > entry_plan のとき：SL=exec+R, TP=exec-R*tp_ratio
- 不利ズレ（BUYで exec>entry_plan / SELLで exec<entry_plan）は、TP/SL を“据え置き”して構造を壊さない

★carry の定義（合意）：
- 「entry済み」かつ「最終日未到達」かつ「TP/SL未達」→ carry（翌日も評価対象）

★市場前の no_bars の扱い（合意）：
- 「まだデータ来てないだけ」は eval_exit_reason を空のままでスキップ（DB更新しない）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, datetime as _dt, time as _time, timedelta as _timedelta
from typing import Any, Dict, List, Optional, Tuple

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade
from aiapp.models.behavior_stats import BehaviorStats
from aiapp.services.bars_5m import load_5m_bars


# ==============================
# small helpers
# ==============================

def _jst_session_range(d: _date) -> Tuple[_dt, _dt]:
    """
    その営業日のザラ場時間（簡易）
    - 09:00〜15:30
    """
    tz = timezone.get_default_timezone()  # Asia/Tokyo
    start = timezone.make_aware(_dt.combine(d, _time(9, 0)), tz)
    end = timezone.make_aware(_dt.combine(d, _time(15, 30)), tz)
    return start, end


def _to_local(dt: Optional[_dt]) -> Optional[_dt]:
    if dt is None:
        return None
    try:
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)  # Asia/Tokyo
    except Exception:
        return None


def _safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None

        # pandas Series（1要素）対策
        try:
            import pandas as pd  # type: ignore
            if isinstance(x, pd.Series):
                if x.empty:
                    return None
                x = x.iloc[0]
        except Exception:
            pass

        # list/tuple（1要素）対策
        if isinstance(x, (list, tuple)):
            if len(x) == 0:
                return None
            x = x[0]

        f = float(x)
        if f != f:
            return None
        return f
    except Exception:
        return None


def _label(qty: Optional[int], pl_per_share: float) -> str:
    if qty is None or qty <= 0:
        return "no_position"
    if pl_per_share > 0:
        return "win"
    if pl_per_share < 0:
        return "lose"
    return "flat"


def _find_ohlc_columns(df) -> Tuple[Optional[Any], Optional[Any], Optional[Any], Optional[Any]]:
    """
    df.columns が str でも MultiIndex でも、
    'open' / 'low' / 'high' / 'close'(or 'adj close') を拾う。
    戻り値は df[...] のキーとして使える実カラム値。
    """
    open_col = low_col = high_col = close_col = None

    for col in df.columns:
        if isinstance(col, tuple):
            parts = [str(p).lower() for p in col if p is not None]
        else:
            parts = [str(col).lower()]

        if open_col is None and any(p == "open" for p in parts):
            open_col = col
        if low_col is None and any(p == "low" for p in parts):
            low_col = col
        if high_col is None and any(p == "high" for p in parts):
            high_col = col
        if close_col is None and any(p in ("close", "adj close") for p in parts):
            close_col = col

    return open_col, low_col, high_col, close_col


def _coerce_ts(val: Any, fallback: _dt) -> _dt:
    """
    row["ts"] を確実に datetime にする（SeriesやTimestampでもOK）。
    """
    try:
        import pandas as pd  # type: ignore
    except Exception:
        return fallback

    if isinstance(val, pd.Series):
        if not val.empty:
            val = val.iloc[0]
        else:
            return fallback

    try:
        ts = pd.Timestamp(val)
    except Exception:
        ts = pd.to_datetime(val, errors="coerce")

    if pd.isna(ts):
        return fallback

    dt = ts.to_pydatetime()
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_default_timezone())
    return timezone.localtime(dt)


def _ensure_ts_jst(df) -> Optional[Any]:
    """
    df["ts"] を必ず Asia/Tokyo の tz-aware datetime にそろえる。
    load_5m_bars の実装やデータ形状で tz がブレても、ここで吸収する。
    """
    try:
        import pandas as pd  # type: ignore
    except Exception:
        return None

    if "ts" not in df.columns:
        return None

    try:
        s = pd.to_datetime(df["ts"], errors="coerce")
    except Exception:
        return None

    try:
        # tz-aware の場合
        if getattr(s.dt, "tz", None) is not None:
            s = s.dt.tz_convert("Asia/Tokyo")
        else:
            # tz-naive の場合：JST として localize
            s = s.dt.tz_localize("Asia/Tokyo")
    except Exception:
        # object dtype などで dt が使えない場合の最後の砦
        try:
            s2 = []
            for x in s:
                if pd.isna(x):
                    s2.append(pd.NaT)
                    continue
                ts = pd.Timestamp(x)
                if ts.tzinfo is None:
                    ts = ts.tz_localize("Asia/Tokyo")
                else:
                    ts = ts.tz_convert("Asia/Tokyo")
                s2.append(ts)
            s = pd.Series(s2, index=df.index)
        except Exception:
            return None

    df["ts"] = s
    return df


def _yf_daily_open(code: str, d: _date) -> Optional[float]:
    """
    プロ仕様：寄り値(09:00)の代替として、日足の Open を取得する。
    取得できない場合は None。

    ※この関数は「寄り約定判定だけ」に使う。
    ※処理が増えるが、寄り値1個だけなら軽い。
    """
    try:
        import yfinance as yf  # type: ignore
        import pandas as pd  # type: ignore
    except Exception:
        return None

    ticker = f"{str(code)}.T"

    try:
        start = pd.Timestamp(d)
        end = pd.Timestamp(d) + pd.Timedelta(days=1)
        df = yf.download(
            tickers=ticker,
            start=start,
            end=end,
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        if df is None or len(df) == 0:
            return None

        open_val = None
        if "Open" in df.columns:
            open_val = df["Open"].iloc[0]
        else:
            for c in df.columns:
                if isinstance(c, tuple) and len(c) >= 1 and str(c[0]).lower() == "open":
                    open_val = df[c].iloc[0]
                    break

        return _safe_float(open_val)
    except Exception:
        return None


def _side(v: VirtualTrade) -> str:
    s = str(getattr(v, "side", "") or "BUY").upper().strip()
    return "SELL" if s == "SELL" else "BUY"


def _plan_params(v: VirtualTrade) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    entry/tp/sl は基本DB優先、無ければ replay["sim_order"] をフォールバック
    """
    replay = v.replay if isinstance(v.replay, dict) else {}
    sim_order = replay.get("sim_order") if isinstance(replay.get("sim_order"), dict) else {}

    entry = _safe_float(v.entry_px)
    tp = _safe_float(v.tp_px)
    sl = _safe_float(v.sl_px)

    if entry is None:
        entry = _safe_float(sim_order.get("entry"))
    if tp is None:
        tp = _safe_float(sim_order.get("tp"))
    if sl is None:
        sl = _safe_float(sim_order.get("sl"))

    return entry, tp, sl


def _reanchor_tp_sl_A(
    *,
    side: str,
    entry_plan: float,
    tp_plan: Optional[float],
    sl_plan: Optional[float],
    exec_entry_px: float,
) -> Tuple[Optional[float], Optional[float], Dict[str, Any]]:
    """
    A案：R基準でTP/SLを再配置（有利ズレのときのみ）
    戻り値： (tp_use, sl_use, meta)
    """
    meta: Dict[str, Any] = {}

    if sl_plan is None:
        meta["a_rule"] = "no_sl_plan"
        return tp_plan, sl_plan, meta

    r = abs(float(entry_plan) - float(sl_plan))
    if r <= 0:
        meta["a_rule"] = "bad_r"
        return tp_plan, sl_plan, meta

    tp_ratio = None
    if tp_plan is not None:
        tp_ratio = abs(float(tp_plan) - float(entry_plan)) / r

    meta["r_plan"] = float(r)
    meta["tp_ratio"] = float(tp_ratio) if tp_ratio is not None else None

    # 有利ズレ判定
    if side == "BUY":
        favorable = float(exec_entry_px) < float(entry_plan)
    else:
        favorable = float(exec_entry_px) > float(entry_plan)

    if not favorable:
        meta["a_rule"] = "not_favorable_keep_plan"
        return tp_plan, sl_plan, meta

    # 有利ズレなら、exec を基準に同じR幅で再配置
    if side == "BUY":
        sl_use = float(exec_entry_px) - r
        tp_use = (float(exec_entry_px) + r * float(tp_ratio)) if tp_ratio is not None else tp_plan
    else:
        sl_use = float(exec_entry_px) + r
        tp_use = (float(exec_entry_px) - r * float(tp_ratio)) if tp_ratio is not None else tp_plan

    meta["a_rule"] = "reanchored_by_exec"
    meta["tp_use"] = float(tp_use) if tp_use is not None else None
    meta["sl_use"] = float(sl_use) if sl_use is not None else None
    return tp_use, sl_use, meta


def _pl_per_share(side: str, exec_entry_px: float, exit_px: float) -> float:
    if side == "SELL":
        return float(exec_entry_px) - float(exit_px)
    return float(exit_px) - float(exec_entry_px)


def _is_trade_day_by_bars(code: str, d: _date) -> bool:
    """
    休日除外の営業日カウント用：
    その日の5分足が取れる＝営業日としてカウント
    """
    bars = load_5m_bars(code, d)
    return (bars is not None) and (len(bars) > 0)


def _collect_horizon_trade_dates(
    code: str,
    start_date: _date,
    horizon_bd: int,
    max_scan_days: int = 60
) -> List[_date]:
    """
    start_date を含めて horizon_bd 営業日分の日付リストを作る。
    営業日判定は「5分足が取れる日」でカウントする（休日は自動スキップ）。

    ★重要：
    - 未来日は当然 bars が無いので out が足りなくなることがある
      → それはエラーではなく carry の材料（exit側で carry にする）
    """
    if horizon_bd <= 0:
        horizon_bd = 1

    out: List[_date] = []
    d = start_date
    scanned = 0

    while len(out) < horizon_bd and scanned < max_scan_days:
        scanned += 1
        if _is_trade_day_by_bars(code, d):
            out.append(d)
        d = d + _timedelta(days=1)

    return out


# ==============================
# result dataclass
# ==============================

@dataclass
class EvalResult:
    ok: bool
    reason: str

    eval_entry_px: Optional[float] = None
    eval_entry_ts: Optional[_dt] = None

    eval_exit_px: Optional[float] = None
    eval_exit_ts: Optional[_dt] = None

    eval_exit_reason: str = ""  # hit_tp / hit_sl / time_stop / carry / no_position / (skip reasons...)
    pl_per_share: Optional[float] = None

    # 追加メタ（replayへ）
    meta: Optional[Dict[str, Any]] = None


# ==============================
# core evaluation
# ==============================

def _evaluate_entry_on_trade_date(
    v: VirtualTrade,
    *,
    verbose: int = 1,
) -> Tuple[bool, str, Optional[float], Optional[_dt], Optional[float], Optional[float], Optional[float], Dict[str, Any]]:
    """
    entry は起票日当日のみ（trade_date のみで約定判定）
    - opened_at（JST）以降で評価開始
    - 寄り判定だけは「日足Open（=寄り値）」を別取得して使う（プロ仕様）
    - marketable limit を再現：
      BUY：open <= entry なら open で即約定
      SELL：open >= entry なら open で即約定
    - 通常の指値判定：low<=entry<=high
    戻り値：
      (ok, reason, exec_entry_px, entry_ts, tp_use, sl_use, r_plan, meta)
    """
    trade_date = v.trade_date
    side = _side(v)

    entry_plan, tp_plan, sl_plan = _plan_params(v)
    if entry_plan is None:
        return False, "no_entry", None, None, None, None, None, {"entry_rule": "no_entry_plan"}

    bars = load_5m_bars(v.code, trade_date)
    if bars is None or len(bars) == 0:
        return False, "no_bars", None, None, None, None, None, {"entry_rule": "no_bars"}

    df = bars.copy()

    if "ts" not in df.columns:
        try:
            import pandas as pd  # type: ignore
            if isinstance(df.index, pd.DatetimeIndex):
                df = df.reset_index().rename(columns={df.index.name or "index": "ts"})
            else:
                return False, "no_ts", None, None, None, None, None, {"entry_rule": "no_ts"}
        except Exception:
            return False, "no_ts", None, None, None, None, None, {"entry_rule": "no_ts_exc"}

    df2 = _ensure_ts_jst(df)
    if df2 is None:
        return False, "bad_ts", None, None, None, None, None, {"entry_rule": "bad_ts"}
    df = df2

    open_col, low_col, high_col, close_col = _find_ohlc_columns(df)
    if low_col is None or high_col is None or close_col is None:
        return False, "no_ohlc", None, None, None, None, None, {"entry_rule": "no_ohlc"}

    opened_local = _to_local(v.opened_at)
    if opened_local is None:
        return False, "no_opened_at", None, None, None, None, None, {"entry_rule": "no_opened_at"}

    session_start, session_end = _jst_session_range(trade_date)

    if opened_local < session_start:
        active_start = session_start
    elif opened_local > session_end:
        return False, "no_bars_after_active", None, None, None, None, None, {"entry_rule": "after_session"}
    else:
        active_start = opened_local

    df_eff = df[(df["ts"] >= active_start) & (df["ts"] <= session_end)]
    if df_eff is None or len(df_eff) == 0:
        return False, "no_bars_after_active", None, None, None, None, None, {"entry_rule": "no_bars_after_active"}

    meta: Dict[str, Any] = {
        "entry_rule": "limit",
        "active_start": str(active_start),
        "session_start": str(session_start),
    }

    exec_entry_px: Optional[float] = None
    entry_ts: Optional[_dt] = None

    # --- 寄り(09:00)の特別判定（プロ仕様） ---
    if active_start == session_start:
        yori = _yf_daily_open(str(v.code), trade_date)
        meta["yori_open"] = yori

        if yori is not None:
            if side == "BUY":
                if float(yori) <= float(entry_plan):
                    exec_entry_px = float(yori)
                    entry_ts = session_start  # 09:00
                    meta["entry_fill"] = "yori_open_marketable"
            else:
                if float(yori) >= float(entry_plan):
                    exec_entry_px = float(yori)
                    entry_ts = session_start  # 09:00
                    meta["entry_fill"] = "yori_open_marketable"

    # --- 5分足での通常判定（寄りで刺さらなかった場合） ---
    if exec_entry_px is None or entry_ts is None:
        for _, row in df_eff.iterrows():
            try:
                lo = _safe_float(row[low_col])
                hi = _safe_float(row[high_col])
            except Exception:
                lo = hi = None

            o = None
            if open_col is not None:
                try:
                    o = _safe_float(row[open_col])
                except Exception:
                    o = None

            bar_ts = _coerce_ts(row["ts"], fallback=active_start)

            if side == "SELL":
                if o is not None and float(o) >= float(entry_plan):
                    exec_entry_px = float(o)
                    entry_ts = bar_ts
                    meta["entry_fill"] = "bar_open_marketable"
                    break
                if lo is not None and hi is not None and float(lo) <= float(entry_plan) <= float(hi):
                    exec_entry_px = float(entry_plan)
                    entry_ts = bar_ts
                    meta["entry_fill"] = "bar_touch_entry"
                    break
            else:
                if o is not None and float(o) <= float(entry_plan):
                    exec_entry_px = float(o)
                    entry_ts = bar_ts
                    meta["entry_fill"] = "bar_open_marketable"
                    break
                if lo is not None and hi is not None and float(lo) <= float(entry_plan) <= float(hi):
                    exec_entry_px = float(entry_plan)
                    entry_ts = bar_ts
                    meta["entry_fill"] = "bar_touch_entry"
                    break

    if exec_entry_px is None or entry_ts is None:
        meta["entry_fill"] = "no_position"
        return True, "no_position", None, None, None, None, None, meta

    # --- A案：有利ズレのときだけ TP/SL を exec 基準に再配置 ---
    tp_use, sl_use, a_meta = _reanchor_tp_sl_A(
        side=side,
        entry_plan=float(entry_plan),
        tp_plan=tp_plan,
        sl_plan=sl_plan,
        exec_entry_px=float(exec_entry_px),
    )
    meta.update({"A": a_meta})

    r_plan = _safe_float(a_meta.get("r_plan")) if isinstance(a_meta, dict) else None

    return True, "entry_ok", float(exec_entry_px), entry_ts, tp_use, sl_use, r_plan, meta


def _evaluate_exit_across_horizon(
    v: VirtualTrade,
    *,
    exec_entry_px: float,
    entry_ts: _dt,
    tp_use: Optional[float],
    sl_use: Optional[float],
    horizon_bd: int,
    verbose: int = 1,
) -> EvalResult:
    """
    entry 後の exit を、休日除外の horizon_bd 営業日ぶんに渡って判定する。

    ルール：
    - TP/SL にヒットした時点で即CLOSED
    - horizon_bd 営業日目の最後の足で未達なら time_stop で強制CLOSED
    - ★未来で horizon 最終営業日のデータが無い → carry（未確定）
    """
    trade_date = v.trade_date
    side = _side(v)

    horizon_dates = _collect_horizon_trade_dates(str(v.code), trade_date, horizon_bd, max_scan_days=60)

    meta: Dict[str, Any] = {
        "horizon_bd": int(horizon_bd),
        "horizon_dates": [str(d) for d in horizon_dates],
    }

    # entry済みでここに来る前提なので、horizon_dates が空は基本あり得ないが保険
    if not horizon_dates:
        # ここを「no_bars_horizon」等で確定エラーにしない（carry/未更新を優先したい）
        return EvalResult(ok=True, reason="carry", eval_exit_reason="carry", meta={**meta, "carry_rule": "no_horizon_dates"})

    # 未来で bars が無いせいで horizon_dates が horizon_bd に満たない場合は carry
    if len(horizon_dates) < int(horizon_bd):
        return EvalResult(
            ok=True,
            reason="carry",
            eval_exit_reason="carry",
            meta={**meta, "carry_rule": "horizon_incomplete_future", "got": len(horizon_dates)},
        )

    last_available_date: Optional[_date] = None
    last_close_px: Optional[float] = None
    last_close_ts: Optional[_dt] = None

    for d in horizon_dates:
        bars = load_5m_bars(v.code, d)
        if bars is None or len(bars) == 0:
            continue

        df = bars.copy()

        if "ts" not in df.columns:
            try:
                import pandas as pd  # type: ignore
                if isinstance(df.index, pd.DatetimeIndex):
                    df = df.reset_index().rename(columns={df.index.name or "index": "ts"})
                else:
                    continue
            except Exception:
                continue

        df2 = _ensure_ts_jst(df)
        if df2 is None:
            continue
        df = df2

        open_col, low_col, high_col, close_col = _find_ohlc_columns(df)
        if low_col is None or high_col is None or close_col is None:
            continue

        session_start, session_end = _jst_session_range(d)

        if d == trade_date:
            start_ts = entry_ts
            if start_ts < session_start:
                start_ts = session_start
        else:
            start_ts = session_start

        df_eff = df[(df["ts"] >= start_ts) & (df["ts"] <= session_end)]
        if df_eff is None or len(df_eff) == 0:
            last_available_date = d
            continue

        last_available_date = d

        last_row = df_eff.iloc[-1]
        try:
            last_close_px = _safe_float(last_row[close_col])
            last_close_ts = _coerce_ts(last_row["ts"], fallback=session_end)
        except Exception:
            last_close_px = None
            last_close_ts = session_end

        hit_tp_idx = None
        hit_sl_idx = None

        if tp_use is not None:
            try:
                tp_mask = df_eff[high_col] >= float(tp_use)
                if tp_mask.to_numpy().any():
                    hit_tp_idx = df_eff[tp_mask].index[0]
            except Exception:
                hit_tp_idx = None

        if sl_use is not None:
            try:
                sl_mask = df_eff[low_col] <= float(sl_use)
                if sl_mask.to_numpy().any():
                    hit_sl_idx = df_eff[sl_mask].index[0]
            except Exception:
                hit_sl_idx = None

        if hit_tp_idx is not None or hit_sl_idx is not None:
            if hit_tp_idx is not None and hit_sl_idx is not None:
                tp_first = hit_tp_idx <= hit_sl_idx
            else:
                tp_first = hit_tp_idx is not None

            if tp_first:
                row2 = df_eff.loc[hit_tp_idx]
                exit_ts = _coerce_ts(row2["ts"], fallback=start_ts)
                exit_px = float(tp_use)
                exit_reason = "hit_tp"
            else:
                row2 = df_eff.loc[hit_sl_idx]
                exit_ts = _coerce_ts(row2["ts"], fallback=start_ts)
                exit_px = float(sl_use)
                exit_reason = "hit_sl"

            plps = _pl_per_share(side, float(exec_entry_px), float(exit_px))
            return EvalResult(
                ok=True,
                reason="exit_ok",
                eval_exit_px=float(exit_px),
                eval_exit_ts=exit_ts,
                eval_exit_reason=exit_reason,
                pl_per_share=plps,
                meta=meta,
            )

    last_horizon_date = horizon_dates[-1]
    if last_available_date != last_horizon_date:
        return EvalResult(
            ok=True,
            reason="carry",
            eval_exit_reason="carry",
            pl_per_share=None,
            meta={**meta, "carry_rule": "last_horizon_not_available", "last_available_date": str(last_available_date) if last_available_date else None},
        )

    if last_close_px is None or last_close_ts is None:
        return EvalResult(
            ok=False,
            reason="no_close_for_time_stop",
            eval_exit_reason="no_close_for_time_stop",
            meta=meta,
        )

    plps = _pl_per_share(side, float(exec_entry_px), float(last_close_px))
    return EvalResult(
        ok=True,
        reason="time_stop",
        eval_exit_px=float(last_close_px),
        eval_exit_ts=last_close_ts,
        eval_exit_reason="time_stop",
        pl_per_share=plps,
        meta=meta,
    )


def _evaluate_one(
    v: VirtualTrade,
    *,
    horizon_bd: int,
    force: bool = False,
    verbose: int = 1,
) -> EvalResult:
    """
    trade_date 起票の紙トレを評価する（プロ仕様）
    - entry：起票日当日のみ
    - exit：休日除外の horizon_bd 営業日
    - carry：未確定なら carry のまま残す（翌日以降も評価対象）
    - --force：既にCLOSEDでも再評価して上書きできる
    """
    current_reason = str(v.eval_exit_reason or "").strip()

    if (not force) and (current_reason not in ("", "carry")):
        return EvalResult(
            ok=True,
            reason="already_closed",
            eval_exit_reason=current_reason,
            meta={"guard": "already_closed", "force": False},
        )

    ok, reason, exec_entry_px, entry_ts, tp_use, sl_use, r_plan, meta_entry = _evaluate_entry_on_trade_date(v, verbose=verbose)

    if not ok:
        # 市場前の「まだデータ来てないだけ」は、ここでは reason=no_bars で返る
        # → handle側で “DB更新しないスキップ” に落とす（合意）
        return EvalResult(ok=False, reason=reason, eval_exit_reason=reason, meta=meta_entry)

    if reason == "no_position":
        return EvalResult(
            ok=True,
            reason="no_position",
            eval_entry_px=None,
            eval_entry_ts=None,
            eval_exit_px=None,
            eval_exit_ts=None,
            eval_exit_reason="no_position",
            pl_per_share=0.0,
            meta=meta_entry,
        )

    if exec_entry_px is None or entry_ts is None:
        return EvalResult(ok=False, reason="bad_entry_state", eval_exit_reason="bad_entry_state", meta=meta_entry)

    res_exit = _evaluate_exit_across_horizon(
        v,
        exec_entry_px=float(exec_entry_px),
        entry_ts=entry_ts,
        tp_use=tp_use,
        sl_use=sl_use,
        horizon_bd=horizon_bd,
        verbose=verbose,
    )

    meta: Dict[str, Any] = {}
    meta.update(meta_entry or {})
    if isinstance(res_exit.meta, dict):
        meta["exit"] = res_exit.meta

    return EvalResult(
        ok=bool(res_exit.ok),
        reason=str(res_exit.reason),
        eval_entry_px=float(exec_entry_px),
        eval_entry_ts=entry_ts,
        eval_exit_px=res_exit.eval_exit_px,
        eval_exit_ts=res_exit.eval_exit_ts,
        eval_exit_reason=str(res_exit.eval_exit_reason or res_exit.reason),
        pl_per_share=res_exit.pl_per_share,
        meta=meta,
    )


# ==============================
# EV / rank
# ==============================

def _ev_true_from_behavior(code: str) -> float:
    """
    A案（全期間）：
    - BehaviorStats の all/all を代表として EV_true_pro に使う
    - win_rate を 0〜1 に正規化して “期待値っぽい指標” として扱う
    """
    row = (
        BehaviorStats.objects
        .filter(code=str(code), mode_period="all", mode_aggr="all")
        .values("win_rate")
        .first()
    )
    if not row:
        return 0.0
    wr = _safe_float(row.get("win_rate"))
    if wr is None:
        return 0.0
    v = max(0.0, min(1.0, wr / 100.0))
    return float(v)


def _rank_within_run(run_id: str) -> int:
    """
    1 run_id 内で ev_true_pro 降順に rank_pro を振る
    """
    qs = (
        VirtualTrade.objects
        .filter(run_id=run_id)
        .order_by("-ev_true_pro", "code", "id")
        .only("id", "ev_true_pro")
    )

    updated = 0
    with transaction.atomic():
        i = 0
        for v in qs:
            i += 1
            if v.rank_pro != i:
                VirtualTrade.objects.filter(id=v.id).update(rank_pro=i)
                updated += 1
    return updated


# ==============================
# management command
# ==============================

class Command(BaseCommand):
    help = "AI紙シミュ評価（5分足）→ VirtualTrade に eval_* / R / EV_true_pro / Rank を反映（プロ仕様）"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=10, help="何日前まで評価対象に含めるか（trade_date基準）")
        parser.add_argument("--horizon", type=int, default=3, help="評価期間（休日除外の営業日数）")
        parser.add_argument("--limit", type=int, default=0, help="0なら全件。>0なら最大件数（新しい opened_at 優先）")
        parser.add_argument("--force", action="store_true", help="すでに評価済みでも再評価して上書きする（再現性注意）")
        parser.add_argument("--dry-run", action="store_true", help="DB更新せずログだけ")

    def handle(self, *args, **options):
        verbose = int(options.get("verbosity", 1) or 1)

        days_opt = options.get("days", None)
        days = 10 if days_opt is None else int(days_opt)

        horizon_opt = options.get("horizon", None)
        horizon = 3 if horizon_opt is None else int(horizon_opt)
        if horizon <= 0:
            horizon = 1

        limit_opt = options.get("limit", None)
        limit = 0 if limit_opt is None else int(limit_opt)

        force = bool(options.get("force"))
        dry_run = bool(options.get("dry_run"))

        now_local = timezone.localtime()
        today = now_local.date()  # JST

        # 朝（市場開始前）に回すと「今日の5分足が無い」ので無駄撃ちになる
        # → 15:40未満なら date_max を昨日に寄せる（運用最適化）
        if now_local.time() < _time(15, 40):
            date_max = today - _timedelta(days=1)
        else:
            date_max = today

        if days <= 0:
            date_min = date_max
        else:
            date_min = date_max - _timedelta(days=days)

        qs = VirtualTrade.objects.filter(trade_date__gte=date_min, trade_date__lte=date_max)

        if not force:
            qs = qs.filter(eval_exit_reason__in=["", "carry"])

        qs = qs.order_by("-opened_at", "-id")
        if limit and limit > 0:
            qs = qs[:limit]

        targets = list(qs)

        self.stdout.write(
            f"[ai_sim_eval] start days={days} horizon={horizon} date_min={date_min} date_max={date_max} "
            f"targets={len(targets)} force={force} dry_run={dry_run}"
        )

        updated = 0
        skipped = 0
        touched_run_ids: set[str] = set()

        for v in targets:
            try:
                res = _evaluate_one(v, horizon_bd=horizon, force=force, verbose=verbose)

                # ==========================
                # 「まだデータ来てないだけ」系は DB を更新しない（合意）
                # ==========================
                # - no_bars は “その日の bars が 0” で起こる
                # - 市場前や直後などで一時的に発生することがあるので、trade_date が今日以降なら未更新でスキップ
                if res.reason == "no_bars":
                    # date_max を昨日に寄せる設計だが、手動実行/テストで今日が混ざっても破壊しない
                    if v.trade_date >= today:
                        skipped += 1
                        if verbose >= 2:
                            self.stdout.write(
                                f"  soft-skip(no_bars_not_ready) id={v.id} code={v.code} trade_date={v.trade_date}"
                            )
                        continue

                # ==========================
                # ハード失敗系（ここは DB に刻む）
                # ==========================
                hard_fail_reasons = {
                    # "no_bars" は上の soft-skip で吸収したいので hard には入れない
                    "no_bars_after_active",
                    "no_ts",
                    "no_ohlc",
                    "bad_ts",
                    "no_opened_at",
                    "no_entry",
                    "bad_entry_state",
                    "no_close_for_time_stop",
                }

                if (not res.ok) and (res.reason in hard_fail_reasons):
                    skipped += 1
                    if verbose >= 2:
                        self.stdout.write(
                            f"  skip id={v.id} code={v.code} trade_date={v.trade_date} reason={res.reason}"
                        )
                    if not dry_run:
                        VirtualTrade.objects.filter(id=v.id).update(
                            eval_exit_reason=res.reason,
                        )
                        updated += 1
                        touched_run_ids.add(v.run_id)
                    continue

                if res.reason == "already_closed" and (not force):
                    if verbose >= 2:
                        self.stdout.write(f"  keep id={v.id} code={v.code} trade_date={v.trade_date} already_closed")
                    continue

                pl_per_share = _safe_float(res.pl_per_share) if res.pl_per_share is not None else None
                pl_per_share = float(pl_per_share) if pl_per_share is not None else 0.0

                qty_r = int(v.qty_rakuten or 0)
                qty_s = int(v.qty_sbi or 0)
                qty_m = int(v.qty_matsui or 0)

                eval_pl_r = pl_per_share * float(qty_r)
                eval_pl_s = pl_per_share * float(qty_s)
                eval_pl_m = pl_per_share * float(qty_m)

                lab_r = _label(qty_r, pl_per_share)
                lab_s = _label(qty_s, pl_per_share)
                lab_m = _label(qty_m, pl_per_share)

                exit_reason = str(res.eval_exit_reason or "").strip()

                # 旧バージョン由来の no_bars_horizon が来ても carry に寄せる（保険）
                if exit_reason == "no_bars_horizon":
                    exit_reason = "carry"

                if exit_reason in ("hit_tp", "hit_sl", "time_stop", "no_position"):
                    closed_at = res.eval_exit_ts if res.eval_exit_ts is not None else timezone.now()
                else:
                    closed_at = None

                ev_true_pro = _ev_true_from_behavior(v.code)

                replay = v.replay if isinstance(v.replay, dict) else {}
                replay["last_eval"] = {
                    "trade_date": str(v.trade_date),
                    "opened_at": str(_to_local(v.opened_at) or v.opened_at),
                    "active_start_rule": "opened_at_local",
                    "result": str(res.reason),
                    "entry_px": res.eval_entry_px,
                    "entry_ts": str(res.eval_entry_ts) if res.eval_entry_ts else None,
                    "exit_px": res.eval_exit_px,
                    "exit_ts": str(res.eval_exit_ts) if res.eval_exit_ts else None,
                    "exit_reason": exit_reason,
                    "pl_per_share": pl_per_share if exit_reason not in ("carry", "") else None,
                    "horizon_bd": int(horizon),
                    "force": bool(force),
                }
                if isinstance(res.meta, dict):
                    replay["last_eval"]["meta"] = res.meta

                pro = replay.get("pro")
                if isinstance(pro, dict):
                    pro["ev_true_pro"] = ev_true_pro
                    replay["pro"] = pro

                if not dry_run:
                    if exit_reason == "no_position":
                        v.eval_entry_px = None
                        v.eval_entry_ts = None
                    else:
                        v.eval_entry_px = res.eval_entry_px
                        v.eval_entry_ts = res.eval_entry_ts

                    if exit_reason == "carry":
                        v.eval_exit_px = None
                        v.eval_exit_ts = None
                        v.eval_pl_rakuten = None
                        v.eval_pl_sbi = None
                        v.eval_pl_matsui = None
                        v.eval_label_rakuten = ""
                        v.eval_label_sbi = ""
                        v.eval_label_matsui = ""
                    else:
                        v.eval_exit_px = res.eval_exit_px
                        v.eval_exit_ts = res.eval_exit_ts
                        v.eval_pl_rakuten = eval_pl_r
                        v.eval_pl_sbi = eval_pl_s
                        v.eval_pl_matsui = eval_pl_m
                        v.eval_label_rakuten = lab_r
                        v.eval_label_sbi = lab_s
                        v.eval_label_matsui = lab_m

                    v.eval_exit_reason = exit_reason
                    v.closed_at = closed_at
                    v.ev_true_pro = ev_true_pro
                    v.replay = replay

                    v.recompute_r()

                    v.save(update_fields=[
                        "eval_entry_px", "eval_entry_ts",
                        "eval_exit_px", "eval_exit_ts",
                        "eval_exit_reason",
                        "eval_label_rakuten", "eval_label_sbi", "eval_label_matsui",
                        "eval_pl_rakuten", "eval_pl_sbi", "eval_pl_matsui",
                        "result_r_rakuten", "result_r_sbi", "result_r_matsui",
                        "closed_at",
                        "ev_true_pro",
                        "replay",
                    ])

                    updated += 1
                    touched_run_ids.add(v.run_id)

                else:
                    if verbose >= 2:
                        self.stdout.write(
                            f"  dry id={v.id} code={v.code} trade_date={v.trade_date} "
                            f"exit_reason={exit_reason} entry={res.eval_entry_px} exit={res.eval_exit_px}"
                        )

            except Exception as e:
                skipped += 1
                if verbose >= 2:
                    self.stdout.write(
                        f"  skip id={v.id} code={v.code} trade_date={v.trade_date} reason=exception({type(e).__name__}) {e}"
                    )
                if not dry_run:
                    VirtualTrade.objects.filter(id=v.id).update(
                        eval_exit_reason="exception",
                    )
                    updated += 1
                    touched_run_ids.add(v.run_id)
                continue

        ranked_rows = 0
        if not dry_run:
            for rid in sorted(touched_run_ids):
                ranked_rows += _rank_within_run(rid)

        self.stdout.write(
            f"[ai_sim_eval] done updated={updated} skipped={skipped} touched_run_ids={len(touched_run_ids)} "
            f"ranked_rows={ranked_rows} dry_run={dry_run}"
        )