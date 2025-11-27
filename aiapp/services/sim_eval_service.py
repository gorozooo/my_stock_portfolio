# aiapp/services/sim_eval_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import yfinance as yf
from django.utils import timezone


# =========================================================
# ユーティリティ
# =========================================================

def _parse_trade_date(rec: Dict[str, Any]) -> date:
    """
    rec["trade_date"] (または run_date) を date に変換する。
    """
    v = rec.get("trade_date") or rec.get("run_date")
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v:
        return date.fromisoformat(v)
    raise ValueError(f"invalid trade_date: {v!r}")


def _yf_symbol(code: str) -> str:
    code = str(code).strip()
    if not code:
        raise ValueError("code is empty")
    if code.endswith(".T"):
        return code
    return f"{code}.T"


def _pick_price_col(df: pd.DataFrame, name: str) -> pd.Series:
    """
    yfinance の 5分足 DataFrame から open/high/low/close を取り出す。
    MultiIndex 列 (('Open', 'xxx'), ...) にも対応する。
    """
    target = None
    for c in df.columns:
        if isinstance(c, tuple):
            key = str(c[0]).lower()
        else:
            key = str(c).lower()
        if key == name:
            target = c
            break
    if target is None:
        raise ValueError(f"5m bars missing column '{name}'")
    return df[target]


def load_5m_bars(code: str, trade_date: date, horizon_days: int) -> pd.DataFrame:
    """
    指定コードの 5分足を trade_date から horizon_days 営業日ぶん取得。
    （ざっくり日数で取り、実際には利用する範囲だけを見る）

    戻り値: columns = ["ts", "open", "high", "low", "close"]
    ts は Asia/Tokyo の tz-aware datetime。
    """
    symbol = _yf_symbol(code)

    # yfinance の start/end は「そのまま解釈されて UTC 日付で扱われる」ので、
    # ここでは単純に日付範囲だけ与える。
    start_dt = datetime.combine(trade_date, datetime.min.time())
    end_dt = start_dt + timedelta(days=horizon_days + 1)

    df = yf.download(
        symbol,
        interval="5m",
        start=start_dt,
        end=end_dt,
        progress=False,
    )
    if df is None or df.empty:
        raise ValueError(f"no 5m data for {code}")

    open_s = _pick_price_col(df, "open")
    high_s = _pick_price_col(df, "high")
    low_s = _pick_price_col(df, "low")
    close_s = _pick_price_col(df, "close")

    # index は UTC のはずなので東京時間に変換
    idx = df.index
    if idx.tz is None:
        # 念のため。yfinance 側で naive のことはほぼ無いが保険。
        idx = idx.tz_localize("UTC")
    ts_jst = idx.tz_convert("Asia/Tokyo")

    out = pd.DataFrame(
        {
            "ts": ts_jst,
            "open": open_s.astype(float),
            "high": high_s.astype(float),
            "low": low_s.astype(float),
            "close": close_s.astype(float),
        }
    ).reset_index(drop=True)

    return out


def _label_and_pl(
    qty: float,
    side: str,
    entry_px: Optional[float],
    exit_px: Optional[float],
) -> Tuple[str, float]:
    """
    qty / entry_px / exit_px から label(win/lose/flat/no_position) と PL を計算。
    """
    if not qty or entry_px is None or exit_px is None:
        return "no_position", 0.0

    side = (side or "BUY").upper()
    if side == "BUY":
        pl = (exit_px - entry_px) * qty
    else:  # SELL（将来対応用。今は基本 BUY 想定）
        pl = (entry_px - exit_px) * qty

    if pl > 0:
        label = "win"
    elif pl < 0:
        label = "lose"
    else:
        label = "flat"
    return label, float(pl)


def _to_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    # JST で統一
    dt_jst = dt.astimezone(timezone.get_default_timezone())
    return dt_jst.isoformat()


# =========================================================
# メイン：1レコード評価
# =========================================================

