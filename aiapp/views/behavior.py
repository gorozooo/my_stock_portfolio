from __future__ import annotations

import os
import glob
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
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


def _count_simulate_files() -> int:
    """
    media/aiapp/simulate/sim_orders_*.jsonl の数（表示用）
    """
    try:
        base = getattr(settings, "MEDIA_ROOT", "")
        ptn = os.path.join(base, "aiapp", "simulate", "sim_orders_*.jsonl")
        return len(glob.glob(ptn))
    except Exception:
        return 0


def _load_behavior_model(user_id: int) -> Dict[str, Any]:
    """
    media/aiapp/behavior/model/latest_behavior_model_u{user}.json を読んで表示用に整形
    """
    base = getattr(settings, "MEDIA_ROOT", "")
    path = os.path.join(base, "aiapp", "behavior", "model", f"latest_behavior_model_u{user_id}.json")
    if not os.path.exists(path):
        return {
            "has_model": False,
            "total_trades": None,
            "wins": None,
            "win_rate": None,
            "avg_pl": None,
            "avg_r": None,
            "updated_at": None,
        }

    try:
        j = json.loads(open(path, "r", encoding="utf-8").read())
        return {
            "has_model": True,
            "total_trades": j.get("total_trades"),
            "wins": j.get("wins"),
            "win_rate": j.get("win_rate"),
            "avg_pl": j.get("avg_pl"),
            "avg_r": j.get("avg_r"),
            "updated_at": j.get("updated_at") or j.get("generated_at") or None,
        }
    except Exception:
        return {
            "has_model": False,
            "total_trades": None,
            "wins": None,
            "win_rate": None,
            "avg_pl": None,
            "avg_r": None,
            "updated_at": None,
        }


def _understanding_level(wl_total: int) -> str:
    """
    データ量から “理解度” をざっくり出す（表示用）
    """
    if wl_total >= 40:
        return "HIGH"
    if wl_total >= 15:
        return "MID"
    return "LOW"


def _build_hypotheses(
    wl_total: int,
    win_rate: Optional[float],
    avg_r: Optional[float],
    avg_win: Optional[float],
    avg_loss: Optional[float],
    best_sector_name: Optional[str],
) -> List[Dict[str, str]]:
    """
    “奇抜/革新的”寄りに、でも実務に刺さる仮説を出す
    """
    hs: List[Dict[str, str]] = []

    # 仮説01（核）
    if wl_total < 5 or win_rate is None:
        hs.append({"level": "SEED", "text": "私は“あなたの型”をまだ作成中。まずは PRO の評価付きログを増やし、勝ち筋と負け筋を分離する。"})
    else:
        hs.append({"level": "CORE", "text": "私は“あなたの型”を作り始めた。次は《再現できる勝ち方》だけを残していく。"})

    # 仮説02（命中）
    if win_rate is None:
        hs.append({"level": "SEED", "text": "命中率はまだ測定不能。評価付きの win/lose を貯め、条件のズレ（銘柄選別 / 入口 / ロット）を切り分ける。"})
    else:
        if win_rate < 45:
            hs.append({"level": "RISK", "text": "命中がまだ低い。選別ロジックが強すぎるか、刺さる条件がズレている。まずは “刺さる市場” を限定して勝ち筋を固定する。"})
        elif win_rate < 60:
            hs.append({"level": "MID", "text": "命中は中間。ここから伸ばすには『同じ勝ち方を反復できる条件』の固定が効く。"})
        else:
            hs.append({"level": "EDGE", "text": "命中は高い。次は “負け方の単純化（損切りの統一）” で R を底上げする。"})
    # 仮説03（R）
    if avg_r is None:
        hs.append({"level": "SEED", "text": "Rはまだ未知。Rが取れるログだけで再学習し、“勝ちの大きさ/負けの深さ” を数字で固定する。"})
    else:
        if avg_r < 0:
            hs.append({"level": "RISK", "text": "平均Rがマイナス。負けがルール想定より深い。ロット / 滑り / 我慢のどれかが混ざっている。"})
        elif avg_r < 0.5:
            hs.append({"level": "MID", "text": "平均Rは薄いプラス。勝ちを伸ばすより先に、負けの形を1パターンに固定すると伸びやすい。"})
        else:
            hs.append({"level": "EDGE", "text": "平均Rは強い。次は『同条件で勝てる回数』を増やすフェーズに入れる。"})
    # 仮説04（セクター）
    if best_sector_name:
        hs.append({"level": "MAP", "text": f"得意地形は《{best_sector_name}》。ここを“母艦”にして、別セクターは偵察（小ロット）で拡張する。"})
    else:
        hs.append({"level": "MAP", "text": "得意地形はまだ確定していない。まずは勝ちが出た条件を “地図化” して固定する。"})

    return hs[:5]


# =========================
# view
# =========================

