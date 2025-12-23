# aiapp/views/behavior.py
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade


# =========================
# dataclass
# =========================

@dataclass
class TradeSide:
    broker: str          # "PRO"
    pl: float            # 損益（円）
    r: Optional[float]   # R（あれば）
    label: str           # "win" / "lose" / "flat"
    ts: str              # 元の ts 文字列
    ts_label: str        # 表示用 ts
    code: str
    name: str
    mode: str            # live / demo / other


# =========================
# helpers
# =========================

def _safe_float(v: Any) -> Optional[float]:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _parse_dt_to_local(ts_str: str) -> Optional[timezone.datetime]:
    if not ts_str:
        return None
    try:
        dt = timezone.datetime.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


def _make_ts_label(ts_str: str) -> str:
    dt = _parse_dt_to_local(ts_str)
    if dt is None:
        return ts_str or ""
    return dt.strftime("%Y-%m-%d %H:%M")


def _bucket_time_of_day_from_dt(dt: Optional[timezone.datetime]) -> str:
    if dt is None:
        return "その他"
    dt = timezone.localtime(dt)
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


def _infer_mode_from_replay(replay: Any) -> str:
    """
    mode は DBに専用カラムが無いことが多いので replay から拾う。
    無ければ other。
    """
    try:
        if isinstance(replay, dict):
            m = (replay.get("mode") or replay.get("sim_mode") or "").strip().lower()
            if m in ("live", "demo"):
                return m
    except Exception:
        pass
    return "other"


def _get_pro_last_eval(replay: Any) -> Dict[str, Any]:
    """
    ai_sim_eval が入れる replay.pro.last_eval を拾う。
    """
    if not isinstance(replay, dict):
        return {}
    pro = replay.get("pro")
    if not isinstance(pro, dict):
        return {}
    last_eval = pro.get("last_eval")
    if not isinstance(last_eval, dict):
        return {}
    return last_eval


def _label_from_exit_reason(exit_reason: str, pl_pro: Optional[float]) -> str:
    """
    PRO公式ラベル（win/lose/flat）をここで確定。
    """
    r = (exit_reason or "").strip().lower()
    if r in ("hit_tp",):
        return "win"
    if r in ("hit_sl",):
        return "lose"
    if r in ("time_stop",):
        if pl_pro is None:
            return "flat"
        if pl_pro > 0:
            return "win"
        if pl_pro < 0:
            return "lose"
        return "flat"
    return ""


def _build_insights(
    total_trades: int,
    win_rate_all: Optional[float],
    sector_stats: List[Dict[str, Any]],
    kpi_avg_win: Optional[float],
    kpi_avg_loss: Optional[float],
    dist_trend: Dict[str, int],
) -> List[str]:
    msgs: List[str] = []

    if total_trades >= 5 and win_rate_all is not None:
        msgs.append(f"直近のトレード勝率はおよそ {win_rate_all:.1f}% です（対象 {total_trades} トレード）。")

    best_sector: Optional[Tuple[str, float, int]] = None
    worst_sector: Optional[Tuple[str, float, int]] = None

    for s in sector_stats:
        trials = int(s.get("trials") or 0)
        wins = int(s.get("wins") or 0)
        if trials < 2:
            continue
        rate = (wins / trials) * 100 if trials > 0 else 0.0
        if best_sector is None or rate > best_sector[1]:
            best_sector = (str(s.get("name") or ""), rate, trials)
        if worst_sector is None or rate < worst_sector[1]:
            worst_sector = (str(s.get("name") or ""), rate, trials)

    if best_sector is not None:
        name, rate, trials = best_sector
        msgs.append(f"現状では「{name}」が比較的得意で、勝率は {rate:.1f}%（{trials} 件）になっています。")

    if worst_sector is not None and worst_sector != best_sector:
        name, rate, trials = worst_sector
        msgs.append(f"一方で「{name}」はまだサンプルが少ないか、やや相性が悪い傾向があります（勝率 {rate:.1f}%／{trials} 件）。")

    if kpi_avg_win is not None and kpi_avg_loss is not None:
        loss_abs = abs(kpi_avg_loss)
        msgs.append(
            f"1トレードあたりの平均利益は約 {kpi_avg_win:,.0f} 円、平均損失は約 {loss_abs:,.0f} 円です。"
            " いまは損失側の方がやや大きいため、損切り幅の見直しやロット調整の余地があります。"
        )

    if dist_trend:
        up_count = int(dist_trend.get("up", 0))
        total = int(sum(dist_trend.values()))
        if total > 0:
            up_pct = up_count / total * 100
            if up_pct >= 70:
                msgs.append(
                    "上昇トレンド銘柄へのエントリーが中心になっており、"
                    "押し目〜順張りパターンに強みが集まりつつあります。"
                )

    if not msgs:
        msgs.append(
            "まだデータ量が少ないため、はっきりしたクセは出ていません。"
            " AI Picks のシミュレを継続して貯めると、勝ちパターンと負けパターンがより明確になります。"
        )

    return msgs


