# aiapp/management/commands/preview_simulate_level3.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

# 5分足キャッシュサービス（既存のサービス想定）
# get_5m_bars_range(code: str, start_date: date, horizon_days: int) -> List[Bar]
# Bar は ts / open / high / low / close を持つ（dict or オブジェクト）
try:
    from aiapp.services.bars_5m import get_5m_bars_range  # type: ignore
except Exception:  # 開発中の安全策
    get_5m_bars_range = None  # type: ignore


Number = Optional[float]


@dataclass
class Bar5m:
    ts: timezone.datetime
    open: float
    high: float
    low: float
    close: float


def _safe_float(v: Any) -> Optional[float]:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _parse_ts(ts_str: Optional[str]) -> Optional[timezone.datetime]:
    """
    JSONL の ts(ISO文字列) を timezone-aware datetime に変換。
    失敗した場合は None。
    """
    if not isinstance(ts_str, str) or not ts_str:
        return None
    try:
        dt = timezone.datetime.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


def _parse_trade_date(price_date: Optional[str], fallback_dt: timezone.datetime):
    """
    price_date(YYYY-MM-DD) があればそれを日付として採用。
    無ければ ts(fallback_dt) の日付を使う。
    """
    if isinstance(price_date, str):
        s = price_date.strip()
        if s and s.lower() != "none":
            try:
                y, m, d = s.split("-")
                return timezone.datetime(int(y), int(m), int(d)).date()
            except Exception:
                pass
    return fallback_dt.date()


def _coerce_bar(obj: Any) -> Optional[Bar5m]:
    """
    5分足1本を Bar5m に正規化する。
    dictでもオブジェクトでも動くようにしておく。
    """
    if obj is None:
        return None

    def get_attr(o: Any, key: str):
        if isinstance(o, dict):
            return o.get(key)
        return getattr(o, key, None)

    ts = get_attr(obj, "ts")
    if isinstance(ts, str):
        ts = _parse_ts(ts)
    if isinstance(ts, timezone.datetime):
        if timezone.is_naive(ts):
            ts = timezone.make_aware(ts, timezone.get_default_timezone())
        ts = timezone.localtime(ts)
    else:
        return None

    def f(key: str) -> Optional[float]:
        return _safe_float(get_attr(obj, key))

    o = f("open")
    h = f("high")
    l = f("low")
    c = f("close")

    if o is None or h is None or l is None or c is None:
        return None

    return Bar5m(ts=ts, open=o, high=h, low=l, close=c)


def _load_5m_bars(code: str, trade_date, horizon_days: int = 5) -> List[Bar5m]:
    """
    5分足キャッシュから、trade_date から horizon_days 営業日ぶんを取得。
    取得できなければ空リスト。
    """
    if get_5m_bars_range is None:
        return []

    try:
        raw = get_5m_bars_range(code, trade_date, horizon_days=horizon_days)
    except Exception:
        return []

    bars: List[Bar5m] = []
    for obj in raw or []:
        bar = _coerce_bar(obj)
        if bar is not None:
            bars.append(bar)

    # 念のため時刻順にソート
    bars.sort(key=lambda b: b.ts)
    return bars


@dataclass
class EvalResult:
    broker: str
    qty: float
    entry: Optional[float]
    filled: bool
    entry_ts: Optional[timezone.datetime]
    exit_ts: Optional[timezone.datetime]
    exit_px: Optional[float]
    pl: Optional[float]
    label: str  # win / lose / flat / no_position / error
    reason: str


