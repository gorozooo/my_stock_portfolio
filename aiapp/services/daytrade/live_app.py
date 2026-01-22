# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/live_app.py

これは何？
- 「朝に Judge が GO のときだけ、場中だけ動く」デイトレ本番アプリ本体。
- LiveRunner（5分→1分）を駆動する“箱”。
- データ取得は provider を差し替える設計。

置き場所
- <project_root>/aiapp/services/daytrade/live_app.py

前提
- 朝の Judge snapshot が保存されている（judge_snapshot.py で作る）
  例: media/aiapp/daytrade/judge/YYYYMMDD/judge.json

重要な割り切り
- 無料範囲前提のため、1分足は「執行用」だけ（過去は不要）。
- データ欠損・出来高欠損は安全側（見送り）で処理。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, time, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import json
import time as time_mod

from django.conf import settings

from .policy_loader import load_policy_yaml
from .minute_collector import MinuteBarBuilder, Tick
from .live_runner import LiveRunner, OrderExecutor, Signal
from .types import Bar  # 既存の Bar（バックテスト/戦略で使っている型）


# ====== Judge snapshot 読み込み ======

def _project_root() -> Path:
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir is None:
        raise RuntimeError("Django settings.BASE_DIR is not set.")
    return Path(base_dir).resolve()


def judge_snapshot_path(d: date) -> Path:
    """
    media/aiapp/daytrade/judge/YYYYMMDD/judge.json
    """
    ymd = d.strftime("%Y%m%d")
    return _project_root() / "media" / "aiapp" / "daytrade" / "judge" / ymd / "judge.json"


def load_today_judge(d: date) -> Dict[str, Any]:
    p = judge_snapshot_path(d)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def is_go_today(d: date) -> bool:
    j = load_today_judge(d)
    return (j.get("decision") == "GO")


# ====== 時間帯ユーティリティ ======

def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


def _now_time_jst() -> time:
    # VPSはJST設定前提（あなたの環境）
    return datetime.now().time()


def _is_in_range(t: time, start: time, end: time) -> bool:
    # start <= t < end
    return (t >= start) and (t < end)


def _in_excludes(t: time, excludes: List[Tuple[time, time]]) -> bool:
    for a, b in excludes:
        if _is_in_range(t, a, b):
            return True
    return False


# ====== Provider（差し替え前提） ======

@dataclass
class RealtimeQuote:
    """
    1分足生成の材料（ティック相当）。
    - price は必須
    - volume は取れたら入れる（取れなければ None）
    - vwap は取れたら入れる（取れなければ None）
    """
    dt: datetime
    price: float
    volume: Optional[float] = None
    vwap: Optional[float] = None


class RealtimeProvider:
    """
    1分足用のリアルタイム取得インターフェース。
    将来ここを「楽天RSS / 証券API / WebSocket」に差し替える。
    """

    def get_quote(self, ticker: str) -> Optional[RealtimeQuote]:
        raise NotImplementedError


class DummyRealtimeProvider(RealtimeProvider):
    """
    ダミー：本番では使わない。
    - 実運用は必ず差し替えること。
    """

    def __init__(self):
        self._px = 1000.0
        self._vol = 1000.0

    def get_quote(self, ticker: str) -> Optional[RealtimeQuote]:
        now = datetime.now()
        # 適当なランダム風（外部ライブラリ不要）
        self._px += 0.2
        self._vol += 10.0
        return RealtimeQuote(dt=now, price=self._px, volume=10.0, vwap=1000.0)


class SignalProvider5m:
    """
    5分足シグナル生成インターフェース。
    - 既存の strategies / backtest_runner と同じ考えで、
      「今、入る候補が出たか」を返す。
    """

    def poll_signal(self, policy: dict) -> Optional[Signal]:
        raise NotImplementedError


