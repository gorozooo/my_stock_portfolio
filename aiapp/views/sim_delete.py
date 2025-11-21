# aiapp/views/sim_delete.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone


def _parse_ts(ts_str: Optional[str]) -> Optional[timezone.datetime]:
    """
    JSONL の ts(ISO 文字列) を timezone-aware datetime に変換。
    失敗した場合は None。
    """
    if not isinstance(ts_str, str) or not ts_str:
        return None
    try:
        dt = timezone.datetime.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


def _sort_key(rec: Dict[str, Any]):
    """
    シミュレ一覧と同じ並び順になるようにソートキーを定義。
    """
    dt = rec.get("_dt")
    if isinstance(dt, timezone.datetime):
        return dt
    return str(rec.get("ts") or "")


@login_required
def simulate_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """
    シミュレ記録を 1 件だけ削除する。

    – /media/aiapp/simulate/*.jsonl からログインユーザーのレコードを全部集める
    – ts 降順でソート
    – pk 番目の 1 件を「削除対象」として特定
    – 対象と一致する JSON レコード 1 行だけを JSONL から除去
    """
    user = request.user
    sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"

    if not sim_dir.exists():
        # そもそも何も無ければ削除対象がない
        raise Http404("no simulate logs")

    # 全レコードを読み込み（ログインユーザーの分だけ）
    user_records: List[Dict[str, Any]] = []

    # このあと削除時に使うので「どのファイルに書かれていたか」も持たせる
    for path in sorted(sim_dir.glob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue

        for line in text.splitlines():
            raw_line = line.strip()
            if not raw_line:
                continue
            try:
                rec = json.loads(raw_line)
            except Exception:
                continue

            if rec.get("user_id") != user.id:
                continue

            # 一覧と同じように ts を datetime 化
            dt = _parse_ts(rec.get("ts"))
            rec["_dt"] = dt
            rec["_file"] = path  # どのファイルに書かれていたかを覚えておく
            user_records.append(rec)

    if not user_records:
        raise Http404("no simulate records for this user")

    # ts 降順でソート（一覧と同じ）
    user_records.sort(key=_sort_key, reverse=True)

    # pk が範囲外なら 404
    if pk < 0 or pk >= len(user_records):
        raise Http404("simulate record index out of range")

    target = user_records[pk]
    target_file: Path = target.get("_file")
    if not isinstance(target_file, Path) or not target_file.exists():
        # ファイル自体が無い場合は 404 扱い
        raise Http404("target file not found")

    # 同一判定に使うキー（完全一致しなくてもよいが、絞り込み用にいくつか見る）
    target_user_id = target.get("user_id")
    target_ts = target.get("ts")
    target_code = target.get("code")
    target_entry = target.get("entry")

    # 実際にファイルを書き換え：該当 1 行だけスキップして再保存
    try:
        text = target_file.read_text(encoding="utf-8")
    except Exception:
        raise Http404("failed to read target file")

    new_lines: List[str] = []
    deleted = False

    for line in text.splitlines():
        raw_line = line.strip()
        if not raw_line:
            # 空行はそのまま捨てるか、必要なら保持しても良いがここではスキップ
            continue

        try:
            rec = json.loads(raw_line)
        except Exception:
            # 壊れた行はそのまま残しておく
            new_lines.append(line)
            continue

        if (
            not deleted and
            rec.get("user_id") == target_user_id and
            rec.get("ts") == target_ts and
            rec.get("code") == target_code and
            rec.get("entry") == target_entry
        ):
            # この 1 件だけ削除（スキップ）する
            deleted = True
            continue

        # それ以外の行はそのまま残す
        new_lines.append(line)

    # 実際に1件も消していない場合でも、そのまま書き戻しておけば整合は取れる
    target_file.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8")

    # 削除後は一覧へ戻す（フィルタは一旦リセット）
    return redirect(reverse("aiapp:simulate_list"))