def _eval_one_side_level3(
    broker_key: str,
    rec: Dict[str, Any],
    bars: List[Bar5m],
    order_ts: timezone.datetime,
    horizon_days: int,
) -> EvalResult:
    """
    レベル3：5分足を使った判定（エントリーは「その日だけ」有効版）

    - broker_key: "rakuten" or "matsui"
    - エントリー判定：
        * trade_date = price_date or ts.date()
        * その日の order_ts 以降の 5分足だけを見る
        * low <= entry <= high が一度でもあれば filled=True
        * それ以外は no_position
    - exit 判定：
        * いったんシンプルに「entry_ts 以降の最後の足の close」を exit とする
        * horizon_days は bars を取得する範囲にのみ関与
    """
    label_suffix = "_rakuten" if broker_key == "rakuten" else "_matsui"

    # 数量
    qty_key = f"qty_{broker_key}"
    qty = _safe_float(rec.get(qty_key)) or 0.0

    # 指値
    entry = _safe_float(rec.get("entry"))

    if qty <= 0:
        return EvalResult(
            broker=broker_key,
            qty=qty,
            entry=entry,
            filled=False,
            entry_ts=None,
            exit_ts=None,
            exit_px=None,
            pl=0.0,
            label="no_position",
            reason="数量が0のため、そもそも発注されていない扱い。",
        )

    if entry is None:
        return EvalResult(
            broker=broker_key,
            qty=qty,
            entry=None,
            filled=False,
            entry_ts=None,
            exit_ts=None,
            exit_px=None,
            pl=None,
            label="error",
            reason="entry価格が記録されていないため、判定できません。",
        )

    if not bars:
        return EvalResult(
            broker=broker_key,
            qty=qty,
            entry=entry,
            filled=False,
            entry_ts=None,
            exit_ts=None,
            exit_px=None,
            pl=None,
            label="error",
            reason="5分足が取得できなかったため、判定できません。",
        )

    # -------- エントリー判定：その日だけ有効 --------
    trade_date = _parse_trade_date(rec.get("price_date"), order_ts)

    entry_bars: List[Bar5m] = [
        b for b in bars if (b.ts.date() == trade_date and b.ts >= order_ts)
    ]

    if not entry_bars:
        return EvalResult(
            broker=broker_key,
            qty=qty,
            entry=entry,
            filled=False,
            entry_ts=None,
            exit_ts=None,
            exit_px=None,
            pl=0.0,
            label="no_position",
            reason="エントリー当日の場中5分足が取得できなかったため、約定しなかった扱い。",
        )

    filled = False
    entry_ts: Optional[timezone.datetime] = None

    for b in entry_bars:
        if b.low <= entry <= b.high:
            filled = True
            entry_ts = b.ts
            break

    if not filled:
        return EvalResult(
            broker=broker_key,
            qty=qty,
            entry=entry,
            filled=False,
            entry_ts=None,
            exit_ts=None,
            exit_px=None,
            pl=0.0,
            label="no_position",
            reason="エントリー当日の場中で、一度も指値価格にタッチしなかったため。",
        )

    # -------- exit 判定：entry_ts 以降の最後の足 --------
    after_bars = [b for b in bars if b.ts > entry_ts]
    if not after_bars:
        return EvalResult(
            broker=broker_key,
            qty=qty,
            entry=entry,
            filled=True,
            entry_ts=entry_ts,
            exit_ts=None,
            exit_px=None,
            pl=0.0,
            label="flat",
            reason="約定後の足が無かったため、PL=0の引き分け扱い。",
        )

    last_bar = after_bars[-1]
    exit_px = last_bar.close
    pl = (exit_px - entry) * qty

    if pl > 0:
        label = "win"
    elif pl < 0:
        label = "lose"
    else:
        label = "flat"

    return EvalResult(
        broker=broker_key,
        qty=qty,
        entry=entry,
        filled=True,
        entry_ts=entry_ts,
        exit_ts=last_bar.ts,
        exit_px=exit_px,
        pl=pl,
        label=label,
        reason="レベル3ロジック：エントリー当日中に約定し、その後の終端足で評価。",
    )


