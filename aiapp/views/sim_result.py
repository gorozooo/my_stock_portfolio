# aiapp/views/sim_result.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse


@login_required
def simulate_result(request: HttpRequest, pk: int) -> HttpResponse:
    """
    シミュレ 1件分の「結果（勝ち/負け/見送り）＋終値」を保存する。

    ポイント：
      - /media/aiapp/simulate/*.jsonl をすべて順に読みながら、
        「ログインユーザーのレコード」だけを 0,1,2,... と採番した index が pk。
      - simulate_list と同じ採番ルールなので、一覧で表示されている id と一致する。
      - 書き換えるのは結果対象のレコード 1件だけ。
    """
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")

    user = request.user
    sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"

    # フォーム値を受け取り
    result = (request.POST.get("result") or "").strip()  # "win" / "lose" / "skip" / ""
    exit_price_raw = (request.POST.get("exit_price") or "").strip()

    if result not in ("", "win", "lose", "skip"):
        messages.error(request, "結果の指定が不正です。")
        return redirect(reverse("aiapp:simulate_list"))

    exit_price: float | None = None
    if exit_price_raw:
        try:
            exit_price = float(exit_price_raw)
        except Exception:
            messages.error(request, "終値は数値で入力してください。")
            return redirect(reverse("aiapp:simulate_list"))

    # ---- jsonl を1行ずつ読みながら、対象の index を探して書き換える ----
    if not sim_dir.exists():
        messages.error(request, "シミュレ用ログディレクトリがありません。")
        return redirect(reverse("aiapp:simulate_list"))

    current_index = 0  # 「ログインユーザーのレコードだけ」をカウント

    for path in sorted(sim_dir.glob("*.jsonl")):
        if not path.is_file():
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue

        lines = text.splitlines()
        new_lines: List[str] = []
        changed = False

        for line in lines:
            raw = line.strip()
            if not raw:
                new_lines.append(line)
                continue

            try:
                rec: Dict[str, Any] = json.loads(raw)
            except Exception:
                # 壊れている行はそのまま
                new_lines.append(line)
                continue

            # このユーザーのレコードだけ index を振る（simulate_list と同じ思想）
            if rec.get("user_id") == user.id:
                if current_index == pk:
                    # ★ここがターゲット行：結果を書き換え
                    if result:
                        rec["result"] = result  # "win"/"lose"/"skip"
                    else:
                        # 空にされた場合は削除扱い
                        rec.pop("result", None)

                    if exit_price is not None:
                        rec["exit_price"] = exit_price
                    else:
                        rec.pop("exit_price", None)

                    changed = True

                current_index += 1

            # 変更の有無に関わらず、JSON として書き戻す
            try:
                new_line = json.dumps(rec, ensure_ascii=False)
            except Exception:
                # もし再シリアライズで失敗した場合は元の行を残す
                new_line = line
            new_lines.append(new_line)

        # このファイル内で変更があったときだけ上書き
        if changed:
            try:
                path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            except Exception:
                messages.error(request, f"{path.name} の更新に失敗しました。")
                return redirect(reverse("aiapp:simulate_list"))

    # index が見つからなかった場合でも、とりあえず一覧に戻す
    messages.success(request, "シミュレ結果を保存しました。")
    return redirect(reverse("aiapp:simulate_list"))