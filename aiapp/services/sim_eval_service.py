# aiapp/services/sim_eval_service.py
# -*- coding: utf-8 -*-
"""
sim_eval_service

AIシミュレ（紙トレ）1件ぶんの結果評価ロジック（Level 3 / 5分足版）。

前提:
- 対象レコードは /media/aiapp/simulate/*.jsonl の1行(dict)。
- 以下のキーが最低限入っている想定:
    code: str          ... 証券コード（例: "7508"）
    entry: float       ... エントリー指値（円）。無い場合は last_close を代入して評価。
    tp: float | None   ... 利確指値（円）
    sl: float | None   ... 損切指値（円）
    side: "BUY" | "SELL" ... 現状はほぼ "BUY" 前提
    ts: str            ... シミュレ登録時刻 (ISO 文字列, JST 推奨)
    trade_date: str | None ... 「この注文が有効になる日」。なければ ts から推定。

出力:
- eval_label_rakuten: "win" / "lose" / "flat" / "no_position"
- eval_pl_rakuten: float | None
- eval_label_matsui: 同上
- eval_pl_matsui: float | None
- eval_close_px: float | None
- eval_close_date: "YYYY-MM-DD" | None
- eval_horizon_days: int (引数で指定された値)

評価アルゴリズム (Level 3 簡約版):
1. trade_date を決定
   - rec["trade_date"] があればそれを使用
   - 無ければ ts を見て:
       - ts の時刻 < 15:00 → 当日を trade_date
       - ts の時刻 >= 15:00 → 翌営業日（土日スキップ）を trade_date

2. trade_date の 5分足を yfinance から取得（[trade_date, trade_date+1日)）
   - 1本も取れなければ「データ無し」とみなして no_position 評価（PL=0）にする

3. 5分足で「指値が一度でもタッチしたか？」を判定
   - side="BUY" の場合: low <= entry <= high を満たす最初のバーを探す
   - 見つからなければ no_position（エントリ不成立 / PL=0）

4. エントリ成立後の TP / SL 判定
   - エントリーが成立したバー以降の 5分足で:
       - まず sl をチェック（low <= sl <= high）→ 当たれば即 exit=sl / hit_sl
       - その次に tp をチェック（low <= tp <= high）→ 当たれば exit=tp / hit_tp
       ※ 同じバーで TP/SL 両方タッチした場合、SL優先（保守的）

5. TP / SL どちらも当たらなかった場合
   - 当日の最後の 5分足 Close で決済 (horizon_close)

6. PL / ラベル付け
   - BUY の場合: PL = (exit_px - entry) * qty
   - SELL の場合: PL = (entry - exit_px) * qty
   - qty <= 0 → "no_position" / PL=0
   - qty > 0:
       - PL > 0 → "win"
       - PL < 0 → "lose"
       - PL = 0 → "flat"

注意:
- ここでは「同じ exit_px / exit_ts」を楽天・松井で共通利用する。
- 将来的にブローカーごとに別 TP/SL を持つなら拡張可能。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, datetime as _datetime, time as _time, timedelta
from typing import Any, Dict, Optional, Tuple

import yfinance as yf


# =========================================================
# 日付ユーティリティ
# =========================================================

def _to_date(s: str) -> Optional[_date]:
    if not s:
        return None
    try:
        return _date.fromisoformat(s)
    except Exception:
        return None


def _parse_ts(ts: Optional[str]) -> Optional[_datetime]:
    if not ts or not isinstance(ts, str):
        return None
    try:
        # fromisoformat は tz 情報付きでもOK
        dt = _datetime.fromisoformat(ts)
        return dt
    except Exception:
        return None


def _next_business_day(d: _date) -> _date:
    """
    非厳密版の「翌営業日」。
    - 土日をスキップ
    - 祝日は考慮しない（将来必要なら JPX カレンダー連携）
    """
    nd = d + timedelta(days=1)
    while nd.weekday() >= 5:  # 5=土, 6=日
        nd += timedelta(days=1)
    return nd


def _decide_trade_date(rec: Dict[str, Any]) -> _date:
    """
    trade_date を決定する。

    優先度:
      1) rec["trade_date"] があればそれをそのまま採用
      2) 無ければ ts から決める:
         - ts 時刻 < 15:00 → ts.date()
         - ts 時刻 >=15:00 → _next_business_day(ts.date())
      3) それも失敗したら「今日」にフォールバック
    """
    # 1) trade_date 明示指定があればそれを使う
    td_raw = rec.get("trade_date")
    if isinstance(td_raw, str):
        d = _to_date(td_raw)
        if d:
            return d

    # 2) ts から決める
    ts = _parse_ts(rec.get("ts"))
    if ts:
        base = ts.date()
        # 15:00 以降は「翌営業日」扱い
        if ts.timetz() >= _time(15, 0):
            return _next_business_day(base)
        return base

    # 3) フォールバック：今日
    return _datetime.now().date()


# =========================================================
# 5分足取得（yfinance）
# =========================================================

@dataclass
class FiveMinBars:
    """
    5分足データを簡単に扱うためのラッパ。
    rows: List[Tuple[ts(datetime|None), open, high, low, close]]
    """
    rows: Tuple[Tuple[Optional[_datetime], float, float, float, float], ...]


def _load_5m_bars_yf(code: str, trade_date: _date) -> Optional[FiveMinBars]:
    """
    yfinance から 5分足を1営業日分取得する。
    - シンボルは「XXXX.T」として扱う
    - 取得範囲: [trade_date, trade_date+1日)（JST想定だが、細かいTZは今回は気にしない）
    - auto_adjust=False を指定して FutureWarning を回避
    - 一部のケースで df.columns が MultiIndex (("Open","7508.T"),…) になるので、
      その場合は「最初のレベル(Open/High/Low/Close)」を見てマッピングする。
    """
    symbol = f"{code.strip()}.T"
    start_str = trade_date.strftime("%Y-%m-%d")
    end_str = (trade_date + timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        df = yf.download(
            symbol,
            interval="5m",
            start=start_str,
            end=end_str,
            auto_adjust=False,
            progress=False,
        )
    except Exception:
        return None

    if df is None or df.empty:
        return None

    # yfinance の戻りは index=datetime,
    # columns が通常 Index(["Open","High","Low","Close",...])
    # だが、環境によっては MultiIndex([("Open","7508.T"), ...]) になることもある。
    # → カラムオブジェクトそのものを value として保持しつつ、
    #    key には「最初のレベル(Open/High/Low/Close)の小文字」を使う。
    cols_map: Dict[str, Any] = {}
    for c in df.columns:
        if isinstance(c, tuple) and c:
            base = c[0]
        else:
            base = c
        key = str(base).lower()
        cols_map[key] = c

    open_col = cols_map.get("open")
    high_col = cols_map.get("high")
    low_col = cols_map.get("low")
    close_col = cols_map.get("close")

    if not all([open_col, high_col, low_col, close_col]):
        return None

    rows = []
    for idx, row in df.iterrows():
        ts = None
        if isinstance(idx, _datetime):
            ts = idx
        try:
            o = float(row[open_col])
            h = float(row[high_col])
            l = float(row[low_col])
            c = float(row[close_col])
        except Exception:
            continue
        # 価格が NaN などはスキップ
        if any(x != x for x in (o, h, l, c)):  # NaN チェック
            continue
        rows.append((ts, o, h, l, c))

    if not rows:
        return None

    return FiveMinBars(rows=tuple(rows))


# =========================================================
# メイン評価ロジック
# =========================================================

def _as_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _calc_pl(side: str, entry: float, exit_px: float, qty: float) -> float:
    """
    side と entry / exit から PL を計算する。
    """
    if qty <= 0:
        return 0.0
    if side.upper() == "SELL":
        per_share = entry - exit_px
    else:
        # デフォルト BUY
        per_share = exit_px - entry
    return per_share * qty


def eval_sim_record(rec: Dict[str, Any], horizon_days: int = 5) -> Dict[str, Any]:
    """
    シミュレ1件分を Level3 ルールで評価し、eval_* を付けた dict を返す。

    ※ rec 自体を書き換える仕様だが、呼び出し側から見ると
       「eval_* が増えた dict」が返ってくると考えてOK。
    """
    side = (rec.get("side") or "BUY").upper()
    code = (rec.get("code") or "").strip()

    # コード or entry が無い場合は評価不能 → eval_* を None にして返す
    entry = _as_float(rec.get("entry") or rec.get("last_close"))
    if not code or entry is None:
        rec["eval_label_rakuten"] = None
        rec["eval_pl_rakuten"] = None
        rec["eval_label_matsui"] = None
        rec["eval_pl_matsui"] = None
        rec["eval_close_px"] = None
        rec["eval_close_date"] = None
        rec["eval_horizon_days"] = horizon_days
        return rec

    tp = _as_float(rec.get("tp"))
    sl = _as_float(rec.get("sl"))

    qty_r = _as_float(rec.get("qty_rakuten")) or 0.0
    qty_m = _as_float(rec.get("qty_matsui")) or 0.0

    trade_date = _decide_trade_date(rec)

    bars = _load_5m_bars_yf(code, trade_date)
    if bars is None or not bars.rows:
        # 5分足が取れなかった場合:
        # → 「エントリ不成立」とみなして no_position / PL=0 にしておく
        rec["eval_label_rakuten"] = "no_position" if qty_r > 0 else None
        rec["eval_pl_rakuten"] = 0.0 if qty_r > 0 else None
        rec["eval_label_matsui"] = "no_position" if qty_m > 0 else None
        rec["eval_pl_matsui"] = 0.0 if qty_m > 0 else None
        rec["eval_close_px"] = entry
        rec["eval_close_date"] = trade_date.isoformat()
        rec["eval_horizon_days"] = horizon_days
        return rec

    # -----------------------------------------------------
    # 1) 「エントリー指値がタッチしたか？」を判定
    # -----------------------------------------------------
    rows = bars.rows
    entry_index: Optional[int] = None
    entry_ts: Optional[_datetime] = None

    for idx, (ts, _o, h, l, _c) in enumerate(rows):
        if side == "SELL":
            # SELL でも「指値をタッチしたか」は同じ条件でOK
            touched = (l <= entry <= h)
        else:
            touched = (l <= entry <= h)

        if touched:
            entry_index = idx
            entry_ts = ts
            break

    if entry_index is None:
        # 一度もタッチしていない → no_position
        label_r = "no_position" if qty_r > 0 else None
        label_m = "no_position" if qty_m > 0 else None

        rec["eval_label_rakuten"] = label_r
        rec["eval_pl_rakuten"] = 0.0 if label_r is not None else None
        rec["eval_label_matsui"] = label_m
        rec["eval_pl_matsui"] = 0.0 if label_m is not None else None
        rec["eval_close_px"] = entry
        rec["eval_close_date"] = trade_date.isoformat()
        rec["eval_horizon_days"] = horizon_days
        return rec

    # -----------------------------------------------------
    # 2) TP / SL 判定
    #    - エントリー成立バーを含めて走査
    #    - SL を優先、その次に TP
    # -----------------------------------------------------
    exit_px: float = entry
    exit_ts: Optional[_datetime] = entry_ts
    exit_reason: str = "entry_only"

    # エントリー以降のバーだけ見る
    for ts, _o, h, l, c in rows[entry_index:]:
        # SL / TP のタッチ判定
        sl_hit = False
        tp_hit = False

        if sl is not None:
            if l <= sl <= h:
                sl_hit = True

        if tp is not None:
            if l <= tp <= h:
                tp_hit = True

        # BUY / SELL 共通で「損側(SL)優先」で保守的に判定
        if sl_hit:
            exit_px = sl
            exit_ts = ts
            exit_reason = "hit_sl"
            break
        if tp_hit:
            exit_px = tp
            exit_ts = ts
            exit_reason = "hit_tp"
            break

        # どちらもヒットしていなければ継続（c は最後に horizon_close で使う可能性があるが、
        # ここでは for を抜けたあとに rows[-1] を見るので何もしない）

    else:
        # for ループが break せずに終わった → TP/SL に掛からず引け決済
        last_ts, _o2, _h2, _l2, last_c = rows[-1]
        exit_px = last_c
        exit_ts = last_ts
        exit_reason = "horizon_close"

    # -----------------------------------------------------
    # 3) PL & ラベルを計算（楽天 / 松井 共通 exit を使用）
    # -----------------------------------------------------
    pl_r = _calc_pl(side, entry, exit_px, qty_r) if qty_r > 0 else 0.0
    pl_m = _calc_pl(side, entry, exit_px, qty_m) if qty_m > 0 else 0.0

    def label_for(qty: float, pl: float) -> Optional[str]:
        if qty <= 0:
            return None
        if pl > 0:
            return "win"
        if pl < 0:
            return "lose"
        return "flat"

    label_r = label_for(qty_r, pl_r)
    label_m = label_for(qty_m, pl_m)

    rec["eval_label_rakuten"] = label_r
    rec["eval_pl_rakuten"] = pl_r if label_r is not None else None
    rec["eval_label_matsui"] = label_m
    rec["eval_pl_matsui"] = pl_m if label_m is not None else None

    # 共通メタ
    rec["eval_close_px"] = exit_px
    rec["eval_close_date"] = (
        exit_ts.date().isoformat() if isinstance(exit_ts, _datetime) else trade_date.isoformat()
    )
    rec["eval_horizon_days"] = horizon_days

    # 将来的に UI で使うかもしれないので、理由も一応残しておく
    rec["eval_exit_reason"] = exit_reason
    rec["eval_entry_ts"] = entry_ts.isoformat() if isinstance(entry_ts, _datetime) else None
    rec["eval_exit_ts"] = exit_ts.isoformat() if isinstance(exit_ts, _datetime) else None

    return rec