class Command(BaseCommand):
    help = "AIシミュレを 5分足レベル3ロジック（エントリー当日だけ有効）でプレビュー表示する。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            type=int,
            default=None,
            help="対象ユーザーID（省略時は全ユーザーのログを対象にする）",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="最大表示件数（新しい順）",
        )
        parser.add_argument(
            "--code",
            type=str,
            default=None,
            help="銘柄コードでフィルタ（例: 7508）",
        )
        parser.add_argument(
            "--horizon",
            type=int,
            default=5,
            help="5分足を取得する営業日数（exit評価用の期間）。デフォルト5。",
        )

    def handle(self, *args, **options):
        user_id: Optional[int] = options.get("user")
        limit: int = options.get("limit") or 20
        code_filter: Optional[str] = options.get("code")
        horizon_days: int = options.get("horizon") or 5

        media_root = Path(settings.MEDIA_ROOT)
        sim_dir = media_root / "aiapp" / "simulate"

        self.stdout.write(
            f"[preview_simulate_level3] MEDIA_ROOT={media_root} user={user_id} horizon_days={horizon_days}"
        )

        if not sim_dir.exists():
            self.stdout.write(f"  simulateディレクトリがありません: {sim_dir}")
            return

        # ---------- JSONL 読み込み（全ファイル） ----------
        records: List[Dict[str, Any]] = []

        for path in sorted(sim_dir.glob("*.jsonl")):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception as e:
                self.stdout.write(f"  !! 読み込み失敗: {path} ({e})")
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

                if code_filter:
                    if str(rec.get("code") or "").strip() != code_filter.strip():
                        continue

                dt = _parse_ts(rec.get("ts"))
                if dt is None:
                    continue
                rec["_dt"] = dt
                records.append(rec)

        if not records:
            self.stdout.write("  対象となるシミュレ記録が見つかりません。")
            return

        # 新しい順
        records.sort(key=lambda r: r["_dt"], reverse=True)
        records = records[:limit]

        self.stdout.write(
            f"  対象レコード数: {len(records)} 件（limit={limit}, code={code_filter or 'ALL'}）\n"
        )

        # ---------- 各レコードをレベル3ロジックで評価 ----------
        for idx, rec in enumerate(records, start=1):
            ts: timezone.datetime = rec["_dt"]
            code = str(rec.get("code") or "")
            name = str(rec.get("name") or "(名称不明)")
            mode = str(rec.get("mode") or "demo")
            price_date = rec.get("price_date")

            trade_date = _parse_trade_date(price_date, ts)

            self.stdout.write(
                f"===== #{idx} {code} {name}  ts={ts.strftime('%Y-%m-%d %H:%M:%S')} "
                f"mode={mode} price_date={price_date or trade_date} ====="
            )

            # 5分足をまとめて取得（trade_date から horizon_days 営業日ぶん）
            bars = _load_5m_bars(code, trade_date, horizon_days=horizon_days)
            self.stdout.write(f"  5分足取得: {len(bars)} 本")

            if not bars:
                self.stdout.write("  ※ 5分足が取得できなかったため、両サイドとも判定不可\n")
                continue

            # 楽天・松井それぞれ評価
            for broker_key, label_ja in (("rakuten", "楽天"), ("matsui", "松井")):
                qty_key = f"qty_{broker_key}"
                qty = _safe_float(rec.get(qty_key)) or 0.0
                if qty <= 0:
                    self.stdout.write(f"  [{label_ja}] 数量0 → 判定スキップ（no_position）")
                    continue

                result = _eval_one_side_level3(
                    broker_key=broker_key,
                    rec=rec,
                    bars=bars,
                    order_ts=ts,
                    horizon_days=horizon_days,
                )

                # 表示整形
                entry_str = (
                    f"{result.entry:,.2f} 円" if result.entry is not None else "(不明)"
                )
                pl_str = (
                    f"{result.pl:,.0f} 円" if result.pl is not None else "(不明)"
                )
                if result.exit_px is not None:
                    exit_px_str = f"{result.exit_px:,.2f} 円"
                else:
                    exit_px_str = "(なし)"

                entry_ts_str = (
                    result.entry_ts.strftime("%Y-%m-%d %H:%M")
                    if isinstance(result.entry_ts, timezone.datetime)
                    else "-"
                )
                exit_ts_str = (
                    result.exit_ts.strftime("%Y-%m-%d %H:%M")
                    if isinstance(result.exit_ts, timezone.datetime)
                    else "-"
                )

                self.stdout.write(
                    f"  [{label_ja}] label={result.label} qty={qty:,.0f} 株 "
                    f"entry={entry_str} entry_ts={entry_ts_str} "
                    f"exit_px={exit_px_str} exit_ts={exit_ts_str} PL={pl_str}"
                )
                if result.reason:
                    self.stdout.write(f"        → {result.reason}")

            self.stdout.write("")  # 空行で区切る

        self.stdout.write("[preview_simulate_level3] 完了\n")