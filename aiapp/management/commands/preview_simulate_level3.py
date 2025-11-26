# -*- coding: utf-8 -*-
"""
preview_simulate_level3

レベル3ロジックのデバッグ用プレビューコマンド。

- 入力: /media/aiapp/simulate/sim_orders_YYYY-MM-DD.jsonl （source=ai_simulate_auto）
- 各レコードごとに:
    - trade_date（なければ run_date → ts 日付）をトレード日として決定
    - その銘柄の 5分足を trade_date 1日分だけ取得
    - 指値 entry が その日の 5分足で一度でも触れたか？
        → 触れなければ no_position
    - 触れた場合:
        → そのバー以降で TP / SL のどちらが先にヒットしたかを判定
        → どちらも当日中に触れなければ「当日引け決済（flat）」扱い

使い方例:
  python manage.py preview_simulate_level3 --user 1 --code 7508 --limit 5
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as _date, datetime as _dt, time as _time, timedelta as _td
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


SIM_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"


# ========= 日時ユーティリティ =========

def _now_jst() -> _dt:
    JST = timezone.get_fixed_timezone(9 * 60)
    return timezone.now().astimezone(JST)


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


def _resolve_trade_date(rec: Dict[str, Any]) -> _date:
    """
    トレード日を決める優先順位:
      1) trade_date（ai_simulate_auto が書き込むフィールド）
      2) run_date
      3) ts の日付（JSTで解釈）
      4) どうしてもなければ「今日」
    """
    td = rec.get("trade_date")
    if isinstance(td, str) and td:
        try:
            return _date.fromisoformat(td)
        except Exception:
            pass

    rd = rec.get("run_date")
    if isinstance(rd, str) and rd:
        try:
            return _date.fromisoformat(rd)
        except Exception:
            pass

    ts = rec.get("ts")
    dt = _parse_ts(ts)
    if dt is not None:
        return dt.date()

    return _now_jst().date()


def _to_yf_symbol(code: str) -> str:
    """
    JPX銘柄コード → Yahoo Finance シンボル
    例: "7203" -> "7203.T"
    """
    code = str(code).strip()
    if not code:
        return code
    # ETF/ETN も含め、とりあえず ".T" を付与
    if not code.endswith(".T"):
        code = code + ".T"
    return code


# ========= 5分足取得（trade_date 1日分だけ） =========

def _load_5m_bars(code: str, trade_date: _date) -> pd.DataFrame:
    """
    指定銘柄・指定日の 5分足を 1日ぶん取得して返す。
    - 取得範囲: trade_date 00:00〜翌日00:00（JST換算でほぼその日だけ）
    - 返り値: index=DatetimeIndex, columns=["Open","High","Low","Close","Volume"]
    """
    sym = _to_yf_symbol(code)
    # yfinanceはUTCベースだが、現状は「その日1日分が入っていればOK」という前提でシンプルに取得
    start = _dt.combine(trade_date, _time(0, 0))
    end = start + _td(days=1)

    df = yf.download(
        sym,
        start=start,
        end=end,
        interval="5m",
        auto_adjust=False,
        progress=False,
    )

    if df is None or df.empty:
        return pd.DataFrame()

    # カラム名を揃えておく
    df = df.rename(
        columns={
            "Open": "Open",
            "High": "High",
            "Low": "Low",
            "Close": "Close",
            "Adj Close": "AdjClose",
            "Volume": "Volume",
        }
    )

    # index をローカルタイムにそろえる（ざっくりJST換算）
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df = df.tz_convert("Asia/Tokyo")
    elif isinstance(df.index, pd.DatetimeIndex) and df.index.tz is None:
        df = df.tz_localize("Asia/Tokyo")

    # その日のセッションっぽい時間帯だけをざっくり残す（9:00〜15:10）
    start_sess = _dt.combine(trade_date, _time(9, 0)).astimezone(timezone.get_default_timezone())
    end_sess = _dt.combine(trade_date, _time(15, 10)).astimezone(timezone.get_default_timezone())

    mask = (df.index >= start_sess) & (df.index <= end_sess)
    df = df.loc[mask].copy()

    return df


# ========= シミュレレコード =========

@dataclass
class SimRecord:
    raw: Dict[str, Any]
    trade_date: _date


def _iter_sim_records(user_id: Optional[int], code_filter: Optional[str], limit: int) -> List[SimRecord]:
    """
    /media/aiapp/simulate/sim_orders_YYYY-MM-DD.jsonl を新しい日付順に読んで、
    条件に合うレコードを最大 limit 件まで返す。
    - source が "ai_simulate_auto" のものだけを見る。
    """
    recs: List[SimRecord] = []

    files = sorted(
        SIM_DIR.glob("sim_orders_*.jsonl"),
        key=lambda p: p.name,
        reverse=True,
    )

    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue

            if rec.get("source") != "ai_simulate_auto":
                continue

            if user_id is not None and rec.get("user_id") != user_id:
                continue

            if code_filter:
                if str(rec.get("code") or "") != str(code_filter):
                    continue

            td = _resolve_trade_date(rec)
            recs.append(SimRecord(raw=rec, trade_date=td))

            if len(recs) >= limit:
                return recs

    return recs


# ========= レベル3判定ロジック（1レコード分） =========

@dataclass
class Level3Result:
    label_rakuten: str
    label_matsui: str
    touched: bool
    entry_px: Optional[float]
    exit_px: Optional[float]
    exit_reason: str


def _judge_level3_one(rec: SimRecord, bars: pd.DataFrame) -> Level3Result:
    """
    1件のシミュレ注文について、5分足の中での
    - 指値タッチの有無
    - TP/SL どちらが先か
    を判定する。

    現時点では BUY のみ想定:
      - 指値: entry（買い指値）
      - TP  : tp   （利確指値）
      - SL  : sl   （損切り指値）
    """
    r = rec.raw

    entry = r.get("entry")
    tp = r.get("tp")
    sl = r.get("sl")

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

    # データが無い場合は判定不可
    if bars is None or bars.empty or entry_f is None:
        return Level3Result(
            label_rakuten="none",
            label_matsui="none",
            touched=False,
            entry_px=None,
            exit_px=None,
            exit_reason="bars_not_available_or_no_entry",
        )

    # BUY前提の判定: 「Low <= entry <= High」でそのバー内で指値をタッチしたとみなす
    hit_idx: Optional[pd.Timestamp] = None
    for idx, row in bars.iterrows():
        low = float(row["Low"])
        high = float(row["High"])
        if low <= entry_f <= high:
            hit_idx = idx
            break

    if hit_idx is None:
        # 一度も指値に触れていない → no_position
        return Level3Result(
            label_rakuten="no_position",
            label_matsui="no_position",
            touched=False,
            entry_px=entry_f,
            exit_px=None,
            exit_reason="no_touch",
        )

    # 指値に触れたあとの TP/SL 判定
    after = bars.loc[bars.index >= hit_idx].copy()
    exit_px: Optional[float] = None
    exit_reason = "flat"

    for idx, row in after.iterrows():
        low = float(row["Low"])
        high = float(row["High"])
        close = float(row["Close"])

        # まず SL（損切り）を優先判定
        if sl_f is not None and low <= sl_f <= high:
            exit_px = sl_f
            exit_reason = "hit_sl"
            label = "lose"
            break

        # 次に TP（利確）
        if tp_f is not None and low <= tp_f <= high:
            exit_px = tp_f
            exit_reason = "hit_tp"
            label = "win"
            break
    else:
        # 当日中に TP/SL にかからなかった → 引け成りでクローズ（flat）
        last_close = float(after["Close"].iloc[-1])
        exit_px = last_close
        exit_reason = "close_at_eod"
        # 勝ち負けはここでは付けず、flat扱い
        label = "flat"

    # 量はここでは見ず、「ラベルだけ」両サイド共通扱い
    return Level3Result(
        label_rakuten=label,
        label_matsui=label,
        touched=True,
        entry_px=entry_f,
        exit_px=exit_px,
        exit_reason=exit_reason,
    )


# ========= コマンド本体 =========

class Command(BaseCommand):
    help = "レベル3ロジックで ai_simulate_auto のシミュレをプレビューする（5分足ベース）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            type=int,
            default=None,
            help="対象ユーザーID（省略時は全ユーザー）",
        )
        parser.add_argument(
            "--code",
            type=str,
            default=None,
            help="銘柄コードで絞り込み（例: 7508）",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="最大件数（新しい順）",
        )

    def handle(self, *args, **options):
        user_id: Optional[int] = options.get("user")
        code_filter: Optional[str] = options.get("code")
        limit: int = int(options.get("limit") or 20)

        self.stdout.write(
            f"[preview_simulate_level3] MEDIA_ROOT={settings.MEDIA_ROOT} "
            f"user={user_id} limit={limit}"
        )

        records = _iter_sim_records(user_id=user_id, code_filter=code_filter, limit=limit)
        self.stdout.write(
            f"  対象レコード数: {len(records)} 件（limit={limit}, code={code_filter}）"
        )

        if not records:
            self.stdout.write("  ※ 対象レコードがありません。")
            return

        for idx, rec in enumerate(records, start=1):
            r = rec.raw
            code = r.get("code")
            name = r.get("name")
            mode = r.get("mode")
            ts = r.get("ts")
            trade_date = rec.trade_date

            self.stdout.write(
                "===== #{idx} {code} {name}  ts={ts} mode={mode} trade_date={td} =====".format(
                    idx=idx,
                    code=code,
                    name=name,
                    ts=ts,
                    mode=mode,
                    td=trade_date.isoformat(),
                )
            )

            # 5分足取得
            bars = _load_5m_bars(code=str(code), trade_date=trade_date)
            n_bars = 0 if bars is None else len(bars)
            self.stdout.write(f"  5分足取得: {n_bars} 本")

            if bars is None or bars.empty:
                self.stdout.write("  ※ 5分足が取得できなかったため、両サイドとも判定不可")
                continue

            # レベル3判定
            result = _judge_level3_one(rec, bars)

            # サマリ表示
            if not result.touched:
                self.stdout.write(
                    f"  → 指値 {result.entry_px:.2f} 円 はこの日の5分足で一度もタッチせず → no_position 扱い"
                )
                continue

            self.stdout.write(
                f"  → エントリー {result.entry_px:.2f} 円 → "
                f"exit {result.exit_px:.2f} 円 ({result.exit_reason})"
            )
            self.stdout.write(
                f"    label_rakuten={result.label_rakuten} / "
                f"label_matsui={result.label_matsui}"
            )

        self.stdout.write("[preview_simulate_level3] 完了")