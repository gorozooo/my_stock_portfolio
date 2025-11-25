# aiapp/views/behavior.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone


Number = float | int


# ------------------------------------------------------------
# ユーティリティ
# ------------------------------------------------------------
def _to_float(v: Any) -> float | None:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _load_behavior_rows(user_id: int) -> List[Dict[str, Any]]:
    """
    latest_behavior.jsonl から、ログインユーザーの行だけ読み込む。
    """
    behavior_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "behavior"
    path = behavior_dir / "latest_behavior.jsonl"

    if not path.exists():
        return []

    rows: List[Dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []

    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except Exception:
            continue

        if rec.get("user_id") != user_id:
            continue

        # ts → ラベル
        ts_str = rec.get("ts")
        ts_label = ""
        if isinstance(ts_str, str) and ts_str:
            try:
                dt = timezone.datetime.fromisoformat(ts_str)
                if timezone.is_naive(dt):
                    dt = timezone.make_aware(dt, timezone.get_default_timezone())
                dt = timezone.localtime(dt)
                ts_label = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                ts_label = ts_str
        rec["ts_label"] = ts_label

        rows.append(rec)

    return rows


def _dedupe_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    同じ銘柄・モード・日付・エントリー・数量の完全重複を除外する。
    （同じ日に同じ銘柄を何度もテストしても、1件として扱う）
    """
    seen: set[Tuple[Any, ...]] = set()
    unique: List[Dict[str, Any]] = []

    for r in rows:
        code = r.get("code")
        mode = r.get("mode")
        price_date = r.get("price_date")
        entry = _to_float(r.get("entry"))
        qty_r = _to_float(r.get("qty_rakuten")) or 0.0
        qty_m = _to_float(r.get("qty_matsui")) or 0.0

        # entry は小数点2桁で丸めてキーにする
        entry_key = round(entry, 2) if entry is not None else None

        key = (code, mode, price_date, entry_key, qty_r, qty_m)
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    return unique


def _count_pl_label(rec: Dict[str, Any], broker: str) -> str:
    """
    eval_label_{broker} を win/lose/flat/no_position/none のいずれかに正規化。
    """
    label = rec.get(f"eval_label_{broker}")
    if label in ("win", "lose", "flat", "no_position"):
        return label
    # qty が 0 なら no_position 扱い
    qty = _to_float(rec.get(f"qty_{broker}")) or 0.0
    if qty == 0:
        return "no_position"
    return "none"


def _make_dist(items: List[Tuple[str, int]], total: int) -> List[Dict[str, Any]]:
    """
    [("上昇トレンド", 10), ...] から
    [{"name": "...", "count": 10, "pct": 76.9}, ...] を生成。
    """
    if total <= 0:
        total = 1
    out: List[Dict[str, Any]] = []
    for name, cnt in items:
        pct = (cnt / total) * 100.0
        out.append({"name": name, "count": cnt, "pct": pct})
    return out


# ------------------------------------------------------------
# メインビュー
# ------------------------------------------------------------
@login_required
def behavior_dashboard(request: HttpRequest) -> HttpResponse:
    user = request.user

    # 1) 行動データ読み込み ＋ 重複除去
    rows_raw = _load_behavior_rows(user.id)
    rows = _dedupe_rows(rows_raw)

    has_data = len(rows) > 0
    if not has_data:
        ctx = {"has_data": False}
        return render(request, "aiapp/behavior_dashboard.html", ctx)

    total = len(rows)

    # --------------------------------------------------------
    # モード内訳
    # --------------------------------------------------------
    mode_counts = {"live": 0, "demo": 0, "other": 0}
    for r in rows:
        m = (r.get("mode") or "").lower()
        if m == "live":
            mode_counts["live"] += 1
        elif m == "demo":
            mode_counts["demo"] += 1
        else:
            mode_counts["other"] += 1

    # --------------------------------------------------------
    # 勝敗カウント（楽天 / 松井 それぞれ）
    # --------------------------------------------------------
    def _init_pl_counts() -> Dict[str, int]:
        return {"win": 0, "lose": 0, "flat": 0, "no_position": 0, "none": 0}

    pl_counts_r = _init_pl_counts()
    pl_counts_m = _init_pl_counts()

    for r in rows:
        lbl_r = _count_pl_label(r, "rakuten")
        pl_counts_r[lbl_r] += 1
        lbl_m = _count_pl_label(r, "matsui")
        pl_counts_m[lbl_m] += 1

    # --------------------------------------------------------
    # KPI 用：勝率（全体）・平均R・平均利益・平均損失
    # --------------------------------------------------------
    win_trades = 0
    lose_trades = 0
    flat_trades = 0

    win_pl_list: List[float] = []
    lose_pl_list: List[float] = []
    r_trade_list: List[float] = []

    for r in rows:
        # 1トレードの「合算PL」
        pl_r = _to_float(r.get("eval_pl_rakuten")) or 0.0
        pl_m = _to_float(r.get("eval_pl_matsui")) or 0.0
        qty_r = _to_float(r.get("qty_rakuten")) or 0.0
        qty_m = _to_float(r.get("qty_matsui")) or 0.0

        if qty_r == 0 and qty_m == 0:
            # どちらの口座でもポジションを取っていない
            continue

        total_pl = pl_r + pl_m

        if total_pl > 0:
            win_trades += 1
            win_pl_list.append(total_pl)
        elif total_pl < 0:
            lose_trades += 1
            lose_pl_list.append(total_pl)
        else:
            flat_trades += 1

        # R 値（1トレード分）
        r_parts: List[float] = []
        for broker in ("rakuten", "matsui"):
            qty = _to_float(r.get(f"qty_{broker}")) or 0.0
            if qty == 0:
                continue
            rv = _to_float(r.get(f"eval_r_{broker}"))
            if rv is None:
                continue
            r_parts.append(rv)

        if r_parts:
            r_trade = sum(r_parts) / len(r_parts)
            r_trade_list.append(r_trade)

    # 勝率（win / (win+lose)）
    denom = win_trades + lose_trades
    if denom > 0:
        win_rate_overall = (win_trades / denom) * 100.0
    else:
        win_rate_overall = None

    # 平均R
    if r_trade_list:
        avg_r = sum(r_trade_list) / len(r_trade_list)
    else:
        avg_r = None

    # 平均利益（勝ちのみ）
    if win_pl_list:
        avg_profit = sum(win_pl_list) / len(win_pl_list)
    else:
        avg_profit = None

    # 平均損失（負けのみ）
    if lose_pl_list:
        avg_loss = sum(lose_pl_list) / len(lose_pl_list)
    else:
        avg_loss = None

    # --------------------------------------------------------
    # セクター別勝率（楽天）
    # --------------------------------------------------------
    sector_map: Dict[str, Dict[str, Any]] = {}

    for r in rows:
        sector = r.get("sector") or "(未分類)"
        s = sector_map.setdefault(sector, {"name": sector, "wins": 0, "trials": 0})
        lbl = _count_pl_label(r, "rakuten")
        if lbl in ("win", "lose"):
            s["trials"] += 1
            if lbl == "win":
                s["wins"] += 1

    sector_stats: List[Dict[str, Any]] = []
    for sec, s in sector_map.items():
        trials = s["trials"]
        wins = s["wins"]
        if trials > 0:
            win_rate = (wins / trials) * 100.0
        else:
            win_rate = 0.0
        sector_stats.append(
            {"name": sec, "wins": wins, "trials": trials, "win_rate": win_rate}
        )

    # 表示順：試行数 ↓
    sector_stats.sort(key=lambda x: x["trials"], reverse=True)

    # --------------------------------------------------------
    # 相性マップ用の分布
    #  trend_daily / 時間帯 / ATR / slope などを簡易集計
    # --------------------------------------------------------
    # トレンド
    trend_counter = {"up": 0, "flat": 0, "down": 0, "other": 0}
    for r in rows:
        t = (r.get("trend_daily") or "").lower()
        if t in trend_counter:
            trend_counter[t] += 1
        else:
            trend_counter["other"] += 1
    trend_stats = _make_dist(
        [
            ("上昇トレンド", trend_counter["up"]),
            ("もみ合い", trend_counter["flat"]),
            ("下降トレンド", trend_counter["down"]),
        ],
        total=sum(trend_counter.values()),
    )

    # 時間帯（ざっくり：寄り〜11時 / 11〜14時 / 14時〜）
    time_counter = {"am_open": 0, "mid": 0, "pm": 0}
    for r in rows:
        ts_str = r.get("ts")
        if not isinstance(ts_str, str) or not ts_str:
            continue
        try:
            dt = timezone.datetime.fromisoformat(ts_str)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_default_timezone())
            dt = timezone.localtime(dt)
            h = dt.hour
        except Exception:
            continue
        if 9 <= h < 11:
            time_counter["am_open"] += 1
        elif 11 <= h < 14:
            time_counter["mid"] += 1
        else:
            time_counter["pm"] += 1
    time_stats = _make_dist(
        [
            ("寄り〜11時", time_counter["am_open"]),
            ("11〜14時", time_counter["mid"]),
            ("14時以降", time_counter["pm"]),
        ],
        total=sum(time_counter.values()) or 1,
    )

    # ATR 分布（1〜2%, 2〜3%, 3%以上）
    atr_counter = {"1_2": 0, "2_3": 0, "over3": 0}
    for r in rows:
        atr = _to_float(r.get("atr_14"))
        last_close = _to_float(r.get("last_close"))
        if atr is None or last_close in (None, 0):
            continue
        atr_pct = (atr / last_close) * 100.0
        if atr_pct < 2:
            atr_counter["1_2"] += 1
        elif atr_pct < 3:
            atr_counter["2_3"] += 1
        else:
            atr_counter["over3"] += 1
    atr_stats = _make_dist(
        [
            ("ATR 〜2%", atr_counter["1_2"]),
            ("ATR 2〜3%", atr_counter["2_3"]),
            ("ATR 3%以上", atr_counter["over3"]),
        ],
        total=sum(atr_counter.values()) or 1,
    )

    # slope 分布（弱め / 普通 / 強め）
    slope_counter = {"weak": 0, "mid": 0, "strong": 0}
    for r in rows:
        slope = _to_float(r.get("slope_20"))
        if slope is None:
            continue
        abs_s = abs(slope)
        if abs_s < 3:
            slope_counter["weak"] += 1
        elif abs_s < 8:
            slope_counter["mid"] += 1
        else:
            slope_counter["strong"] += 1
    slope_stats = _make_dist(
        [
            ("傾き 小さめ", slope_counter["weak"]),
            ("傾き 普通", slope_counter["mid"]),
            ("傾き 大きめ", slope_counter["strong"]),
        ],
        total=sum(slope_counter.values()) or 1,
    )

    # --------------------------------------------------------
    # TOP 勝ち / 負け（楽天ベース）
    # --------------------------------------------------------
    def _collect_top(kind: str, limit: int = 5) -> List[Dict[str, Any]]:
        tmp: List[Dict[str, Any]] = []
        for r in rows:
            pl = _to_float(r.get("eval_pl_rakuten"))
            if pl is None:
                continue
            if kind == "win" and pl <= 0:
                continue
            if kind == "lose" and pl >= 0:
                continue
            tmp.append({"code": r.get("code"), "name": r.get("name"), "pl": pl,
                        "mode": r.get("mode"), "ts_label": r.get("ts_label")})
        reverse = True if kind == "win" else False
        tmp.sort(key=lambda x: x["pl"], reverse=reverse)
        return tmp[:limit]

    top_win = _collect_top("win")
    top_lose = _collect_top("lose")

    # --------------------------------------------------------
    # AI インサイトテキスト（C 濃度：そこそこ丁寧）
    # --------------------------------------------------------
    # 勝率テキスト
    if win_rate_overall is not None:
        win_rate_txt = f"直近のトレード勝率はおおよそ {win_rate_overall:.1f}% です（対象 {win_trades + lose_trades} トレード）。"
    else:
        win_rate_txt = "まだ勝敗の付いたトレードが少ないため、勝率の傾向ははっきりしていません。"

    # セクターで目立つところ
    sector_insights: List[str] = []
    if sector_stats:
        # 試行数が多い順に並んでいる
        sec_best = max(sector_stats, key=lambda x: x["win_rate"])  # type: ignore[arg-type]
        sec_worst = min(sector_stats, key=lambda x: x["win_rate"])  # type: ignore[arg-type]
        if sec_best["trials"] >= 2:
            sector_insights.append(
                f"現状では「{sec_best['name']}」が比較的得意で、勝率は {sec_best['win_rate']:.1f}% "
                f"（{sec_best['trials']} 件中 {sec_best['wins']} 件勝ち）になっています。"
            )
        if sec_worst["trials"] >= 2 and sec_worst["win_rate"] < sec_best["win_rate"]:
            sector_insights.append(
                f"一方で「{sec_worst['name']}」はサンプルが少ないか、やや相性が悪い傾向がありそうです "
                f"（勝率 {sec_worst['win_rate']:.1f}% / {sec_worst['trials']} 件）。"
            )

    # 利益・損失テキスト
    pl_txt_parts: List[str] = []
    if avg_profit is not None:
        pl_txt_parts.append(f"1トレードあたりの平均利益は約 {avg_profit:,.0f} 円")
    if avg_loss is not None:
        pl_txt_parts.append(f"平均損失は約 {avg_loss:,.0f} 円")
    pl_txt = "、".join(pl_txt_parts) if pl_txt_parts else ""

    insights_lines: List[str] = [win_rate_txt]
    insights_lines.extend(sector_insights)
    if pl_txt:
        insights_lines.append(
            pl_txt + " いまは損失側の方がやや大きいように見えるため、損切り幅やロット調整の余地がありそうです。"
        )

    insight_text = "\n".join(insights_lines)

    # --------------------------------------------------------
    # コンテキスト
    # --------------------------------------------------------
    ctx = {
        "has_data": True,
        "total": total,
        "mode_counts": mode_counts,
        "pl_counts_r": pl_counts_r,
        "pl_counts_m": pl_counts_m,
        "win_rate_overall": win_rate_overall,
        "avg_r": avg_r,
        "avg_profit": avg_profit,
        "avg_loss": avg_loss,
        "sector_stats": sector_stats,
        "trend_stats": trend_stats,
        "time_stats": time_stats,
        "atr_stats": atr_stats,
        "slope_stats": slope_stats,
        "top_win": top_win,
        "top_lose": top_lose,
        "insight_text": insight_text,
    }

    return render(request, "aiapp/behavior_dashboard.html", ctx)