def eval_sim_record(rec: Dict[str, Any], horizon_days: int = 5) -> Dict[str, Any]:
    """
    1つのシミュレレコードを評価して、eval_ 系の情報を付与して返す。

    ★重要★
      - rec["entry"] / "tp" / "sl" は「AIが出した指値スナップショット」
        → ここでは **絶対に書き換えない**
      - 実際の約定価格・時間は eval_entry_px / eval_entry_ts に入れる。
    """
    # もとの dict を壊さないようにコピーしてから更新
    out = dict(rec)

    code = str(rec.get("code"))
    side = (rec.get("side") or "BUY").upper()

    # AI が出した指値（スナップショット）
    ai_entry_px = rec.get("entry")
    tp = rec.get("tp")
    sl = rec.get("sl")

    ai_entry_px = float(ai_entry_px) if ai_entry_px is not None else None
    tp = float(tp) if tp is not None else None
    sl = float(sl) if sl is not None else None

    trade_d = _parse_trade_date(rec)

    try:
        df = load_5m_bars(code, trade_d, horizon_days)
    except Exception:
        # データ取得に失敗した場合は eval_horizon_days だけ付与して何もしない。
        out["eval_horizon_days"] = horizon_days
        return out

    if df.empty:
        out["eval_horizon_days"] = horizon_days
        return out

    # ============================================
    # 1) エントリー判定
    # ============================================
    entry_ts: Optional[datetime] = None
    entry_px: Optional[float] = None

    if ai_entry_px is None:
        # 指値自体が無いならそもそもエントリー無し
        entry_ts = None
        entry_px = None
    else:
        # 寄り（その日の最初のバー）
        first = df.iloc[0]
        open_px = float(first["open"])
        open_ts = first["ts"].to_pydatetime()

        # BUY 指値の基本ルール
        #   - 指値 >= 寄り → 寄り成約（オープンで約定）
        #   - それ以外 → 5分足の高値安値レンジで指値を通過したバーを探す
        if side == "BUY":
            if ai_entry_px >= open_px:
                entry_ts = open_ts
                entry_px = open_px
            else:
                hit = df[(df["low"] <= ai_entry_px) & (df["high"] >= ai_entry_px)]
                if not hit.empty:
                    bar = hit.iloc[0]
                    entry_ts = bar["ts"].to_pydatetime()
                    # 指値で約定したとみなす
                    entry_px = ai_entry_px
        else:
            # SELL（将来用。今は使っていないが一応実装）
            if ai_entry_px <= open_px:
                entry_ts = open_ts
                entry_px = open_px
            else:
                hit = df[(df["high"] >= ai_entry_px) & (df["low"] <= ai_entry_px)]
                if not hit.empty:
                    bar = hit.iloc[0]
                    entry_ts = bar["ts"].to_pydatetime()
                    entry_px = ai_entry_px

    # ============================================
    # 2) エグジット判定（TP / SL / タイムアップ）
    # ============================================
    exit_reason = None
    exit_ts: Optional[datetime] = None
    exit_px: Optional[float] = None

    if entry_ts is None or entry_px is None:
        # 1度も指値に触れなかったケース
        # → タイムアップ扱い + no_position
        last_bar = df.iloc[-1]
        exit_ts = last_bar["ts"].to_pydatetime()
        exit_px = float(last_bar["close"])
        exit_reason = "no_fill"
    else:
        # エントリー以降のバーで TP / SL をチェック
        df_after = df[df["ts"] >= entry_ts]

        hit_index: Optional[int] = None
        hit_kind: Optional[str] = None
        hit_px: Optional[float] = None
        hit_ts: Optional[datetime] = None

        if not df_after.empty:
            for i, row in df_after.iterrows():
                high = float(row["high"])
                low = float(row["low"])
                bar_ts = row["ts"].to_pydatetime()

                if side == "BUY":
                    # 先に SL → その後 TP だと「先に当たった方」で決めるので、
                    # TP/SL の優先順序はここで調整可能（今は TP 優先）
                    if tp is not None and high >= tp:
                        hit_index = i
                        hit_kind = "hit_tp"
                        hit_px = tp
                        hit_ts = bar_ts
                        break
                    if sl is not None and low <= sl:
                        hit_index = i
                        hit_kind = "hit_sl"
                        hit_px = sl
                        hit_ts = bar_ts
                        break
                else:
                    # SELL の場合（将来用）
                    if tp is not None and low <= tp:
                        hit_index = i
                        hit_kind = "hit_tp"
                        hit_px = tp
                        hit_ts = bar_ts
                        break
                    if sl is not None and high >= sl:
                        hit_index = i
                        hit_kind = "hit_sl"
                        hit_px = sl
                        hit_ts = bar_ts
                        break

        if hit_index is not None:
            exit_reason = hit_kind
            exit_px = hit_px
            exit_ts = hit_ts
        else:
            # TP / SL どちらも当たらず → horizon_close
            last_bar = df_after.iloc[-1]
            exit_reason = "horizon_close"
            exit_px = float(last_bar["close"])
            exit_ts = last_bar["ts"].to_pydatetime()

    # ============================================
    # 3) PL / ラベル計算
    # ============================================
    qty_r = float(out.get("qty_rakuten") or 0)
    qty_m = float(out.get("qty_matsui") or 0)

    label_r, pl_r = _label_and_pl(qty_r, side, entry_px, exit_px)
    label_m, pl_m = _label_and_pl(qty_m, side, entry_px, exit_px)

    out["eval_label_rakuten"] = label_r
    out["eval_pl_rakuten"] = pl_r
    out["eval_label_matsui"] = label_m
    out["eval_pl_matsui"] = pl_m

    # 共通の情報
    out["eval_close_px"] = exit_px
    out["eval_close_date"] = exit_ts.date().isoformat() if exit_ts else None
    out["eval_horizon_days"] = horizon_days
    out["eval_exit_reason"] = exit_reason
    out["eval_entry_px"] = entry_px
    out["eval_entry_ts"] = _to_iso(entry_ts)
    out["eval_exit_ts"] = _to_iso(exit_ts)

    # UI 用に「楽天／松井をまとめたラベル」を作っておくと便利
    # - 両方 no_position         → "skip"（指値未ヒット）
    # - どちらか win どちらか負け → "mixed"
    # - どちらかでも win         → "win"
    # - 両方負け                  → "lose"
    combined = "unknown"
    labels = {label_r, label_m}
    if labels <= {"no_position"}:
        combined = "skip"
    elif "win" in labels and "lose" in labels:
        combined = "mixed"
    elif "win" in labels:
        combined = "win"
    elif "lose" in labels:
        combined = "lose"
    elif labels <= {"flat"}:
        combined = "flat"

    out["_combined_label"] = combined

    return out