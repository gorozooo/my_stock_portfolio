# aiapp/management/commands/preview_simulate_level3.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as _date, datetime as _dt, time as _time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from aiapp.services.bars_5m import load_5m_bars

SIM_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"


@dataclass
class SimRecord:
    raw: Dict[str, Any]
    ts: Optional[_dt]
    trade_date: Optional[_date]
    code: str
    name: str
    mode: str


def _parse_ts(ts_str: Optional[str]) -> Optional[_dt]:
    if not isinstance(ts_str, str) or not ts_str:
        return None
    try:
        dt = _dt.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


def _parse_date(s: Optional[str]) -> Optional[_date]:
    if not isinstance(s, str) or not s:
        return None
    try:
        return _dt.fromisoformat(s).date()
    except Exception:
        return None


def _detect_trade_date(rec: Dict[str, Any], ts: Optional[_dt]) -> Optional[_date]:
    """
    trade_date を決める優先順位:
      1) rec["trade_date"] があればそれを使う
      2) rec["run_date"] (ai_simulate_auto の日付)
      3) rec["price_date"]
      4) ts.date()（最後の手段）
    """
    for key in ("trade_date", "run_date", "price_date"):
        v = rec.get(key)
        d = _parse_date(v) if isinstance(v, str) else None
        if d is not None:
            return d
    if isinstance(ts, _dt):
        return ts.date()
    return None