@login_required
def behavior_dashboard(request: HttpRequest) -> HttpResponse:
    """
    ✅ VirtualTrade（DB）を正とする（JSONL依存しない）
    - PRO一択
    - PRO公式評価（replay.pro.last_eval + eval_exit_reason）を表示に使う
    """
    user = request.user
    today = timezone.localdate()

    # 直近の範囲（重くなったら調整）
    date_min = today - timezone.timedelta(days=180)

    qs = (
        VirtualTrade.objects
        .filter(user_id=user.id)
        .filter(trade_date__gte=date_min, trade_date__lte=today)
        .filter(qty_pro__gt=0)
        .order_by("-opened_at", "-id")
    )
    rows_vt = list(qs[:3000])

    if not rows_vt:
        return render(request, "aiapp/behavior_dashboard.html", {"has_data": False})

    # 「評価が1件も無い（=まだ ai_sim_eval/sync してない）」なら空扱い
    any_eval = any((str(getattr(v, "eval_exit_reason", "") or "").strip() != "") for v in rows_vt)
    if not any_eval:
        return render(request, "aiapp/behavior_dashboard.html", {"has_data": False})

    simulate_files = _count_simulate_files()
    behavior_model = _load_behavior_model(user.id)
    model_ready = bool(behavior_model.get("has_model"))

    # ---------- モード別件数 ----------
    mode_counts = Counter()
    for v in rows_vt:
        mode_counts[_infer_mode_from_replay(v.replay)] += 1

    # ---------- 集計 ----------
    pl_counts_pro = Counter()
    all_trades: List[TradeSide] = []

    sector_trials: Dict[str, int] = defaultdict(int)
    sector_wins: Dict[str, int] = defaultdict(int)

    for v in rows_vt:
        replay = v.replay if isinstance(v.replay, dict) else {}
        last_eval = _get_pro_last_eval(replay)

        exit_reason = (str(getattr(v, "eval_exit_reason", "") or "") or str(last_eval.get("exit_reason") or "")).strip().lower()
        if exit_reason not in ("hit_tp", "hit_sl", "time_stop", "no_position", "carry"):
            continue

        mode = _infer_mode_from_replay(replay)

        pl_pro = _safe_float(last_eval.get("pl_pro"))
        label = _label_from_exit_reason(exit_reason, pl_pro)

        # 勝敗サマリ
        if label in ("win", "lose", "flat"):
            pl_counts_pro[label] += 1
        elif exit_reason == "no_position":
            pl_counts_pro["no_position"] += 1

        # ここから先は “勝敗が付いたもの” だけ
        if label in ("win", "lose", "flat"):
            r_val = _safe_float(last_eval.get("eval_r_pro")) or _safe_float(last_eval.get("r")) or _safe_float(last_eval.get("r_plan"))

            ts_dt = getattr(v, "eval_exit_ts", None) or getattr(v, "eval_entry_ts", None) or getattr(v, "opened_at", None)
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

            sector = str(getattr(v, "sector", "") or replay.get("sector") or "(未分類)")
            sector_trials[sector] += 1
            if label == "win":
                sector_wins[sector] += 1

    win_trades = [t for t in all_trades if t.label == "win"]
    lose_trades = [t for t in all_trades if t.label == "lose"]
    flat_trades = [t for t in all_trades if t.label == "flat"]

    wl_total = len(win_trades) + len(lose_trades)
    wl_win = len(win_trades)
    wl_lose = len(lose_trades)

    win_rate_all: Optional[float] = None
    if wl_total > 0:
        win_rate_all = wl_win / wl_total * 100.0

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

    # best sector
    best_sector_name: Optional[str] = None
    best_rate = -1.0
    for sec, trials in sector_trials.items():
        if trials < 2:
            continue
        wins = sector_wins.get(sec, 0)
        rate = (wins / trials) * 100.0 if trials > 0 else 0.0
        if rate > best_rate:
            best_rate = rate
            best_sector_name = sec

    understanding = _understanding_level(wl_total)
    hypotheses = _build_hypotheses(
        wl_total=wl_total,
        win_rate=win_rate_all,
        avg_r=avg_r,
        avg_win=avg_win,
        avg_loss=avg_loss,
        best_sector_name=best_sector_name,
    )

    # TOP
    top_win: List[Dict[str, Any]] = []
    for t in sorted(win_trades, key=lambda x: x.pl, reverse=True)[:5]:
        top_win.append({"code": t.code, "name": t.name, "pl": t.pl, "mode": t.mode, "ts_label": t.ts_label})

    top_lose: List[Dict[str, Any]] = []
    for t in sorted(lose_trades, key=lambda x: x.pl)[:5]:
        top_lose.append({"code": t.code, "name": t.name, "pl": t.pl, "mode": t.mode, "ts_label": t.ts_label})

    ctx = {
        "has_data": True,
        "today": str(today),
        "simulate_files": simulate_files,
        "model_ready": model_ready,
        "understanding": understanding,
        "hypotheses": hypotheses,

        "total": len(rows_vt),
        "mode_counts": {
            "live": mode_counts.get("live", 0),
            "demo": mode_counts.get("demo", 0),
            "other": mode_counts.get("other", 0),
        },

        "wl_total": wl_total,
        "wl_win": wl_win,
        "wl_lose": wl_lose,

        "kpi_win_rate": win_rate_all or 0.0,
        "kpi_avg_r": avg_r or 0.0,

        "best_sector_name": best_sector_name,

        "behavior_model": behavior_model,
        "top_win": top_win,
        "top_lose": top_lose,
    }
    return render(request, "aiapp/behavior_dashboard.html", ctx)