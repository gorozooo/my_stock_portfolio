# aiapp/views/simulate.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from datetime import date as _date, datetime as _datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone


def _parse_ts(ts_str: Optional[str]) -> Optional[timezone.datetime]:
    """
    JSONL の ts(ISO文字列) を timezone-aware datetime に変換する。
    失敗した場合は None を返す。
    """
    if not isinstance(ts_str, str) or not ts_str:
        return None
    try:
        dt = _datetime.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


@login_required
def simulate_list(request: HttpRequest) -> HttpResponse:
    """
    AI Picks の「シミュレ」で登録した紙トレを一覧表示するビュー。

    - /media/aiapp/simulate/*.jsonl を全部読む
    - ログインユーザーの分だけ抽出
    - ts 降順でソート
    - モード / 年月日トグル or date パラメータ / 銘柄コード・名称でフィルタ
    - 1日分だけ表示（最大100件）

    ★ 重複除外仕様
      「同じ銘柄・同じ内容のシミュレは、同じ日付内で重複させない」
      → 同じ日・同じ code・同じ mode・同じエントリー/数量/想定PL・想定損失・TP・SL は
         最初の1件だけ残し、以降は一覧から除外する。

    ★ KPI
      - summary_today: 今日の全ログベース（フィルタと無関係）
      - summary_total: 全期間の全ログベース（通算成績）
    """

    user = request.user
    sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"

    # ---- フィルタ値（クエリパラメータ） ------------------------------
    # mode: all / live / demo （一覧表示用のフィルタ）
    mode = (request.GET.get("mode") or "all").lower()
    if mode not in ("all", "live", "demo"):
        mode = "all"

    # 年月日トグル用パラメータ
    y_param = (request.GET.get("y") or "").strip()
    m_param = (request.GET.get("m") or "").strip()
    d_param = (request.GET.get("d") or "").strip()

    # 旧 date パラメータがあれば一応サポート（y/m/d に分解）
    date_param = (request.GET.get("date") or "").strip()
    if date_param and not (y_param and m_param and d_param):
        try:
            tmp = _date.fromisoformat(date_param)
            y_param = y_param or str(tmp.year)
            m_param = m_param or str(tmp.month)
            d_param = d_param or str(tmp.day)
        except Exception:
            pass

    # q: 銘柄コード or 名称の部分一致（一覧表示用）
    q = (request.GET.get("q") or "").strip()

    # ---- JSONL 読み込み ------------------------------------------------
    entries_all: List[Dict[str, Any]] = []

    if sim_dir.exists():
        for path in sorted(sim_dir.glob("*.jsonl")):
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

                # ユーザー別に絞る
                if rec.get("user_id") != user.id:
                    continue

                # ts を datetime + 表示ラベルに整形
                ts_str = rec.get("ts")
                dt = _parse_ts(ts_str)
                if dt is not None:
                    rec["_dt"] = dt
                    rec["ts_label"] = dt.strftime("%Y/%m/%d %H:%M")
                else:
                    rec["_dt"] = None
                    rec["ts_label"] = ts_str or ""

                # エントリー時刻 / エグジット時刻（sim_eval_service で付与）
                entry_dt = _parse_ts(rec.get("eval_entry_ts"))
                exit_dt = _parse_ts(rec.get("eval_exit_ts"))

                if entry_dt is not None:
                    rec["entry_dt"] = entry_dt
                    rec["entry_label"] = entry_dt.strftime("%Y/%m/%d %H:%M")
                else:
                    rec["entry_dt"] = None
                    rec["entry_label"] = ""

                if exit_dt is not None:
                    rec["exit_dt"] = exit_dt
                    rec["exit_label"] = exit_dt.strftime("%Y/%m/%d %H:%M")
                else:
                    rec["exit_dt"] = None
                    rec["exit_label"] = ""

                # exit_reason のラベル（評価結果テキスト用）
                exit_reason = rec.get("eval_exit_reason") or ""
                exit_reason_label = ""
                if exit_reason == "hit_tp":
                    exit_reason_label = "利確"
                elif exit_reason == "hit_sl":
                    exit_reason_label = "損切"
                elif exit_reason == "horizon_close":
                    # タイムアップ → 持ち越し扱い
                    exit_reason_label = "持ち越し"
                elif exit_reason in ("no_touch", "no_fill"):
                    exit_reason_label = "指値に刺さらなかった"

                rec["exit_reason"] = exit_reason
                rec["exit_reason_label"] = exit_reason_label

                entries_all.append(rec)

    # ---- ts 降順でソート ----------------------------------------------
    def _sort_key(r: Dict[str, Any]):
        dt = r.get("_dt")
        if isinstance(dt, timezone.datetime):
            return dt
        return str(r.get("ts") or "")

    entries_all.sort(key=_sort_key, reverse=True)

    # ---- 同じ日・同じ内容の重複をまとめる ----------------------------
    deduped: List[Dict[str, Any]] = []
    seen_keys = set()

    for e in entries_all:
        dt = e.get("_dt")
        day = dt.date() if isinstance(dt, timezone.datetime) else None

        key = (
            day,
            e.get("code"),
            (e.get("mode") or "").lower() if e.get("mode") else None,
            e.get("entry"),
            e.get("tp"),
            e.get("sl"),
            e.get("qty_rakuten"),
            e.get("qty_matsui"),
            e.get("est_pl_rakuten"),
            e.get("est_pl_matsui"),
            e.get("est_loss_rakuten"),
            e.get("est_loss_matsui"),
        )

        if key in seen_keys:
            continue

        seen_keys.add(key)
        deduped.append(e)

    entries_all = deduped

    # ---- id の付与（削除用の安定したインデックス） ------------------
    for idx, e in enumerate(entries_all):
        eid = e.get("id")
        if isinstance(eid, int):
            continue
        try:
            if isinstance(eid, str) and eid.strip() != "":
                e["id"] = int(eid)
                continue
        except Exception:
            pass
        e["id"] = idx

    # ---- combined_label を付与（勝ち/負け/持ち越し/見送り） ----------
    for e in entries_all:
        qty_total = 0.0
        for key in ("qty_rakuten", "qty_matsui"):
            v = e.get(key)
            try:
                if v is not None:
                    qty_total += float(v)
            except (TypeError, ValueError):
                pass

        exit_reason = e.get("exit_reason") or ""

        if exit_reason == "hit_tp":
            combined = "win"
        elif exit_reason == "hit_sl":
            combined = "lose"
        elif exit_reason in ("horizon_close", "carry"):
            combined = "carry"  # 持ち越し
        elif exit_reason in ("no_touch", "no_fill"):
            combined = "skip"   # 見送り
        else:
            # exit_reason がまだ無い場合：
            # 数量 > 0 → まだ期間中のポジション＝持ち越し
            # 数量 = 0 → 見送り扱い
            if qty_total > 0:
                combined = "carry"
            else:
                combined = "skip"

        e["combined_label"] = combined

    now = timezone.localtime()
    today_date = now.date()

    # ---- 日付候補（年 / 月 / 日）を作成 ------------------------------
    date_list = [
        e["_dt"].date()
        for e in entries_all
        if isinstance(e.get("_dt"), timezone.datetime)
    ]

    year_options: List[Dict[str, Any]] = []
    month_options: List[Dict[str, Any]] = []
    day_options: List[Dict[str, Any]] = []

    selected_year: Optional[int] = None
    selected_month: Optional[int] = None
    selected_day: Optional[int] = None
    selected_date: Optional[_date] = None

    if date_list:
        latest_date = max(date_list)

        years = sorted({d.year for d in date_list}, reverse=True)
        try:
            y_val = int(y_param) if y_param else None
        except ValueError:
            y_val = None
        if y_val and y_val in years:
            selected_year = y_val
        else:
            selected_year = latest_date.year

        months = sorted(
            {d.month for d in date_list if d.year == selected_year},
            reverse=True,
        )
        try:
            m_val = int(m_param) if m_param else None
        except ValueError:
            m_val = None
        if m_val and m_val in months:
            selected_month = m_val
        else:
            selected_month = months[0] if months else None

        days = sorted(
            {
                d.day
                for d in date_list
                if d.year == selected_year and d.month == selected_month
            },
            reverse=True,
        )
        try:
            d_val = int(d_param) if d_param else None
        except ValueError:
            d_val = None
        if d_val and d_val in days:
            selected_day = d_val
        else:
            selected_day = days[0] if days else None

        for y in years:
            year_options.append({"value": y, "label": f"{y}年"})
        for m in months:
            month_options.append({"value": m, "label": f"{m:02d}月"})
        for d in days:
            day_options.append({"value": d, "label": f"{d:02d}日"})

        if selected_year and selected_month and selected_day:
            selected_date = _date(selected_year, selected_month, selected_day)

    # ★ 日付ピッカー用の文字列（input type="date" の value に使う）
    if selected_date is not None:
        selected_date_str = selected_date.isoformat()
    else:
        # ログがまったく無い場合などは空文字にしておく
        selected_date_str = ""

    # ---- KPI集計：今日 & 通算（フィルタとは無関係） -------------------
    def _accumulate(summary: Dict[str, Any], e: Dict[str, Any]) -> None:
        total_pl = summary.get("total_pl", 0.0)
        for key in ("eval_pl_rakuten", "eval_pl_matsui"):
            val = e.get(key)
            try:
                if val is not None:
                    total_pl += float(val)
            except (TypeError, ValueError):
                pass
        summary["total_pl"] = total_pl

        combined = e.get("combined_label")
        if combined == "win":
            summary["win"] = summary.get("win", 0) + 1
        elif combined == "lose":
            summary["lose"] = summary.get("lose", 0) + 1
        elif combined == "flat":
            summary["flat"] = summary.get("flat", 0) + 1
        else:
            # carry / skip どちらも「見送り/持ち越し」枠
            summary["skip"] = summary.get("skip", 0) + 1

    summary_today: Dict[str, Any] = {
        "win": 0,
        "lose": 0,
        "flat": 0,
        "skip": 0,
        "total_pl": 0.0,
        "has_data": False,
    }
    summary_total: Dict[str, Any] = {
        "win": 0,
        "lose": 0,
        "flat": 0,
        "skip": 0,
        "total_pl": 0.0,
        "has_data": False,
    }

    for e in entries_all:
        dt = e.get("_dt")
        if isinstance(dt, timezone.datetime) and dt.date() == today_date:
            _accumulate(summary_today, e)
        _accumulate(summary_total, e)

    summary_today["has_data"] = (summary_today["win"] +
                                 summary_today["lose"] +
                                 summary_today["flat"] +
                                 summary_today["skip"]) > 0
    summary_total["has_data"] = (summary_total["win"] +
                                 summary_total["lose"] +
                                 summary_total["flat"] +
                                 summary_total["skip"]) > 0

    # ---- 一覧表示用フィルタ（モード / 年月日 / 検索） -----------------
    filtered: List[Dict[str, Any]] = []

    for e in entries_all:
        # mode
        rec_mode = (e.get("mode") or "").lower()
        if mode == "live" and rec_mode != "live":
            continue
        if mode == "demo" and rec_mode != "demo":
            continue

        # 年月日
        dt = e.get("_dt")
        if selected_date:
            if not isinstance(dt, timezone.datetime):
                continue
            if dt.date() != selected_date:
                continue

        # 銘柄検索
        if q:
            code = str(e.get("code") or "")
            name = str(e.get("name") or "")
            if q not in code and q not in name:
                continue

        filtered.append(e)

    entries = filtered[:100]

    ctx = {
        "entries": entries,
        "mode": mode,
        "q": q,
        "summary_today": summary_today,
        "summary_total": summary_total,
        "year_options": year_options,
        "month_options": month_options,
        "day_options": day_options,
        "selected_year": selected_year,
        "selected_month": selected_month,
        "selected_day": selected_day,
        # 日付ピッカー用
        "selected_date": selected_date,
        "selected_date_str": selected_date_str,
    }
    return render(request, "aiapp/simulate_list.html", ctx)