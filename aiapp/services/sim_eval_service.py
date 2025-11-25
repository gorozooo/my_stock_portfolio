# aiapp/services/sim_eval_service.py
# -*- coding: utf-8 -*-
"""
AIシミュレ評価ロジック（TP/SL/何R・勝敗ラベル計算）

目的
------
- 自動シミュレ（JSONL の 1 レコード）に対して、
  「その後の値動きがどうなったか」を計算して
  eval_ 系のフィールドを埋めるための純粋ロジックをまとめる。
- 管理コマンド ai_sim_eval から呼び出されることを想定。
- ここでは「1銘柄・1シミュレ記録」単位の計算だけを担当し、
  ファイル I/O や JSONL の読み書きは一切行わない。

前提
------
- ロング想定（買い方向）のみをサポート。
- 価格データは aiapp.services.fetch_price.get_prices を通じて取得する。
- DataFrame の index は DatetimeIndex を想定し、日足の High / Low / Close を使用する。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date as _date, datetime as _dt
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
from django.utils import timezone

# 価格取得（picks_build と同じサービスを優先して使う）
try:
    from aiapp.services.fetch_price import get_prices  # type: ignore
except Exception:  # pragma: no cover - フォールバック用
    def get_prices(code: str, nbars: int = 120, period: str = "1y") -> pd.DataFrame:  # type: ignore
        """
        フォールバック版：本番環境で fetch_price が無い場合でも
        例外で落とさないようにするためのダミー実装。
        """
        return pd.DataFrame()


Number = float


@dataclass
class SimEvalResult:
    """
    シミュレ結果評価の出力。

    - eval_label_xxx: "win" / "lose" / "flat"（引き分け） など
    - eval_pl_xxx   : 損益（円）
    - eval_close_px : 評価に使った終値
    - eval_close_date: 終値の日付（"YYYY-MM-DD" の文字列）
    - eval_horizon_days: 何営業日後までを評価対象としたか
    """

    eval_label_rakuten: Optional[str] = None
    eval_pl_rakuten: Optional[Number] = None

    eval_label_matsui: Optional[str] = None
    eval_pl_matsui: Optional[Number] = None

    eval_close_px: Optional[Number] = None
    eval_close_date: Optional[str] = None
    eval_horizon_days: Optional[int] = None


# =========================================================
# 内部ユーティリティ
# =========================================================

def _to_naive_date(d: Any) -> Optional[_date]:
    """
    任意の値から date 型を安全に取り出す。
    - "2025-11-25" / "2025/11/25" / datetime / date などに対応。
    """
    if isinstance(d, _date) and not isinstance(d, _dt):
        return d
    if isinstance(d, _dt):
        return d.date()
    if isinstance(d, str):
        s = d.strip()
        if not s:
            return None
        # 区切りを統一
        s = s.replace("/", "-")
        try:
            parts = s.split("-")
            if len(parts) == 3:
                y = int(parts[0])
                m = int(parts[1])
                day = int(parts[2])
                return _date(y, m, day)
        except Exception:
            return None
    return None


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
        if not np.isfinite(v):
            return None
        return v
    except Exception:
        return None


def _get_entry_date(rec: Dict[str, Any]) -> Optional[_date]:
    """
    シミュレ記録から「評価開始日」を推定する。
    - 優先: price_date（"YYYY-MM-DD" など）
    - 無ければ ts（ISO文字列）の日付部分
    """
    if "price_date" in rec and rec["price_date"]:
        d = _to_naive_date(rec["price_date"])
        if d:
            return d

    ts_str = rec.get("ts")
    if isinstance(ts_str, str) and ts_str:
        try:
            dt = _dt.fromisoformat(ts_str)
        except Exception:
            # タイムゾーン付きで失敗した場合など
            try:
                # 末尾のZやオフセットを雑に削って再トライ
                base = ts_str.split("+")[0].split("Z")[0]
                dt = _dt.fromisoformat(base)
            except Exception:
                dt = None
        if dt is not None:
            return dt.date()

    return None


def _get_price_window(
    code: str,
    entry_date: _date,
    horizon_days: int,
    buffer_days: int = 3,
) -> pd.DataFrame:
    """
    指定銘柄の「エントリー日以降の価格データ」を取得する。

    - horizon_days: 評価したい営業日数（5営業日など）
    - buffer_days : 余裕を持って少し多めに取得する日数（例：+3）

    戻り値:
        - index: DatetimeIndex（日足）
        - columns: ["Open", "High", "Low", "Close", ...] を想定
    """
    # horizon_days + buffer_days 分くらいの日足があれば十分
    nbars = max(10, horizon_days + buffer_days)
    df = get_prices(code, nbars=nbars, period="6mo")
    if df is None or len(df) == 0:
        return pd.DataFrame()

    # index を date に変換してフィルタ
    idx_dates = df.index
    if not isinstance(idx_dates, pd.DatetimeIndex):
        try:
            idx_dates = pd.to_datetime(idx_dates)
            df.index = idx_dates
        except Exception:
            return pd.DataFrame()

    # entry_date 当日以降の行だけに絞る
    mask = idx_dates.date >= entry_date
    df2 = df.loc[mask].copy()
    if df2.empty:
        return pd.DataFrame()

    # horizon_days 分だけ先まで（足数ベースで切る）
    df2 = df2.iloc[:horizon_days]

    return df2


def _eval_path_long(
    entry: float,
    tp: Optional[float],
    sl: Optional[float],
    df: pd.DataFrame,
) -> Tuple[Optional[str], Optional[float], Optional[_date], Optional[float]]:
    """
    ロング想定の TP/SL 判定。

    引数:
        entry: エントリー価格
        tp   : 利確ライン（None の場合は TP なし）
        sl   : 損切りライン（None の場合は SL なし）
        df   : 評価対象の期間の日足 DataFrame
               必須列: "High", "Low", "Close"

    戻り値:
        (label, exit_price, exit_date, eval_close_px)

        label:
          - "win"  : TP に先に到達
          - "lose" : SL に先に到達
          - "flat" : 期間内で TP/SL どちらも付かず、最終日の Close で評価
          - None   : データ不足など評価不能

        exit_price:
          実際に決済したとみなす価格（TP/SL か最終 Close）

        exit_date:
          決済日

        eval_close_px:
          ラベル表示用の「評価に使った終値」
          - TP/SL 決済の場合も、「その日の終値」を入れておくとラベルに使える。
    """
    if df is None or df.empty:
        return None, None, None, None

    if "High" not in df.columns or "Low" not in df.columns or "Close" not in df.columns:
        return None, None, None, None

    # 日ごとに TP/SL 到達判定
    for dt, row in df.iterrows():
        try:
            high = float(row["High"])
            low = float(row["Low"])
            close = float(row["Close"])
        except Exception:
            continue

        # まず TP 判定
        if tp is not None and high >= tp:
            # TP 到達 → 勝ち
            exit_price = tp
            return "win", exit_price, dt.date(), close

        # 次に SL 判定
        if sl is not None and low <= sl:
            # SL 到達 → 負け
            exit_price = sl
            return "lose", exit_price, dt.date(), close

    # 期間内に TP/SL どちらも到達しなかった場合：
    # 最後の終値で評価
    last_dt = df.index[-1]
    last_close = float(df["Close"].iloc[-1])
    # エントリーとの差で勝ち負け判定
    diff = last_close - entry
    if diff > 0:
        label = "win"
    elif diff < 0:
        label = "lose"
    else:
        label = "flat"

    return label, last_close, last_dt.date(), last_close


# =========================================================
# パブリックAPI
# =========================================================

def eval_sim_record(
    rec: Dict[str, Any],
    *,
    horizon_days: int = 5,
) -> Dict[str, Any]:
    """
    シミュレ JSON レコード 1件に対して「勝敗評価 + 損益」を付与する。

    引数:
        rec          : JSONL の 1 行分（dict）
        horizon_days : 何営業日後までの値動きを評価するか（例: 5）

    戻り値:
        - 元の rec をコピーし、以下のキーを追加した dict を返す：
            eval_label_rakuten
            eval_pl_rakuten
            eval_label_matsui
            eval_pl_matsui
            eval_close_px
            eval_close_date
            eval_horizon_days
    """

    # まずは rec をコピーして編集
    out = dict(rec)

    # すでに評価済みの場合は、そのまま上書きしてもOKだが、
    # ここでは毎回計算し直す前提で上書きする。
    out["eval_label_rakuten"] = None
    out["eval_pl_rakuten"] = None
    out["eval_label_matsui"] = None
    out["eval_pl_matsui"] = None
    out["eval_close_px"] = None
    out["eval_close_date"] = None
    out["eval_horizon_days"] = horizon_days

    # 必須情報が無ければ何もせず返す
    code = rec.get("code")
    entry = _safe_float(rec.get("entry"))
    if not code or entry is None:
        return out

    tp = _safe_float(rec.get("tp"))
    sl = _safe_float(rec.get("sl"))

    # エントリー日を決める
    entry_date = _get_entry_date(rec)
    if entry_date is None:
        return out

    # 価格データ取得
    try:
        df = _get_price_window(str(code), entry_date, horizon_days=horizon_days)
    except Exception:
        df = pd.DataFrame()

    if df is None or df.empty:
        # データ取得に失敗した場合は何も付与できない
        return out

    # ロング想定で評価
    label, exit_price, exit_date, eval_close_px = _eval_path_long(
        entry=entry,
        tp=tp,
        sl=sl,
        df=df,
    )

    if label is None or exit_price is None or exit_date is None:
        return out

    # 損益計算（単純に entry→exit の差 × 数量）
    qty_rakuten = rec.get("qty_rakuten")
    qty_matsui = rec.get("qty_matsui")

    pl_rakuten: Optional[float] = None
    pl_matsui: Optional[float] = None

    if isinstance(qty_rakuten, (int, float)) and qty_rakuten:
        pl_rakuten = (exit_price - entry) * float(qty_rakuten)

    if isinstance(qty_matsui, (int, float)) and qty_matsui:
        pl_matsui = (exit_price - entry) * float(qty_matsui)

    # ラベルと損益を反映
    # 数量が 0 / None の場合は PL だけ None のままでもOK
    out["eval_label_rakuten"] = label if pl_rakuten is not None else None
    out["eval_pl_rakuten"] = pl_rakuten

    out["eval_label_matsui"] = label if pl_matsui is not None else None
    out["eval_pl_matsui"] = pl_matsui

    # 終値情報（ラベル脇に表示する用）
    out["eval_close_px"] = eval_close_px
    out["eval_close_date"] = exit_date.strftime("%Y-%m-%d")

    return out