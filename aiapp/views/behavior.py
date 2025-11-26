# aiapp/views/behavior.py
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone


Number = Optional[float]


@dataclass
class TradeSide:
    broker: str          # "楽天" or "松井"
    pl: float            # 損益
    r: Optional[float]   # R
    label: str           # "win" / "lose" / "flat" / "no_position" / "none"
    ts: str              # 元の ts 文字列
    ts_label: str        # 表示用 ts
    code: str
    name: str
    mode: str            # live / demo / other


def _safe_float(v: Any) -> Optional[float]:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _parse_dt(ts_str: str) -> Optional[timezone.datetime]:
    if not ts_str:
        return None
    try:
        dt = timezone.datetime.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


def _dedup_records(raw_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    行動データ latest_behavior.jsonl から読み込んだレコードを
    「同日・同コード・同モード・同エントリー・同数量」で重複除外する。
    """
    seen: set[Tuple[Any, ...]] = set()
    deduped: List[Dict[str, Any]] = []

    for r in raw_rows:
        entry = _safe_float(r.get("entry")) or 0.0
        key = (
            r.get("user_id"),
            r.get("mode"),
            r.get("code"),
            r.get("price_date"),
            round(entry, 3),
            _safe_float(r.get("qty_rakuten")) or 0.0,
            _safe_float(r.get("qty_matsui")) or 0.0,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    return deduped


def _make_ts_label(ts_str: str) -> str:
    dt = _parse_dt(ts_str)
    if dt is None:
        return ts_str or ""
    return dt.strftime("%Y-%m-%d %H:%M")


def _bucket_time_of_day(ts_str: str) -> str:
    dt = _parse_dt(ts_str)
    if dt is None:
        return "その他"
    h = dt.hour * 60 + dt.minute
    if 9 * 60 <= h < 11 * 60 + 30:
        return "前場寄り〜11:30"
    if 11 * 60 + 30 <= h < 13 * 60:
        return "お昼〜後場寄り"
    if 13 * 60 <= h <= 15 * 60:
        return "後場〜大引け"
    return "時間外/その他"


def _bucket_atr(atr: Optional[float]) -> str:
    if atr is None:
        return "不明"
    if atr < 1.0:
        return "ATR 〜1%"
    if atr < 2.0:
        return "ATR 1〜2%"
    if atr < 3.0:
        return "ATR 2〜3%"
    return "ATR 3%以上"


def _bucket_slope(slope: Optional[float]) -> str:
    if slope is None:
        return "不明"
    if slope < 0:
        return "下向き"
    if slope < 5:
        return "緩やかな上向き"
    if slope < 10:
        return "強めの上向き"
    return "急騰寄り"


def _build_insights(
    total_trades: int,
    win_rate_all: Optional[float],
    sector_stats: List[Dict[str, Any]],
    kpi_avg_win: Optional[float],
    kpi_avg_loss: Optional[float],
    dist_trend: Dict[str, int],
) -> List[str]:
    """
    インサイト文（テキスト）の生成。
    濃度C：3〜5行くらいの簡易コメント。
    （ここは生ログベース。行動モデルの分はあとから足す）
    """
    msgs: List[str] = []

    # 1) 勝率コメント
    if total_trades >= 5 and win_rate_all is not None:
        msgs.append(
            f"直近のトレード勝率はおよそ {win_rate_all:.1f}% です（対象 {total_trades} トレード）。"
        )

    # 2) セクターの得意・不得意
    best_sector: Optional[Tuple[str, float, int]] = None
    worst_sector: Optional[Tuple[str, float, int]] = None

    for s in sector_stats:
        trials = s["trials"]
        wins = s["wins"]
        if trials < 2:
            continue
        rate = (wins / trials) * 100 if trials > 0 else 0.0

        if best_sector is None or rate > best_sector[1]:
            best_sector = (s["name"], rate, trials)
        if worst_sector is None or rate < worst_sector[1]:
            worst_sector = (s["name"], rate, trials)

    if best_sector is not None:
        name, rate, trials = best_sector
        msgs.append(
            f"現状では「{name}」が比較的得意で、勝率は {rate:.1f}%（{trials} 件）になっています。"
        )

    if worst_sector is not None and worst_sector != best_sector:
        name, rate, trials = worst_sector
        msgs.append(
            f"一方で「{name}」はまだサンプルが少ないか、やや相性が悪い傾向があります（勝率 {rate:.1f}%／{trials} 件）。"
        )

    # 3) 平均利益・損失のバランス
    if kpi_avg_win is not None and kpi_avg_loss is not None:
        loss_abs = abs(kpi_avg_loss)
        msgs.append(
            f"1トレードあたりの平均利益は約 {kpi_avg_win:,.0f} 円、平均損失は約 {loss_abs:,.0f} 円です。"
            " いまは損失側の方がやや大きいため、損切り幅の見直しやロット調整の余地があります。"
        )

    # 4) トレンド方向の偏り
    if dist_trend:
        up_count = dist_trend.get("up", 0)
        total = sum(dist_trend.values())
        if total > 0:
            up_pct = up_count / total * 100
            if up_pct >= 70:
                msgs.append(
                    "上昇トレンド銘柄へのエントリーが中心になっており、"
                    "押し目〜順張りパターンに強みが集まりつつあります。"
                )

    # 5) データが少ないとき
    if not msgs:
        msgs.append(
            "まだデータ量が少ないため、はっきりしたクセは出ていません。"
            " AI Picks のシミュレを継続して貯めると、勝ちパターンと負けパターンがより明確になります。"
        )

    return msgs


@login_required
def behavior_dashboard(request: HttpRequest) -> HttpResponse:
    """
    latest_behavior.jsonl を読み込んで、
    - 重複シミュレを除外
    - 楽天 + 松井 をまとめて集計
    - KPI / セクター / 相性マップ / TOP トレード / インサイト
    を表示するダッシュボード。

    さらに latest_behavior_model_uX.json（行動モデル）も読み込んで、
    「AI がどう学習したか」の要約も表示する。
    """
    user = request.user
    behavior_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "behavior"
    latest_path = behavior_dir / "latest_behavior.jsonl"

    has_data = latest_path.exists()
    if not has_data:
        ctx = {"has_data": False}
        return render(request, "aiapp/behavior_dashboard.html", ctx)

    # ---------- JSONL 読み込み ----------
    raw_rows: List[Dict[str, Any]] = []
    try:
        text = latest_path.read_text(encoding="utf-8")
    except Exception:
        text = ""

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("user_id") != user.id:
            continue
        raw_rows.append(rec)

    # ユーザー分だけになった状態から重複除外
    rows = _dedup_records(raw_rows)

    if not rows:
        ctx = {"has_data": False}
        return render(request, "aiapp/behavior_dashboard.html", ctx)

    total = len(rows)

    # ---------- モード別件数 ----------
    mode_counts = Counter()
    for r in rows:
        mode = (r.get("mode") or "").lower()
        if mode not in ("live", "demo"):
            mode = "other"
        mode_counts[mode] += 1

    # ---------- 楽天 / 松井ごとの勝敗カウント & 全体KPI ----------
    pl_counts_r = Counter()
    pl_counts_m = Counter()

    all_trades: List[TradeSide] = []

    def add_side(broker: str, r: Dict[str, Any]) -> None:
        if broker == "楽天":
            label_key = "eval_label_rakuten"
            pl_key = "eval_pl_rakuten"
            r_key = "eval_r_rakuten"
            qty_key = "qty_rakuten"
        else:
            label_key = "eval_label_matsui"
            pl_key = "eval_pl_matsui"
            r_key = "eval_r_matsui"
            qty_key = "qty_matsui"

        label = (r.get(label_key) or "none").lower()
        pl_val = _safe_float(r.get(pl_key))
        r_val = _safe_float(r.get(r_key))
        qty = _safe_float(r.get(qty_key)) or 0.0

        # 数量0なら実質 no_position 扱い
        if qty == 0:
            label = "no_position"
            if pl_val is None:
                pl_val = 0.0

        # カウンタ更新
        if broker == "楽天":
            pl_counts_r[label] += 1
        else:
            pl_counts_m[label] += 1

        # 実トレード（勝/負/引き分け）のみ全体 KPI / TOP 用に追加
        if label in ("win", "lose", "flat"):
            if pl_val is None:
                pl_val = 0.0
            trade = TradeSide(
                broker=broker,
                pl=float(pl_val),
                r=r_val,
                label=label,
                ts=str(r.get("ts") or ""),
                ts_label=_make_ts_label(str(r.get("ts") or "")),
                code=str(r.get("code") or ""),
                name=str(r.get("name") or ""),
                mode=str(r.get("mode") or ""),
            )
            all_trades.append(trade)

    for r in rows:
        add_side("楽天", r)
        add_side("松井", r)

    # 勝率・平均R・平均利益/損失（楽天＋松井 全体）
    win_trades = [t for t in all_trades if t.label == "win"]
    lose_trades = [t for t in all_trades if t.label == "lose"]

    total_win_lose = len(win_trades) + len(lose_trades)
    win_rate_all: Optional[float] = None
    if total_win_lose > 0:
        win_rate_all = len(win_trades) / total_win_lose * 100.0

    r_values = [t.r for t in all_trades if t.r is not None]
    avg_r: Optional[float] = None
    if r_values:
        avg_r = sum(r_values) / len(r_values)

    avg_win: Optional[float] = None
    if win_trades:
        avg_win = sum(t.pl for t in win_trades) / len(win_trades)

    avg_loss: Optional[float] = None
    if lose_trades:
        avg_loss = sum(t.pl for t in lose_trades) / len(lose_trades)

    # ---------- セクター別勝率（楽天のみ） ----------
    sector_counter_trials: Dict[str, int] = defaultdict(int)
    sector_counter_win: Dict[str, int] = defaultdict(int)

    for r in rows:
        label = (r.get("eval_label_rakuten") or "none").lower()
        sector = str(r.get("sector") or "(未分類)")
        if label in ("win", "lose", "flat"):
            sector_counter_trials[sector] += 1
            if label == "win":
                sector_counter_win[sector] += 1

    sector_stats: List[Dict[str, Any]] = []
    for sec, trials in sector_counter_trials.items():
        wins = sector_counter_win.get(sec, 0)
        win_rate = (wins / trials * 100.0) if trials > 0 else 0.0
        sector_stats.append(
            {"name": sec, "trials": trials, "wins": wins, "win_rate": win_rate}
        )

    sector_stats.sort(key=lambda x: (-x["trials"], -x["win_rate"]))

    # ---------- 相性マップ ----------
    trend_counter: Dict[str, int] = defaultdict(int)
    time_counter: Dict[str, int] = defaultdict(int)
    atr_counter: Dict[str, int] = defaultdict(int)
    slope_counter: Dict[str, int] = defaultdict(int)

    for r in rows:
        trend = str(r.get("trend_daily") or "不明")
        trend_counter[trend] += 1

        time_bucket = _bucket_time_of_day(str(r.get("ts") or ""))
        time_counter[time_bucket] += 1

        atr = _safe_float(r.get("atr_14"))
        atr_bucket = _bucket_atr(atr)
        atr_counter[atr_bucket] += 1

        slope = _safe_float(r.get("slope_20"))
        slope_bucket = _bucket_slope(slope)
        slope_counter[slope_bucket] += 1

    def _build_dist_list(counter: Dict[str, int]) -> List[Dict[str, Any]]:
        total_count = sum(counter.values()) or 1
        items: List[Dict[str, Any]] = []
        for name, c in counter.items():
            items.append(
                {
                    "name": name,
                    "count": c,
                    "pct": c / total_count * 100.0,
                }
            )
        items.sort(key=lambda x: -x["count"])
        return items

    trend_stats = _build_dist_list(trend_counter)
    time_stats = _build_dist_list(time_counter)
    atr_stats = _build_dist_list(atr_counter)
    slope_stats = _build_dist_list(slope_counter)

    # ---------- TOP 勝ち / 負け（楽天＋松井 混在） ----------
    top_win: List[Dict[str, Any]] = []
    for t in sorted(win_trades, key=lambda x: x.pl, reverse=True)[:5]:
        top_win.append(
            {
                "code": t.code,
                "name": t.name,
                "broker": t.broker,
                "pl": t.pl,
                "mode": t.mode,
                "ts_label": t.ts_label,
            }
        )

    top_lose: List[Dict[str, Any]] = []
    for t in sorted(lose_trades, key=lambda x: x.pl)[:5]:
        top_lose.append(
            {
                "code": t.code,
                "name": t.name,
                "broker": t.broker,
                "pl": t.pl,
                "mode": t.mode,
                "ts_label": t.ts_label,
            }
        )

    # ---------- 行動モデル（latest_behavior_model_uX.json）読み込み ----------
    behavior_model: Dict[str, Any] = {
        "has_model": False,
        "total_trades": None,
        "wins": None,
        "win_rate": None,
        "avg_pl": None,
        "avg_r": None,
        "brokers": [],
        "sectors": [],
    }

    model_dir = behavior_dir / "model"
    model_path_user = model_dir / f"latest_behavior_model_u{user.id}.json"
    model_path_all = model_dir / "latest_behavior_model_uall.json"

    model_path: Optional[Path] = None
    if model_path_user.exists():
        model_path = model_path_user
    elif model_path_all.exists():
        model_path = model_path_all

    if model_path is not None:
        try:
            j = json.loads(model_path.read_text(encoding="utf-8"))
            behavior_model["has_model"] = True
            behavior_model["total_trades"] = j.get("total_trades")
            behavior_model["wins"] = j.get("wins")
            behavior_model["win_rate"] = j.get("win_rate")
            behavior_model["avg_pl"] = j.get("avg_pl")
            behavior_model["avg_r"] = j.get("avg_r")

            by_feature = j.get("by_feature", {}) or {}

            # broker 別
            brokers: List[Dict[str, Any]] = []
            broker_map = by_feature.get("broker", {}) or {}
            label_map = {"rakuten": "楽天", "matsui": "松井"}
            for key, val in broker_map.items():
                brokers.append(
                    {
                        "key": key,
                        "label": label_map.get(key, key),
                        "trials": val.get("trials", 0),
                        "wins": val.get("wins", 0),
                        "win_rate": val.get("win_rate", 0.0),
                        "avg_pl": val.get("avg_pl"),
                        "avg_r": val.get("avg_r"),
                    }
                )
            brokers.sort(key=lambda x: -x["trials"])
            behavior_model["brokers"] = brokers

            # sector 別（上位5件まで）
            sectors: List[Dict[str, Any]] = []
            sector_map = by_feature.get("sector", {}) or {}
            for name, val in sector_map.items():
                sectors.append(
                    {
                        "name": name,
                        "trials": val.get("trials", 0),
                        "wins": val.get("wins", 0),
                        "win_rate": val.get("win_rate", 0.0),
                        "avg_pl": val.get("avg_pl"),
                        "avg_r": val.get("avg_r"),
                    }
                )
            sectors.sort(key=lambda x: -x["trials"])
            behavior_model["sectors"] = sectors[:5]

        except Exception:
            behavior_model["has_model"] = False

    # ---------- インサイト（元ロジック＋行動モデルをあと乗せ） ----------
    total_trades_for_insight = len(win_trades) + len(lose_trades) + len(
        [t for t in all_trades if t.label == "flat"]
    )
    insights = _build_insights(
        total_trades=total_trades_for_insight,
        win_rate_all=win_rate_all,
        sector_stats=sector_stats,
        kpi_avg_win=avg_win,
        kpi_avg_loss=avg_loss,
        dist_trend={k: v for k, v in trend_counter.items()},
    )

    extra_insights: List[str] = []
    if behavior_model.get("has_model"):
        avg_r_model = behavior_model.get("avg_r")
        if isinstance(avg_r_model, (int, float)):
            if avg_r_model >= 0:
                extra_insights.append(
                    f"行動モデル全体で見ると、リスク1単位あたりの平均Rは {avg_r_model:.2f} とおおむねフラット〜ややプラス圏です。"
                    " 勝ちパターンのRを伸ばしつつ、この水準を安定して維持できると期待値はかなり良くなります。"
                )
            else:
                extra_insights.append(
                    f"行動モデル全体の平均Rは {avg_r_model:.2f} とマイナス寄りです。"
                    " いまは「負けのRを小さく抑える」ことを優先テーマにすると、カーブの傾きが改善しやすくなります。"
                )

        brokers_ctx = behavior_model.get("brokers") or []
        valid_brokers = [b for b in brokers_ctx if (b.get("trials") or 0) >= 1]
        if valid_brokers:
            best = max(
                valid_brokers,
                key=lambda x: (x.get("win_rate") is not None, x.get("win_rate") or 0.0),
            )
            label = best.get("label", "")
            win_rate_b = best.get("win_rate") or 0.0
            extra_insights.append(
                f"口座別では「{label}」の勝率が {win_rate_b:.1f}% と相対的に安定しています。"
                " 当面はこの口座側のルールやサイズ感をベースに、他口座も揃えていくのがおすすめです。"
            )

    insights = (insights or []) + extra_insights
    if len(insights) > 5:
        insights = insights[:5]

    # ---------- コンテキスト ----------
    ctx = {
        "has_data": True,
        "total": total,
        "mode_counts": {
            "live": mode_counts.get("live", 0),
            "demo": mode_counts.get("demo", 0),
            "other": mode_counts.get("other", 0),
        },
        # KPI（楽天＋松井 全体）
        "kpi_win_rate": win_rate_all,
        "kpi_avg_r": avg_r,
        "kpi_avg_win": avg_win,
        "kpi_avg_loss": avg_loss,
        # 勝敗サマリ（楽天 / 松井）
        "pl_counts_r": pl_counts_r,
        "pl_counts_m": pl_counts_m,
        # セクター・相性マップ
        "sector_stats": sector_stats,
        "trend_stats": trend_stats,
        "time_stats": time_stats,
        "atr_stats": atr_stats,
        "slope_stats": slope_stats,
        # TOP トレード
        "top_win": top_win,
        "top_lose": top_lose,
        # インサイト文
        "insights": insights,
        # 行動モデル（テンプレ用そのまま）
        "behavior_model": behavior_model,
    }
    return render(request, "aiapp/behavior_dashboard.html", ctx)