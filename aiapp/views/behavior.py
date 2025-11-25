# aiapp/views/behavior.py
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone


Number = float | int


def _to_float(v: Any) -> Optional[float]:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


@dataclass
class Record:
    raw: Dict[str, Any]
    ts: timezone.datetime
    ts_label: str
    mode: str
    code: str
    name: str
    sector: str | None
    qty_rakuten: float
    eval_pl_rakuten: Optional[float]
    eval_r_rakuten: Optional[float]
    eval_label_rakuten: str | None
    qty_matsui: float
    eval_pl_matsui: Optional[float]
    eval_label_matsui: str | None
    last_close: Optional[float]
    atr_14: Optional[float]
    slope_20: Optional[float]
    trend_daily: str | None


def _parse_ts(ts_str: str) -> Optional[timezone.datetime]:
    if not ts_str:
        return None
    try:
        dt = timezone.datetime.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


def _load_behavior_records(user_id: int) -> List[Record]:
    """
    /media/aiapp/behavior/latest_behavior.jsonl を読み込んで
    ログインユーザー分だけ Record リストにして返す。
    """
    behavior_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "behavior"
    latest_path = behavior_dir / "latest_behavior.jsonl"

    records: List[Record] = []
    if not latest_path.exists():
        return records

    try:
        text = latest_path.read_text(encoding="utf-8")
    except Exception:
        return records

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

        ts = _parse_ts(rec.get("ts") or "")
        if ts is None:
            continue

        ts_label = ts.strftime("%Y-%m-%d %H:%M:%S%z")
        mode = str(rec.get("mode") or "").lower()
        code = str(rec.get("code") or "")
        name = str(rec.get("name") or "")
        sector = rec.get("sector")
        if isinstance(sector, str) and sector.strip() == "":
            sector = None

        qty_rakuten = _to_float(rec.get("qty_rakuten")) or 0.0
        qty_matsui = _to_float(rec.get("qty_matsui")) or 0.0

        eval_pl_r = _to_float(rec.get("eval_pl_rakuten"))
        eval_pl_m = _to_float(rec.get("eval_pl_matsui"))
        eval_r_r = _to_float(rec.get("eval_r_rakuten"))
        eval_label_r = rec.get("eval_label_rakuten")
        eval_label_m = rec.get("eval_label_matsui")

        last_close = _to_float(rec.get("last_close"))
        atr_14 = _to_float(rec.get("atr_14"))
        slope_20 = _to_float(rec.get("slope_20"))
        trend_daily = rec.get("trend_daily")

        records.append(
            Record(
                raw=rec,
                ts=ts,
                ts_label=ts.strftime("%Y-%m-%d %H:%M"),
                mode=mode,
                code=code,
                name=name,
                sector=sector,
                qty_rakuten=qty_rakuten,
                eval_pl_rakuten=eval_pl_r,
                eval_r_rakuten=eval_r_r,
                eval_label_rakuten=eval_label_r,
                qty_matsui=qty_matsui,
                eval_pl_matsui=eval_pl_m,
                eval_label_matsui=eval_label_m,
                last_close=last_close,
                atr_14=atr_14,
                slope_20=slope_20,
                trend_daily=trend_daily,
            )
        )

    # ts 降順
    records.sort(key=lambda r: r.ts, reverse=True)
    return records


def _mode_counts(records: List[Record]) -> Dict[str, int]:
    out = {"live": 0, "demo": 0, "other": 0}
    for r in records:
        if r.mode == "live":
            out["live"] += 1
        elif r.mode == "demo":
            out["demo"] += 1
        else:
            out["other"] += 1
    return out


def _pl_counts(records: List[Record], broker: str) -> Dict[str, int]:
    out = {"win": 0, "lose": 0, "flat": 0, "no_position": 0, "none": 0}
    for r in records:
        if broker == "rakuten":
            label = r.eval_label_rakuten
            qty = r.qty_rakuten
        else:
            label = r.eval_label_matsui
            qty = r.qty_matsui

        if qty == 0:
            out["no_position"] += 1
            continue

        if not label:
            out["none"] += 1
        elif label == "win":
            out["win"] += 1
        elif label == "lose":
            out["lose"] += 1
        elif label == "flat":
            out["flat"] += 1
        else:
            out["none"] += 1
    return out


