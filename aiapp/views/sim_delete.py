# aiapp/views/sim_delete.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, Http404
from django.shortcuts import redirect
from django.urls import reverse


@login_required
def simulate_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """
    pk = JSONL 上での “連番インデックス”
    → simulate_list 側で e["id"] として付与している番号

    JSONL から該当行だけ削除する
    """
    if request.method != "POST":
        raise Http404("POST only")

    user = request.user
    sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"

    # 対象ファイルが存在しない場合はそのままリダイレクト
    if not sim_dir.exists():
        return redirect("aiapp:simulate_list")

    # --- 全ての JSONL を再構築するためのバッファ ---
    new_files: Dict[Path, List[str]] = {}
    found = False

    # 全 JSONL を走査
    for path in sorted(sim_dir.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue

        new_lines = []
        idx = 1  # JSONL 内でのローカルインデックス

        for ln in lines:
            ln2 = ln.strip()
            if not ln2:
                continue

            try:
                rec = json.loads(ln2)
            except Exception:
                continue

            # --- このレコードが削除対象かチェック ---
            # simulate_list 側で付与した e["id"] が pk と一致すれば削除
            if rec.get("user_id") == user.id and rec.get("id") == pk:
                found = True
                # この行は new_lines に追加しない → 削除
                continue

            # 削除しない行は保持
            new_lines.append(ln2)

        new_files[path] = new_lines

    if found:
        # JSONL ファイルを書き戻す
        for p, lines in new_files.items():
            try:
                p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            except Exception:
                pass

    # 削除完了後は一覧へ戻る
    return redirect("aiapp:simulate_list")