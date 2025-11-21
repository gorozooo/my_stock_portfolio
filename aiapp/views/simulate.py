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

    - /media/aiapp/simulate/*.jsonl を全部読む
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

    # ---- 削除用に「必ず整数の id を振る」 --------------------------
    # 一覧ページの並び順（ts 降順）に対して 1,2,3,... と連番を振る。
    # テンプレートの e.id と、削除ビューの pk はこの番号を使う前提。
    for idx, e in enumerate(entries, start=1):
        e["id"] = idx
    # --------------------------------------------------------------

    ctx = {
        "entries": entries,
    }
    return render(request, "aiapp/simulate_list.html", ctx)