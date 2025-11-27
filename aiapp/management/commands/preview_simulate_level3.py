# aiapp/management/commands/preview_simulate_level3.py
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from aiapp.services import bars_5m


@dataclass
class SimRecord:
    raw: Dict[str, Any]
    ts: datetime
    trade_date: date
    code: str
    name: str
    mode: str
    entry: Optional[float]
    tp: Optional[float]
    sl: Optional[float]


def _parse_ts(ts_str: str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


def _next_business_day(d: date) -> date:
    """
    かなりラフな「翌営業日」判定。
    - 土日を飛ばす
    - 祝日は考慮しない（実運用では別途カレンダーを噛ませる）
    """
    nd = d + timedelta(days=1)
    while nd.weekday() >= 5:  # 5=土, 6=日
        nd += timedelta(days=1)
    return nd


def _resolve_trade_date(rec: Dict[str, Any]) -> (datetime, date):
    """
    ts と trade_date の両方を見て「いつの5分足で判定するか」を決める。
    優先順位:
      1) rec["trade_date"] があればそれを使う
      2) 無ければ ts から決める
         - ts.time >= 15:00 → 翌営業日
         - それ以外 → 当日扱い
    """
    ts_str = str(rec.get("ts") or "")
    ts = _parse_ts(ts_str)
    if ts is None:
        # どうしようもない場合は「今日」
        now = timezone.localtime()
        return now, now.date()

    # すでに trade_date が保存されていればそれを優先
    td_str = rec.get("trade_date")
    if td_str:
        try:
            td = datetime.fromisoformat(td_str).date()
            return ts, td
        except Exception:
            pass

    # ts だけから決める
    t: time = ts.time()
    if t >= time(15, 0):
        td = _next_business_day(ts.date())
    else:
        td = ts.date()

    return ts, td


def _load_sim_records(
    root: Path,
    user_id: int,
    code: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[SimRecord]:
    """
    /media/aiapp/simulate/sim_orders_*.jsonl を読み込んで
    指定ユーザー＆銘柄コードの最新レコードから順に返す。
    """
    sim_dir = root / "aiapp" / "simulate"
    if not sim_dir.exists():
        raise CommandError(f"simulate dir not found: {sim_dir}")

    records: List[SimRecord] = []

    # sim_orders_YYYY-MM-DD.jsonl だけを見る
    paths = sorted(sim_dir.glob("sim_orders_*.jsonl"))
    for path in paths:
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

            if code and str(rec.get("code")) != str(code):
                continue

            ts, trade_date = _resolve_trade_date(rec)
            name = str(rec.get("name") or "")
            mode = str(rec.get("mode") or "demo")
            entry = rec.get("entry")
            tp = rec.get("tp")
            sl = rec.get("sl")

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

            records.append(
                SimRecord(
                    raw=rec,
                    ts=ts,
                    trade_date=trade_date,
                    code=str(rec.get("code") or ""),
                    name=name,
                    mode=mode,
                    entry=entry_f,
                    tp=tp_f,
                    sl=sl_f,
                )
            )

    # ts 降順（新しい順）
    records.sort(key=lambda r: r.ts, reverse=True)

    if limit is not None and limit > 0:
        records = records[:limit]

    return records


def _print_header(idx: int, r: SimRecord, out):
    out.write(
        f"===== #{idx} {r.code} {r.name}  ts={r.ts.isoformat()} mode={r.mode} trade_date={r.trade_date.isoformat()} =====\n"
    )


def _preview_one_record(
    idx: int,
    rec: SimRecord,
    horizon_days: int,
    out,
):
    _print_header(idx, rec, out)

    # 5分足取得（キャッシュサービス経由）
    bars_result = bars_5m.load_5m_bars(rec.code, rec.trade_date, horizon_days=horizon_days)
    df = bars_result.df

    total_bars = 0 if df is None else len(df)
    out.write(f"  5分足取得: {total_bars} 本\n")

    if df is None or df.empty:
        out.write("  ※ 5分足が取得できなかったため、両サイドとも判定不可\n")
        return

    # ts 以降のバーを有効とするか、trade_date全体を有効とするか
    # ルール:
    #  - trade_date が ts.date() と同じなら「ts以降のみ」
    #  - 違う場合（15:00以降の注文 → 翌営業日など）は trade_date の全バー
    if rec.trade_date == rec.ts.date():
        valid_mask = df.index >= rec.ts
        df_valid = df.loc[valid_mask].copy()
    else:
        df_valid = df[df.index.date == rec.trade_date].copy()

    valid_bars = len(df_valid)
    out.write(f"  有効判定バー数: {valid_bars} 本\n")

    if valid_bars == 0 or rec.entry is None:
        out.write("  ※ ts 以降の5分足（または trade_date の5分足）が存在しないため、判定不可\n")
        return

    # ===== 1) エントリー判定（entry がタッチされたか） =====
    # 指値: Low <= entry <= High のどこかで一度でもタッチしたか？
    touched_mask = (df_valid["Low"] <= rec.entry) & (df_valid["High"] >= rec.entry)
    if not bool(touched_mask.any()):
        out.write(
            f"  → 指値 {rec.entry:.2f} 円 はこの日の5分足で一度もタッチせず → no_position 扱い\n"
        )
        return

    # 最初にタッチしたバー
    first_touch_idx = df_valid.index[touched_mask.to_numpy().argmax()]
    out.write(f"  → 指値 {rec.entry:.2f} 円 が最初にタッチしたバー: {first_touch_idx.isoformat()}\n")

    # ===== 2) エグジット判定（TP/SL or ホールド） =====
    # エントリー以降、horizon_days 日分のバーを対象に TP / SL 判定
    horizon_end = rec.trade_date + timedelta(days=horizon_days)
    df_after = df[df.index >= first_touch_idx].copy()
    df_after = df_after[df_after.index.date < horizon_end].copy()

    if df_after.empty:
        out.write("  ※ エントリー後の5分足が存在しないため、判定不可（rare case）\n")
        return

    exit_price: Optional[float] = None
    exit_dt: Optional[datetime] = None
    exit_reason: str = "horizon"

    for dt_idx, row in df_after.iterrows():
        low = float(row["Low"])
        high = float(row["High"])
        close = float(row["Close"])

        # まず SL（損切り）判定 → 次に TP（利確）判定
        if rec.sl is not None and low <= rec.sl:
            exit_price = rec.sl
            exit_dt = dt_idx
            exit_reason = "hit_sl"
            break

        if rec.tp is not None and high >= rec.tp:
            exit_price = rec.tp
            exit_dt = dt_idx
            exit_reason = "hit_tp"
            break

        # どちらも触れていなければ次のバーへ

    if exit_price is None:
        # TP/SL どちらも触れず → 期間末の Close で評価
        last_row = df_after.iloc[-1]
        exit_price = float(last_row["Close"])
        exit_dt = df_after.index[-1]
        exit_reason = "horizon_close"

    out.write(
        f"  → エントリー {rec.entry:.2f} 円 → exit {exit_price:.2f} 円 ({exit_reason}) @ {exit_dt.isoformat()}\n"
    )

    # ===== 3) ラベル（win/lose/flat）判定（楽天/松井共通） =====
    if exit_price > rec.entry:
        label = "win"
    elif exit_price < rec.entry:
        label = "lose"
        # exit_price == entry なら flat
    else:
        label = "flat"

    out.write(f"    label_rakuten={label} / label_matsui={label}\n")


class Command(BaseCommand):
    help = "AIシミュレ Level3 プレビュー: 5分足ベースでエントリー/エグジット判定の結果を表示する"

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            type=int,
            required=True,
            help="対象ユーザーID",
        )
        parser.add_argument(
            "--code",
            type=str,
            default=None,
            help="銘柄コード（例: 7508）。指定がなければ全銘柄",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=5,
            help="最新から何件プレビューするか",
        )
        parser.add_argument(
            "--horizon_days",
            type=int,
            default=5,
            help="判定対象の営業日数（デフォルト5）",
        )

    def handle(self, *args, **options):
        user_id: int = options["user"]
        code: Optional[str] = options.get("code")
        limit: int = options.get("limit") or 5
        horizon_days: int = options.get("horizon_days") or 5

        media_root = Path(settings.MEDIA_ROOT)
        self.stdout.write(
            f"[preview_simulate_level3] MEDIA_ROOT={media_root} user={user_id} limit={limit}\n"
        )

        records = _load_sim_records(media_root, user_id=user_id, code=code, limit=limit)
        self.stdout.write(
            f"  対象レコード数: {len(records)} 件（limit={limit}, code={code or 'ALL'}）\n"
        )

        if not records:
            self.stdout.write("[preview_simulate_level3] 対象レコードがありません。\n")
            return

        for idx, rec in enumerate(records, start=1):
            _preview_one_record(idx, rec, horizon_days=horizon_days, out=self.stdout)

        self.stdout.write("[preview_simulate_level3] 完了\n")