def _load_sim_records(user_id: int, code: Optional[str], limit: int) -> List[SimRecord]:
    """
    /media/aiapp/simulate/*.jsonl から対象ユーザー＆銘柄のシミュレレコードを読み込む
    ts 降順にソートして limit 件まで返す
    """
    records: List[SimRecord] = []

    if not SIM_DIR.exists():
        return []

    for path in sorted(SIM_DIR.glob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue

        for line in text.splitlines():
            raw_line = line.strip()
            if not raw_line:
                continue
            try:
                rec = json.loads(raw_line)
            except Exception:
                continue

            if rec.get("user_id") != user_id:
                continue

            if code and str(rec.get("code") or "").strip() != str(code).strip():
                continue

            ts = _parse_ts(rec.get("ts"))
            trade_date = _detect_trade_date(rec, ts)

            records.append(
                SimRecord(
                    raw=rec,
                    ts=ts,
                    trade_date=trade_date,
                    code=str(rec.get("code") or ""),
                    name=str(rec.get("name") or ""),
                    mode=str(rec.get("mode") or "").lower() or "demo",
                )
            )

    # ts 降順（None は一番後ろへ）
    def _sort_key(r: SimRecord):
        return r.ts or _dt.min.replace(tzinfo=timezone.get_default_timezone())

    records.sort(key=_sort_key, reverse=True)
    return records[:limit]


def _jst_session_range(d: _date) -> Tuple[_dt, _dt]:
    """
    その営業日のザラ場時間（仮）：9:00〜15:00 JST
    """
    tz = timezone.get_default_timezone()
    start = timezone.make_aware(_dt.combine(d, _time(9, 0)), tz)
    end = timezone.make_aware(_dt.combine(d, _time(15, 0)), tz)
    return start, end


def _label_for_side_pl(qty: float, pl_per_share: float) -> str:
    """
    ざっくりラベル:
      - 数量0 or None → "no_position"
      - pl_per_share > 0  → "win"
      - pl_per_share < 0  → "lose"
      - それ以外         → "flat"
    """
    if qty is None or qty <= 0:
        return "no_position"
    if pl_per_share > 0:
        return "win"
    if pl_per_share < 0:
        return "lose"
    return "flat"


def _coerce_ts_scalar(val: Any, fallback: _dt) -> _dt:
    """
    row["ts"] から安全に Timestamp を取り出すための小ヘルパー。
    Series だったり Python datetime だったりしても必ず1つの Timestamp に潰す。
    NaT の場合は fallback を返す。
    """
    # Series なら 先頭要素を使う
    if isinstance(val, pd.Series):
        if not val.empty:
            val = val.iloc[0]
        else:
            return fallback

    # すでに Timestamp / datetime の場合
    if isinstance(val, (pd.Timestamp, _dt)):
        ts = pd.Timestamp(val)
    else:
        ts = pd.to_datetime(val, errors="coerce")

    if pd.isna(ts):
        return fallback
    return ts


def _find_ohlc_columns(df: pd.DataFrame) -> Tuple[Optional[Any], Optional[Any], Optional[Any]]:
    """
    df.columns が str でも MultiIndex でも、
    'Low' / 'High' / 'Close' (or 'Adj Close') をうまく拾う。
    戻り値は (low_col, high_col, close_col) で、
    それぞれ df[...] のキーとしてそのまま使える実カラム値。
    """
    low_col = high_col = close_col = None

    for col in df.columns:
        # col がタプル (MultiIndex) の場合は要素を全部文字列化して見る
        if isinstance(col, tuple):
            parts = [str(p).lower() for p in col if p is not None]
        else:
            parts = [str(col).lower()]

        if low_col is None and any(p == "low" for p in parts):
            low_col = col
        if high_col is None and any(p == "high" for p in parts):
            high_col = col
        if close_col is None and any(p in ("close", "adj close") for p in parts):
            close_col = col

    return low_col, high_col, close_col


def _preview_one_record(
    idx: int,
    rec: SimRecord,
    horizon_days: int,
    stdout,
) -> None:
    raw = rec.raw
    code = rec.code
    name = rec.name
    ts = rec.ts
    trade_date = rec.trade_date
    mode = rec.mode

    stdout.write(
        f"===== #{idx} {code} {name}  ts={ts} mode={mode} trade_date={trade_date} ====="
    )

    # trade_date が決まらないとどうしようもない
    if not trade_date:
        stdout.write("  trade_date が特定できないため、判定不可")
        return

    # 5分足読み込み（キャッシュ経由）
    bars = load_5m_bars(code, trade_date)
    n_bars = len(bars)
    stdout.write(f"  5分足取得: {n_bars} 本")

    if n_bars == 0:
        stdout.write("  ※ 5分足が取得できなかったため、両サイドとも判定不可")
        return

    # ザラ場時間と、実際にオーダーが有効になる時間
    session_start, session_end = _jst_session_range(trade_date)
    if ts and ts > session_start:
        active_start = ts
    else:
        active_start = session_start

    # DataFrame 正規化
    df = bars.copy()

    # ts カラムが無ければ index から復元
    if "ts" not in df.columns:
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index().rename(columns={df.index.name or "index": "ts"})
        else:
            stdout.write("  ※ ts カラムが無いため、判定不可")
            return

    # ts を datetime に強制（tz情報付きでもOK）
    df["ts"] = pd.to_datetime(df["ts"])

    # OHLC カラムを特定（MultiIndex 対応）
    low_col, high_col, close_col = _find_ohlc_columns(df)
    if low_col is None or high_col is None or close_col is None:
        stdout.write("  ※ Low/High/Close カラムを検出できなかったため、判定不可")
        return

    # 有効バーに絞る（エントリー可能時間帯）
    df = df[(df["ts"] >= active_start) & (df["ts"] <= session_end)]
    n_eff = len(df)
    stdout.write(f"  有効判定バー数: {n_eff} 本")

    if n_eff == 0:
        stdout.write("  ※ ts 以降の5分足が無いため、判定不可（場外で登録された等）")
        return

    # 必要なパラメータ
    entry = raw.get("entry")
    tp = raw.get("tp")
    sl = raw.get("sl")

    try:
        entry_f = float(entry) if entry is not None else None
    except Exception:
        entry_f = None

    try:
        tp_f = float(tp) if tp is not None else None
    except Exception:
        tp_f = None

    try:
        sl_f = float(sl) if sl is not None else None
    except Exception:
        sl_f = None

    if entry_f is None:
        stdout.write("  ※ entry が無いため、判定不可")
        return

    # -------- エントリー判定（指値） --------
    hit_mask = (df[low_col] <= entry_f) & (df[high_col] >= entry_f)

    if not hit_mask.to_numpy().any():
        stdout.write(
            f"  → 指値 {entry_f:.2f} 円 はこの日の5分足で一度もタッチせず → no_position 扱い"
        )
        return

    hit_df = df[hit_mask]
    if hit_df.empty:
        stdout.write(
            f"  → 指値 {entry_f:.2f} 円 はこの日の5分足で一度もタッチせず（hit_df empty） → no_position 扱い"
        )
        return

    first_hit = hit_df.iloc[0]
    entry_ts = _coerce_ts_scalar(first_hit["ts"], fallback=active_start)
    exec_entry_px = entry_f  # 指値約定として扱う

    # -------- エグジット判定（TP / SL / horizon_close） --------
    #   ・エントリーバー以降のバーを対象
    eval_df = df[df["ts"] >= entry_ts].copy()
    if eval_df.empty:
        stdout.write(
            f"  → エントリー {exec_entry_px:.2f} 円 (ts={entry_ts}) 以降のバー無し → horizon_close=entry 扱い"
        )
        exit_ts = entry_ts
        exit_px = exec_entry_px
        exit_reason = "horizon_close"
    else:
        # TP / SL ヒットの有無をチェック
        hit_tp_idx = None
        hit_sl_idx = None

        if tp_f is not None:
            tp_mask = eval_df[high_col] >= tp_f
            if tp_mask.to_numpy().any():
                hit_tp_idx = eval_df[tp_mask].index[0]

        if sl_f is not None:
            sl_mask = eval_df[low_col] <= sl_f
            if sl_mask.to_numpy().any():
                hit_sl_idx = eval_df[sl_mask].index[0]

        if hit_tp_idx is not None or hit_sl_idx is not None:
            # どちらかヒットしていれば、時間が早い方を優先
            if hit_tp_idx is not None and hit_sl_idx is not None:
                if hit_tp_idx <= hit_sl_idx:
                    row = eval_df.loc[hit_tp_idx]
                    exit_ts = _coerce_ts_scalar(row["ts"], fallback=entry_ts)
                    exit_px = float(tp_f)
                    exit_reason = "hit_tp"
                else:
                    row = eval_df.loc[hit_sl_idx]
                    exit_ts = _coerce_ts_scalar(row["ts"], fallback=entry_ts)
                    exit_px = float(sl_f)
                    exit_reason = "hit_sl"
            elif hit_tp_idx is not None:
                row = eval_df.loc[hit_tp_idx]
                exit_ts = _coerce_ts_scalar(row["ts"], fallback=entry_ts)
                exit_px = float(tp_f)
                exit_reason = "hit_tp"
            else:
                row = eval_df.loc[hit_sl_idx]
                exit_ts = _coerce_ts_scalar(row["ts"], fallback=entry_ts)
                exit_px = float(sl_f)
                exit_reason = "hit_sl"
        else:
            # どちらもタッチしなかった → 終値クローズ
            last_row = eval_df.iloc[-1]
            exit_ts = _coerce_ts_scalar(last_row["ts"], fallback=entry_ts)
            exit_px = float(last_row[close_col])
            exit_reason = "horizon_close"

    pl_per_share = float(exit_px) - float(exec_entry_px)

    qty_r = raw.get("qty_rakuten") or 0
    qty_m = raw.get("qty_matsui") or 0
    try:
        qty_r = float(qty_r or 0)
    except Exception:
        qty_r = 0.0
    try:
        qty_m = float(qty_m or 0)
    except Exception:
        qty_m = 0.0

    label_r = _label_for_side_pl(qty_r, pl_per_share)
    label_m = _label_for_side_pl(qty_m, pl_per_share)

    stdout.write(
        f"  → エントリー {exec_entry_px:.2f} 円 → exit {exit_px:.2f} 円 ({exit_reason}) @ {exit_ts}"
    )
    stdout.write(f"    label_rakuten={label_r} / label_matsui={label_m}")


class Command(BaseCommand):
    help = "AIシミュレ Level3 の挙動を、1ユーザー・1銘柄単位でプレビューするための開発用コマンド。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--user",
            type=int,
            required=True,
            help="対象ユーザーID（必須）",
        )
        parser.add_argument(
            "--code",
            type=str,
            default=None,
            help="銘柄コード（例: 7508。指定なしなら全銘柄から最新順で）",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=5,
            help="プレビューする最大件数（デフォルト5件）",
        )
        parser.add_argument(
            "--horizon-days",
            type=int,
            default=5,
            help="評価期間（日数）。今は同日5分足のみを使うが、将来マルチデイ対応時のために保持。",
        )

    def handle(self, *args, **options) -> None:
        user_id: int = options["user"]
        code: Optional[str] = options.get("code") or None
        limit: int = int(options.get("limit") or 5)
        horizon_days: int = int(options.get("horizon_days") or 5)

        self.stdout.write(
            f"[preview_simulate_level3] MEDIA_ROOT={settings.MEDIA_ROOT} user={user_id} limit={limit}"
        )

        records = _load_sim_records(user_id=user_id, code=code, limit=limit)
        self.stdout.write(
            f"  対象レコード数: {len(records)} 件（limit={limit}, code={code or 'ALL'}）"
        )

        if not records:
            return

        for i, rec in enumerate(records, start=1):
            _preview_one_record(
                idx=i,
                rec=rec,
                horizon_days=horizon_days,
                stdout=self.stdout,
            )

        self.stdout.write("[preview_simulate_level3] 完了")