def _sector_stats(records: List[Record]) -> List[Dict[str, Any]]:
    stat_map: Dict[str, Dict[str, Any]] = {}

    for r in records:
        if r.qty_rakuten <= 0:
            continue
        label = r.eval_label_rakuten
        if label not in ("win", "lose", "flat"):
            continue

        name = r.sector or "(未分類)"
        s = stat_map.setdefault(name, {"name": name, "wins": 0, "trials": 0})
        s["trials"] += 1
        if label == "win":
            s["wins"] += 1

    stats = list(stat_map.values())
    for s in stats:
        trials = s["trials"]
        if trials > 0:
            s["win_rate"] = s["wins"] * 100.0 / trials
        else:
            s["win_rate"] = 0.0

    # 試行数→勝率の順でソート
    stats.sort(key=lambda x: (-x["trials"], -x["win_rate"], x["name"]))
    return stats


def _make_distribution(
    records: List[Record],
    bucket_func,
) -> List[Dict[str, Any]]:
    buckets: Dict[str, int] = {}
    total = 0
    for r in records:
        if r.qty_rakuten <= 0:
            continue
        label = r.eval_label_rakuten
        if label not in ("win", "lose", "flat"):
            continue

        name = bucket_func(r)
        buckets[name] = buckets.get(name, 0) + 1
        total += 1

    result: List[Dict[str, Any]] = []
    for name, count in buckets.items():
        pct = (count * 100.0 / total) if total > 0 else 0.0
        result.append({"name": name, "count": count, "pct": pct})

    result.sort(key=lambda x: -x["count"])
    return result


def _trend_bucket(r: Record) -> str:
    t = (r.trend_daily or "").lower()
    if t == "up":
        return "上昇トレンド"
    if t == "down":
        return "下降トレンド"
    if t in ("flat", "range"):
        return "レンジ"
    return "不明"


def _time_bucket(r: Record) -> str:
    h = r.ts.hour
    m = r.ts.minute
    total_min = h * 60 + m
    # 日本株用ざっくりバケット
    if total_min < 9 * 60 + 30:
        return "寄り前〜寄り"
    if total_min < 11 * 60:
        return "前場"
    if total_min < 13 * 60:
        return "昼休み〜後場寄り"
    if total_min < 15 * 60:
        return "後場"
    return "引け前〜引け後"


def _atr_bucket(r: Record) -> str:
    if r.atr_14 is None or r.last_close in (None, 0):
        return "不明"
    atr_pct = abs(r.atr_14) * 100.0 / r.last_close
    if atr_pct < 1:
        return "〜1％"
    if atr_pct < 2:
        return "1〜2％"
    if atr_pct < 3:
        return "2〜3％"
    return "3％以上"


def _slope_bucket(r: Record) -> str:
    if r.slope_20 is None:
        return "不明"
    s = r.slope_20
    if s <= -3:
        return "強い下落"
    if s < 0:
        return "弱い下落"
    if s < 3:
        return "弱い上昇"
    return "強い上昇"


def _top_trades(records: List[Record], n: int = 5) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    valid = [r for r in records if r.qty_rakuten > 0 and r.eval_pl_rakuten is not None]
    wins = [r for r in valid if r.eval_pl_rakuten > 0]
    loses = [r for r in valid if r.eval_pl_rakuten < 0]

    wins.sort(key=lambda r: r.eval_pl_rakuten, reverse=True)
    loses.sort(key=lambda r: r.eval_pl_rakuten)

    def _serialize(r: Record) -> Dict[str, Any]:
        return {
            "code": r.code,
            "name": r.name,
            "pl": r.eval_pl_rakuten or 0.0,
            "mode": "LIVE" if r.mode == "live" else "DEMO",
            "ts_label": r.ts_label,
        }

    top_win = [_serialize(r) for r in wins[:n]]
    top_lose = [_serialize(r) for r in loses[:n]]
    return top_win, top_lose