class DummySignalProvider5m(SignalProvider5m):
    """
    ダミー：本番では使わない。
    たまにシグナルを出すだけ。
    """

    def __init__(self):
        self._last_emit: Optional[datetime] = None

    def poll_signal(self, policy: dict) -> Optional[Signal]:
        now = datetime.now()
        # 10分に1回だけ、ダミーシグナルを出す
        if self._last_emit and (now - self._last_emit).total_seconds() < 600:
            return None
        self._last_emit = now

        base_capital = float(policy.get("capital", {}).get("base_capital", 1000000))
        trade_loss_pct = float(policy.get("risk", {}).get("trade_loss_pct", 0.003))
        planned_risk_yen = base_capital * trade_loss_pct

        px = 1000.0
        stop = 998.0
        tp = 1004.0
        max_hold = int(policy.get("exit", {}).get("max_hold_minutes", 15))

        return Signal(
            side="long",
            entry_price=px,
            stop_price=stop,
            take_profit_price=tp,
            max_hold_minutes=max_hold,
            planned_risk_yen=planned_risk_yen,
        )


# ====== Live App ======

class DaytradeLiveApp:
    """
    GO確認 → 場中だけ稼働 → 停止 までをまとめたアプリ。
    """

    def __init__(
        self,
        realtime: RealtimeProvider,
        signal5m: SignalProvider5m,
        executor: Optional[OrderExecutor] = None,
    ):
        self.realtime = realtime
        self.signal5m = signal5m
        self.executor = executor or OrderExecutor()

        loaded = load_policy_yaml()
        self.policy = loaded.policy

        self.live_runner = LiveRunner(policy=self.policy, executor=self.executor)
        self.builder = MinuteBarBuilder()

        # time_filter は active.yml 参照
        tf = self.policy.get("time_filter", {})
        self.session_start = _parse_hhmm(str(tf.get("session_start", "09:15")))
        self.session_end = _parse_hhmm(str(tf.get("session_end", "14:30")))

        ex = []
        for a, b in (tf.get("exclude_ranges") or []):
            try:
                ex.append((_parse_hhmm(a), _parse_hhmm(b)))
            except Exception:
                continue
        self.excludes = ex

        # 対象ティッカー（最小：ウォッチ or picks から取る想定）
        # まずは「1銘柄だけ」で運用開始できるようにする（全自動向け）
        self.ticker = str((self.policy.get("universe_filter", {}) or {}).get("single_ticker", "DUMMY"))

        self._stop_flag = False

    def should_run_now(self) -> bool:
        t = _now_time_jst()
        if not _is_in_range(t, self.session_start, self.session_end):
            return False
        if _in_excludes(t, self.excludes):
            return False
        return True

    def run(self):
        """
        場中だけ回るメインループ。
        - 1秒おきに回す（重くしない）
        - 5分足シグナルはpollで拾う
        - 1分足はティックから自作（MinuteBarBuilder）
        """
        print("[LIVE] start daytrade live app")
        print("[LIVE] ticker =", self.ticker)
        print("[LIVE] session =", self.session_start, "-", self.session_end)
        print("[LIVE] excludes =", self.excludes)

        while not self._stop_flag:
            if not self.should_run_now():
                # 場外は軽く待つ（cron側で止めてもOKだが、念のため安全）
                time_mod.sleep(2.0)
                continue

            # 5分足シグナル（候補）を拾う
            sig = self.signal5m.poll_signal(self.policy)
            if sig is not None:
                self.live_runner.on_signal(sig)

            # 1分ティック取得
            q = self.realtime.get_quote(self.ticker)
            if q is None or q.price is None:
                time_mod.sleep(1.0)
                continue

            # 1分足バー生成（擬似OHLC）
            tick = Tick(dt=q.dt, price=float(q.price), volume=q.volume)
            bar1m = self.builder.update(tick)

            if bar1m is not None:
                # vwap が取得できるならここで詰める
                bar1m.vwap = q.vwap

                # LiveRunnerへ渡す
                # 1) エントリー判定（シグナルがある場合のみ）
                self.live_runner.on_minute_bar(bar1m)

                # 2) ポジション管理（保有中のみ動く）
                self.live_runner.on_minute_bar_position(bar1m)

            time_mod.sleep(1.0)

        print("[LIVE] stop daytrade live app")

    def stop(self):
        self._stop_flag = True