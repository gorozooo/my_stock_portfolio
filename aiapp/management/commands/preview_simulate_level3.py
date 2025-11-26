# aiapp/management/commands/preview_simulate_level3.py
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from aiapp.services.bars_5m import get_5m_bars_range


def _parse_ts(ts_str: Optional[str]) -> Optional[timezone.datetime]:
    """
    JSONL の ts(ISO文字列) を timezone-aware datetime に変換する。
    失敗した場合は None。
    """
    if not isinstance(ts_str, str) or not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


def _iter_simulate_records(
    simulate_dir: Path,
    user_id: Optional[int] = None,
    code: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    /media/aiapp/simulate/*.jsonl から AIシミュレのレコードを読み込む。
    - user_id / code で絞り込み
    - ts 降順でソートして返す
    """
    records: List[Dict[str, Any]] = []
    if not simulate_dir.exists():
        return records

    for path in sorted(simulate_dir.glob("*.jsonl")):
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

            if user_id is not None and rec.get("user_id") != user_id:
                continue
            if code and str(rec.get("code") or "").strip() != str(code).strip():
                continue

            ts = _parse_ts(rec.get("ts"))
            rec["_dt"] = ts
            records.append(rec)

    # ts 降順
    def _sort_key(r: Dict[str, Any]):
        dt = r.get("_dt")
        if isinstance(dt, timezone.datetime):
            return dt
        return datetime.min

    records.sort(key=_sort_key, reverse=True)
    return records


def _pick_trade_date(rec: Dict[str, Any]) -> Optional[date]:
    """
    取引日（その日の5分足を引きたい日）を決める。
    - price_date(YYYY-MM-DD or YYYY/MM/DD) があれば優先
    - 無ければ ts の日付
    """
    price_date = rec.get("price_date")
    if isinstance(price_date, str) and price_date:
        # 2025-11-26 / 2025/11/26 の両方を許容
        try:
            return datetime.fromisoformat(price_date).date()
        except Exception:
            try:
                return datetime.strptime(price_date, "%Y/%m/%d").date()
            except Exception:
                pass

    dt = rec.get("_dt")
    if isinstance(dt, timezone.datetime):
        return dt.date()

    return None


def _preview_one_record(
    idx: int,
    rec: Dict[str, Any],
    horizon_days: int,
) -> None:
    """
    1件ぶんのシミュレに対して、5分足レベル3の「指値ヒット判定」をプレビュー表示。
    （今はエントリーがタッチしたか？＆タッチしたらその日の引けまで持ってたと仮定したPL）
    """
    code = str(rec.get("code") or "")
    name = str(rec.get("name") or "")
    mode = str(rec.get("mode") or "")
    ts = rec.get("_dt") or rec.get("ts")
    price_date = rec.get("price_date") or "None"
    entry = rec.get("entry")

    print(
        f"===== #{idx} {code} {name}  ts={ts} mode={mode} price_date={price_date} ====="
    )

    trade_date = _pick_trade_date(rec)
    if trade_date is None:
        print("  ※ 取引日(trade_date)が特定できなかったため、判定不可")
        return

    # ---------- 5分足取得（DataFrame 前提） ----------
    bars: pd.DataFrame = get_5m_bars_range(code, trade_date, horizon_days=horizon_days)
    n_all = int(len(bars)) if bars is not None else 0
    print(f"  5分足取得: {n_all} 本")

    if n_all == 0 or bars is None:
        print("  ※ 5分足が取得できなかったため、両サイドとも判定不可")
        return

    # ---------- entry のチェック ----------
    if entry is None:
        print("  ※ entry が記録されていないため、指値判定はスキップします")
        return

    try:
        entry_f = float(entry)
    except Exception:
        print("  ※ entry が数値に変換できないため、指値判定はスキップします")
        return

    # ---------- ts 以降の足だけを見る（レベル3の入口） ----------
    dt_ts = rec.get("_dt")
    if isinstance(dt_ts, timezone.datetime):
        # ts の日付と trade_date が違うときは、その日の全バーを対象に
        if dt_ts.date() != trade_date:
            bars_active = bars.copy()
        else:
            # ts 以降の5分足だけを判定対象に
            bars_active = bars[bars.index >= dt_ts].copy()
    else:
        # ts 不明 → その日の全バー
        bars_active = bars.copy()

    n_active = len(bars_active)
    print(f"  有効判定バー数: {n_active} 本")

    if n_active == 0:
        print("  ※ ts 以降の5分足が無いため、判定不可（寄り付き前に登録されていて、その後データ無し等）")
        return

    # ---------- 指値が一度でもタッチしたか？ ----------
    # yfinance の 5分足が MultiIndex 列になっている場合があるので、
    # "Low" / "High" が DataFrame なら 1列目を抜き出して Series 化してから判定する。
    low = bars_active["Low"]
    high = bars_active["High"]

    if isinstance(low, pd.DataFrame):
        low = low.iloc[:, 0]
    if isinstance(high, pd.DataFrame):
        high = high.iloc[:, 0]

    # ここまでで low / high は必ず Series のはず
    hit_mask = (low <= entry_f) & (entry_f <= high)

    # Series.any() は bool を返すので、そのまま使える
    touched = bool(hit_mask.any())

    if not touched:
        print(f"  → 指値 {entry_f:.2f} 円 はこの日の5分足で一度もタッチせず → no_position 扱い")
        return

    # 触れた最初のバーを「約定バー」とみなす
    hit_idx_list = list(bars_active.index[hit_mask])
    hit_dt = hit_idx_list[0]
    hit_row = bars_active.loc[hit_dt]

    # 約定価格は、そのバーの Open としておく（成行で一発目に入った想定）
    if isinstance(hit_row, pd.Series):
        exec_px = float(hit_row["Open"])
    else:
        # 万一 DataFrame になっていた場合のフォールバック（1列目）
        exec_px = float(hit_row["Open"].iloc[0])

    print(f"  → 指値にヒット: {hit_dt} で約定扱い（exec_entry={exec_px:.2f} 円）")

    # いまは簡易版として、その日の最後の5分足 Close を決済価格とみなす
    last_row = bars_active.iloc[-1]
    if isinstance(last_row, pd.Series):
        close_px = float(last_row["Close"])
    else:
        close_px = float(last_row["Close"].iloc[0])

    qty_r = float(rec.get("qty_rakuten") or 0)
    qty_m = float(rec.get("qty_matsui") or 0)

    def _label(pl: float) -> str:
        if pl > 0:
            return "win"
        if pl < 0:
            return "lose"
        return "flat"

    if qty_r > 0:
        pl_r = (close_px - exec_px) * qty_r
        print(
            f"  [楽天] qty={qty_r:.0f} exec={exec_px:.2f} → close={close_px:.2f}  損益={pl_r:,.0f} 円 ({_label(pl_r)})"
        )
    else:
        print("  [楽天] qty=0 → そもそもポジションなし")

    if qty_m > 0:
        pl_m = (close_px - exec_px) * qty_m
        print(
            f"  [松井] qty={qty_m:.0f} exec={exec_px:.2f} → close={close_px:.2f}  損益={pl_m:,.0f} 円 ({_label(pl_m)})"
        )
    else:
        print("  [松井] qty=0 → そもそもポジションなし")


class Command(BaseCommand):
    help = "AIシミュレの Level3 エントリー判定（5分足）をプレビュー表示する。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--user",
            type=int,
            dest="user_id",
            default=None,
            help="対象ユーザーID（省略時は全ユーザー）",
        )
        parser.add_argument(
            "--code",
            type=str,
            dest="code",
            default=None,
            help="銘柄コードを絞り込み（例: 7508）",
        )
        parser.add_argument(
            "--limit",
            type=int,
            dest="limit",
            default=20,
            help="プレビューする最大件数",
        )
        parser.add_argument(
            "--horizon",
            type=int,
            dest="horizon_days",
            default=5,
            help="評価用の営業日数（現状は5分足取得範囲の調整にのみ使用）",
        )

    def handle(self, *args, **options) -> None:
        media_root = settings.MEDIA_ROOT
        user_id: Optional[int] = options.get("user_id")
        code: Optional[str] = options.get("code")
        limit: int = options.get("limit") or 20
        horizon_days: int = options.get("horizon_days") or 5

        print(
            f"[preview_simulate_level3] MEDIA_ROOT={media_root} user={user_id} horizon_days={horizon_days}"
        )

        simulate_dir = Path(media_root) / "aiapp" / "simulate"

        records = _iter_simulate_records(simulate_dir, user_id=user_id, code=code)
        total = len(records)
        if limit and total > limit:
            records = records[:limit]

        print(
            f"  対象レコード数: {total} 件（limit={limit}, code={code or 'ALL'}）"
        )

        if not records:
            print("  ※ 対象レコードがありません。")
            return

        for idx, rec in enumerate(records, start=1):
            _preview_one_record(idx, rec, horizon_days=horizon_days)

        print("[preview_simulate_level3] 完了")