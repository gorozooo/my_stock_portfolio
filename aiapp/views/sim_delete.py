# aiapp/views/sim_delete.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect


@login_required
def simulate_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """
    シミュレ一覧から 1 件削除する。

    simulate_list で付けている「ユーザーごとの連番 id」
    （最新を 1, 2, 3, ...）を pk として受け取り、
    対応する JSONL の 1 行を物理的に削除する。
    """
    if request.method != "POST":
        return redirect("aiapp:simulate_list")

    user = request.user
    sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"

    if not sim_dir.exists():
        return redirect("aiapp:simulate_list")

    # ── simulate_list と同じ順番で走査して「ユーザーのレコードだけ」連番を振る ──
    current_index: int = 1
    target: Tuple[Path, int] | None = None  # (ファイルパス, 行インデックス)

    for path in sorted(sim_dir.glob("*.jsonl")):
        try:
            lines_plain = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue

        for line_idx, line in enumerate(lines_plain):
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
            except Exception:
                continue

            # 自分以外のユーザーのログは番号も付けない（simulate_list と同じ）
            if rec.get("user_id") != user.id:
                continue

            if current_index == pk:
                target = (path, line_idx)
                break

            current_index += 1

        if target is not None:
            break

    # 対象が見つからなければそのまま一覧へ（古いIDを踏んだなど）
    if target is None:
        return redirect("aiapp:simulate_list")

    file_path, remove_idx = target

    # 実際にその行を削除して書き戻す
    try:
        # 改行を保持したまま読む
        lines_with_nl = file_path.read_text(encoding="utf-8").splitlines(True)
    except Exception:
        return redirect("aiapp:simulate_list")

    new_lines = [l for i, l in enumerate(lines_with_nl) if i != remove_idx]

    try:
        file_path.write_text("".join(new_lines), encoding="utf-8")
    except Exception:
        # 書き込み失敗時も、とりあえず一覧に戻す
        return redirect("aiapp:simulate_list")

    return redirect("aiapp:simulate_list")