def _kpi_metrics(records: List[Record]) -> Dict[str, Optional[float]]:
    """
    勝率・平均R・平均利益・平均損失（すべて楽天ベース）
    """
    trades = [
        r for r in records
        if r.qty_rakuten > 0 and r.eval_label_rakuten in ("win", "lose", "flat")
    ]
    if not trades:
        return {
            "kpi_win_rate": None,
            "kpi_avg_r": None,
            "kpi_avg_pl_win": None,
            "kpi_avg_pl_lose": None,
        }

    wins = [r for r in trades if r.eval_label_rakuten == "win"]
    loses = [r for r in trades if r.eval_label_rakuten == "lose"]

    # 勝率
    win_rate = (len(wins) * 100.0 / len(trades)) if trades else None

    # 平均R
    r_values: List[float] = []
    for r in trades:
        if r.eval_r_rakuten is not None:
            r_values.append(float(r.eval_r_rakuten))
        else:
            # eval_r が無ければ PL / 想定損失 で代用
            est_loss = _to_float(r.raw.get("est_loss_rakuten"))
            if est_loss and est_loss != 0 and r.eval_pl_rakuten is not None:
                r_values.append(r.eval_pl_rakuten / est_loss)
    avg_r = statistics.mean(r_values) if r_values else None

    # 平均利益 / 損失（円）
    win_pls = [r.eval_pl_rakuten for r in wins if r.eval_pl_rakuten is not None]
    lose_pls = [r.eval_pl_rakuten for r in loses if r.eval_pl_rakuten is not None]

    avg_pl_win = statistics.mean(win_pls) if win_pls else None
    avg_pl_lose = statistics.mean(lose_pls) if lose_pls else None

    return {
        "kpi_win_rate": win_rate,
        "kpi_avg_r": avg_r,
        "kpi_avg_pl_win": avg_pl_win,
        "kpi_avg_pl_lose": avg_pl_lose,
    }


def _build_insights(records: List[Record], sector_stats: List[Dict[str, Any]]) -> List[str]:
    insights: List[str] = []
    if not records:
        return insights

    kpi = _kpi_metrics(records)
    win_rate = kpi["kpi_win_rate"]
    if win_rate is not None:
        insights.append(f"直近のトレード勝率はおよそ {win_rate:.1f}％ です。")

    if sector_stats:
        best = max(sector_stats, key=lambda s: s["win_rate"])
        insights.append(
            f"現時点では「{best['name']}」が比較的得意なセクターです（勝率 {best['win_rate']:.1f}％）。"
        )

    # トレンド傾向
    t_stats = _make_distribution(records, _trend_bucket)
    up = next((x for x in t_stats if x["name"] == "上昇トレンド"), None)
    if up and up["pct"] >= 60:
        insights.append("上昇トレンド銘柄へのエントリー比率が高めです。")

    # ATR（ボラティリティ）
    a_stats = _make_distribution(records, _atr_bucket)
    mid = next((x for x in a_stats if x["name"] == "1〜2％"), None)
    if mid and mid["pct"] >= 40:
        insights.append("ATR 1〜2％帯の中程度ボラ銘柄を中心に取引しています。")

    if not insights:
        insights.append("まだデータが少ないため、今後のトレードが増えるとより詳しい傾向を表示できます。")

    return insights


@login_required
def behavior_dashboard(request):
    user = request.user
    records = _load_behavior_records(user.id)

    if not records:
        ctx = {
            "has_data": False,
        }
        return render(request, "aiapp/behavior_dashboard.html", ctx)

    total = len(records)
    mode_counts = _mode_counts(records)
    pl_counts_r = _pl_counts(records, "rakuten")
    pl_counts_m = _pl_counts(records, "matsui")

    sector_stats = _sector_stats(records)
    trend_stats = _make_distribution(records, _trend_bucket)
    time_stats = _make_distribution(records, _time_bucket)
    atr_stats = _make_distribution(records, _atr_bucket)
    slope_stats = _make_distribution(records, _slope_bucket)

    top_win, top_lose = _top_trades(records, n=5)
    kpi = _kpi_metrics(records)
    insights = _build_insights(records, sector_stats)

    ctx = {
        "has_data": True,
        "total": total,
        "mode_counts": mode_counts,
        "pl_counts_r": pl_counts_r,
        "pl_counts_m": pl_counts_m,
        "sector_stats": sector_stats,
        "trend_stats": trend_stats,
        "time_stats": time_stats,
        "atr_stats": atr_stats,
        "slope_stats": slope_stats,
        "top_win": top_win,
        "top_lose": top_lose,
        "insights": insights,
        # KPI 4つ
        "kpi_win_rate": kpi["kpi_win_rate"],
        "kpi_avg_r": kpi["kpi_avg_r"],
        "kpi_avg_pl_win": kpi["kpi_avg_pl_win"],
        "kpi_avg_pl_lose": kpi["kpi_avg_pl_lose"],
    }
    return render(request, "aiapp/behavior_dashboard.html", ctx)