# =========================
# view
# =========================

@login_required
def behavior_dashboard(request: HttpRequest) -> HttpResponse:
    """
    ✅ JSONL依存を廃止して、VirtualTrade（DB）を正とする。
    - PRO一択
    - PRO公式評価（replay.pro.last_eval / eval_exit_reason）を表示に使う
    """
    user = request.user
    today = timezone.localdate()

    # 直近の範囲（必要なら後で調整）
    date_min = today - timezone.timedelta(days=120)

    # PRO公式のみ（あなたの ai_sim_eval の方針に合わせる）
    qs = (
        VirtualTrade.objects
        .filter(user_id=user.id)
        .filter(trade_date__gte=date_min, trade_date__lte=today)
        .filter(qty_pro__gt=0)
        .filter(replay__pro__status="accepted")  # 公式記録
        .order_by("-opened_at", "-id")
    )

    rows_vt = list(qs[:2000])  # 念のため上限（重くなったらここ調整）

    if not rows_vt:
        return render(request, "aiapp/behavior_dashboard.html", {"has_data": False})

    # 「評価が1件も無い（=まだ ai_sim_eval してない）」なら空扱い
    any_eval = any((str(v.eval_exit_reason or "").strip() != "") for v in rows_vt)
    if not any_eval:
        return render(request, "aiapp/behavior_dashboard.html", {"has_data": False})

    # ---------- モード別件数 ----------
    mode_counts = Counter()
    for v in rows_vt:
        mode = _infer_mode_from_replay(v.replay)
        mode_counts[mode] += 1

    # ---------- PRO勝敗・KPI用 ----------
    pl_counts_pro = Counter()
    all_trades: List[TradeSide] = []

    sector_counter_trials: Dict[str, int] = defaultdict(int)
    sector_counter_win: Dict[str, int] = defaultdict(int)

    trend_counter: Dict[str, int] = defaultdict(int)
    time_counter: Dict[str, int] = defaultdict(int)
    atr_counter: Dict[str, int] = defaultdict(int)
    slope_counter: Dict[str, int] = defaultdict(int)

    for v in rows_vt:
        replay = v.replay if isinstance(v.replay, dict) else {}
        last_eval = _get_pro_last_eval(replay)

        exit_reason = (str(v.eval_exit_reason or "") or str(last_eval.get("exit_reason") or "")).strip().lower()

        # carry/no_position/time_stop/hit_tp/hit_sl 等
        # ここは「表示にデータがあるか」を決める要素でもあるので拾う
        if exit_reason in ("hit_tp", "hit_sl", "time_stop", "no_position", "carry"):
            pass
        else:
            # 未評価/不明はスキップ
            continue

        mode = _infer_mode_from_replay(replay)

        # pl_pro（円）は replay 側があればそれを優先（無ければ推定しない）
        pl_pro = _safe_float(last_eval.get("pl_pro"))
        if pl_pro is None:
            # DBに eval_exit_px 等があっても、ここは “公式値” 優先なので None のままにする
            pl_pro = None

        label = _label_from_exit_reason(exit_reason, pl_pro)

        # 件数カウント（勝敗サマリ）
        if label in ("win", "lose", "flat"):
            pl_counts_pro[label] += 1
        elif exit_reason == "no_position":
            pl_counts_pro["no_position"] += 1

        # KPI/TOP は勝敗が付いたものだけ
        if label in ("win", "lose", "flat"):
            # R（あれば）
            r_val = _safe_float(last_eval.get("r_plan"))  # A案メタに入る場合もあるが無ければNone
            # 違うキーで入れてる可能性もあるので広めに拾う
            if r_val is None:
                r_val = _safe_float(last_eval.get("r")) or _safe_float(last_eval.get("eval_r_pro"))

            ts_dt = v.eval_exit_ts or v.eval_entry_ts or v.opened_at
            ts_str = timezone.localtime(ts_dt).isoformat() if ts_dt else ""

            code = str(getattr(v, "code", "") or "")
            name = str(getattr(v, "name", "") or (replay.get("name") or ""))

            all_trades.append(
                TradeSide(
                    broker="PRO",
                    pl=float(pl_pro or 0.0),
                    r=r_val,
                    label=label,
                    ts=ts_str,
                    ts_label=_make_ts_label(ts_str),
                    code=code,
                    name=name,
                    mode=mode,
                )
            )

        # セクター（無ければ未分類）
        sector = str(getattr(v, "sector", "") or replay.get("sector") or "(未分類)")
        if label in ("win", "lose", "flat"):
            sector_counter_trials[sector] += 1
            if label == "win":
                sector_counter_win[sector] += 1

        # 相性マップ（雑に拾う：無ければ“不明”）
        trend_daily = str(replay.get("trend_daily") or replay.get("trend") or "不明")
        trend_counter[trend_daily] += 1

        ts_dt2 = v.eval_entry_ts or v.opened_at
        time_bucket = _bucket_time_of_day_from_dt(ts_dt2)
        time_counter[time_bucket] += 1

        atr = _safe_float(replay.get("atr_14") or replay.get("atr"))
        atr_counter[_bucket_atr(atr)] += 1

        slope = _safe_float(replay.get("slope_20") or replay.get("slope"))
        slope_counter[_bucket_slope(slope)] += 1

    # “評価が付いた勝敗トレード” が0なら空扱い（あなたの方針どおり）
    win_trades = [t for t in all_trades if t.label == "win"]
    lose_trades = [t for t in all_trades if t.label == "lose"]
    flat_trades = [t for t in all_trades if t.label == "flat"]

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

    # ---------- セクター別 ----------
    sector_stats: List[Dict[str, Any]] = []
    for sec, trials in sector_counter_trials.items():
        wins = sector_counter_win.get(sec, 0)
        win_rate = (wins / trials * 100.0) if trials > 0 else 0.0
        sector_stats.append({"name": sec, "trials": trials, "wins": wins, "win_rate": win_rate})
    sector_stats.sort(key=lambda x: (-int(x["trials"]), -float(x["win_rate"])))

    # ---------- 分布 ----------
    def _build_dist_list(counter: Dict[str, int]) -> List[Dict[str, Any]]:
        total_count = sum(counter.values()) or 1
        items: List[Dict[str, Any]] = []
        for name, c in counter.items():
            items.append({"name": name, "count": c, "pct": c / total_count * 100.0})
        items.sort(key=lambda x: -int(x["count"]))
        return items

    trend_stats = _build_dist_list(trend_counter)
    time_stats = _build_dist_list(time_counter)
    atr_stats = _build_dist_list(atr_counter)
    slope_stats = _build_dist_list(slope_counter)

    # ---------- TOP ----------
    top_win: List[Dict[str, Any]] = []
    for t in sorted(win_trades, key=lambda x: x.pl, reverse=True)[:5]:
        top_win.append({"code": t.code, "name": t.name, "pl": t.pl, "mode": t.mode, "ts_label": t.ts_label})

    top_lose: List[Dict[str, Any]] = []
    for t in sorted(lose_trades, key=lambda x: x.pl)[:5]:
        top_lose.append({"code": t.code, "name": t.name, "pl": t.pl, "mode": t.mode, "ts_label": t.ts_label})

    # ---------- インサイト ----------
    total_trades_for_insight = len(win_trades) + len(lose_trades) + len(flat_trades)
    insights = _build_insights(
        total_trades=total_trades_for_insight,
        win_rate_all=win_rate_all,
        sector_stats=sector_stats,
        kpi_avg_win=avg_win,
        kpi_avg_loss=avg_loss,
        dist_trend={k: v for k, v in trend_counter.items()},
    )
    if len(insights) > 5:
        insights = insights[:5]

    # ---------- 行動モデル（今回は “表示が出る” を最優先して、モデル表示は一旦オフ）
    # 既存テンプレの shape に合わせて空で返す（後で復活させればOK）
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

    ctx = {
        "has_data": True,
        "total": len(rows_vt),
        "mode_counts": {
            "live": mode_counts.get("live", 0),
            "demo": mode_counts.get("demo", 0),
            "other": mode_counts.get("other", 0),
        },
        "kpi_win_rate": win_rate_all,
        "kpi_avg_r": avg_r,
        "kpi_avg_win": avg_win,
        "kpi_avg_loss": avg_loss,
        "pl_counts_pro": pl_counts_pro,
        "sector_stats": sector_stats,
        "trend_stats": trend_stats,
        "time_stats": time_stats,
        "atr_stats": atr_stats,
        "slope_stats": slope_stats,
        "top_win": top_win,
        "top_lose": top_lose,
        "insights": insights,
        "behavior_model": behavior_model,
    }
    return render(request, "aiapp/behavior_dashboard.html", ctx)