# aiapp/views/simulate.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

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
    - ts 降順で最大100件まで表示
    """
    user = request.user
    sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"

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

    # ts（ISO文字列）で新しい順にソート
    def _key(r: Dict[str, Any]) -> str:
        return str(r.get("ts") or "")

    entries.sort(key=_key, reverse=True)

    # 最大100件に制限（必要なら調整）
    entries = entries[:100]

    # ts をローカルタイムのラベルに変換
    for e in entries:
        ts_str = e.get("ts")
        label = ""
        if isinstance(ts_str, str) and ts_str:
            try:
                dt = timezone.datetime.fromisoformat(ts_str)
                if timezone.is_naive(dt):
                    dt = timezone.make_aware(dt, timezone.get_default_timezone())
                dt = timezone.localtime(dt)
                label = dt.strftime("%Y/%m/%d %H:%M")
            except Exception:
                label = ts_str
        e["ts_label"] = label

    # ---- URL に使う id を必ず持たせる ---------------------------------
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
    # -----------------------------------------------------------------

    # ---- 一覧上部に出すサマリー値を計算 --------------------------------
    total_count = len(entries)
    live_count = 0
    demo_count = 0
    total_est_pl = 0.0        # 想定利益（楽天＋松井）
    total_est_loss = 0.0      # 想定損失（楽天＋松井の絶対値合計）

    for e in entries:
        mode = e.get("mode")
        if mode == "live":
            live_count += 1
        elif mode == "demo":
            demo_count += 1

        try:
            est_pl_r = float(e.get("est_pl_rakuten") or 0)
        except Exception:
            est_pl_r = 0.0
        try:
            est_pl_m = float(e.get("est_pl_matsui") or 0)
        except Exception:
            est_pl_m = 0.0
        try:
            est_loss_r = float(e.get("est_loss_rakuten") or 0)
        except Exception:
            est_loss_r = 0.0
        try:
            est_loss_m = float(e.get("est_loss_matsui") or 0)
        except Exception:
            est_loss_m = 0.0

        total_est_pl += est_pl_r + est_pl_m
        # 損失側は絶対値で合計しておく
        total_est_loss += abs(est_loss_r) + abs(est_loss_m)

    summary = {
        "total_count": total_count,
        "live_count": live_count,
        "demo_count": demo_count,
        "total_est_pl": total_est_pl,
        "total_est_loss": total_est_loss,
    }
    # -----------------------------------------------------------------

    ctx = {
        "entries": entries,
        "summary": summary,
    }
    return render(request, "aiapp/simulate_list.html", ctx)