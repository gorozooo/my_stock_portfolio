# aiapp/views/simulate.py
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone


@login_required
def simulate_list(request: HttpRequest) -> HttpResponse:
    """
    AI Picks の「シミュレ」で登録した紙トレを一覧表示するビュー。

    - /media/aiapp/simulate/YYYYMMDD.jsonl を全部読む
    - ログインユーザーの分だけ抽出
    - フィルタ（mode / 期間 / コード検索）を適用
    - ts 降順で最大100件まで表示
    """
    user = request.user
    sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"

    # ---- フィルタパラメータ（GET） ----------------------------------------
    # mode: all / live / demo
    mode = (request.GET.get("mode") or "all").lower()
    if mode not in ("all", "live", "demo"):
        mode = "all"

    # period: 30d / today / week
    period = (request.GET.get("period") or "30d").lower()
    if period not in ("30d", "today", "week"):
        period = "30d"

    # 検索クエリ（銘柄コード / 名称）
    query = (request.GET.get("q") or "").strip()

    entries: List[Dict[str, Any]] = []

    if sim_dir.exists():
        # 日付順に並び替えた上で全ファイルを読む（新しいファイルほど後になる）
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

                entries.append(rec)

    # ts を aware datetime & ラベルに変換 ------------------------------------
    now = timezone.localtime()
    for e in entries:
        ts_str = e.get("ts")
        label = ""
        dt: Optional[timezone.datetime] = None

        if isinstance(ts_str, str) and ts_str:
            try:
                # ISO文字列 → datetime
                dt = timezone.datetime.fromisoformat(ts_str)
                if timezone.is_naive(dt):
                    dt = timezone.make_aware(dt, timezone.get_default_timezone())
                dt = timezone.localtime(dt)
                label = dt.strftime("%Y/%m/%d %H:%M")
            except Exception:
                label = ts_str
                dt = None

        e["ts_label"] = label
        e["_ts_dt"] = dt  # フィルタ・ソート用に保持

    # ---- id 正規化（URL で必ず int を使えるようにする） -------------------
    # 古いログなどで id が入っていない場合があるので、
    # 既に int の id があればそれを優先し、それ以外は連番で補完する。
    for idx, e in enumerate(entries):
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

    # ---- フィルタ適用 -----------------------------------------------------
    filtered: List[Dict[str, Any]] = []

    # 期間フィルタの境界
    if period == "today":
        # 今日 0:00 〜
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        # 今週（月曜 0:00 〜）
        weekday = now.weekday()  # 月曜=0
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=weekday)
    else:  # "30d"
        start_dt = now - timedelta(days=30)

    q_lower = query.lower()

    for e in entries:
        dt = e.get("_ts_dt")
        if dt is None:
            # ts がパースできないものは一応通しておく（期間条件は緩め）
            dt_ok = True
        else:
            dt_ok = dt >= start_dt

        # mode フィルタ
        rec_mode = (e.get("mode") or "").lower()
        if mode == "live" and rec_mode != "live":
            continue
        if mode == "demo" and rec_mode != "demo":
            continue
        # mode="all" はすべて通す

        # 期間フィルタ
        if not dt_ok:
            continue

        # コード / 名称検索（部分一致・小文字で比較）
        if q_lower:
            code_str = str(e.get("code") or "").lower()
            name_str = str(e.get("name") or "").lower()
            if q_lower not in code_str and q_lower not in name_str:
                continue

        filtered.append(e)

    # ---- 並び替え＆件数制限 ----------------------------------------------
    def _sort_key(r: Dict[str, Any]):
        dt = r.get("_ts_dt")
        if dt is not None:
            return dt
        # dt が無い場合は ts 文字列でフォールバック
        return str(r.get("ts") or "")

    filtered.sort(key=_sort_key, reverse=True)
    filtered = filtered[:100]

    ctx = {
        "entries": filtered,
        # フィルタ状態（テンプレ側でチップの選択状態/フォーム初期値に使う）
        "filter_mode": mode,      # all / live / demo
        "filter_period": period,  # 30d / today / week
        "query": query,
    }
    return render(request, "aiapp/simulate_list.html", ctx)