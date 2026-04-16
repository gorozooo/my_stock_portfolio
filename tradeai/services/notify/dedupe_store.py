# =========================================================
# [FILE] dedupe_store.py
# [PATH] <project_root>/tradeai/services/notify/dedupe_store.py
#
# このファイルは何？
# - LINE通知の重複を抑えるための簡易ストアです。
# - DBは使わず、media 配下の JSON ファイルで管理します。
# - 同じ銘柄・同じ方向・同じ判断が短時間に何度も送られないようにします。
# =========================================================

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone


def _store_path() -> Path:
    return Path(settings.MEDIA_ROOT) / "tradeai" / "notify_state" / "watch_signals_dedupe.json"


def _ensure_parent_dir() -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)


def _load_store() -> dict:
    path = _store_path()
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_store(data: dict) -> None:
    _ensure_parent_dir()
    path = _store_path()
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def cleanup_old_entries(days: int = 7) -> None:
    data = _load_store()
    if not data:
        return

    now = timezone.now()
    cutoff = now - timedelta(days=days)

    cleaned: dict[str, str] = {}
    for key, value in data.items():
        try:
            sent_at = timezone.datetime.fromisoformat(value)
            if timezone.is_naive(sent_at):
                sent_at = timezone.make_aware(sent_at, timezone.get_current_timezone())
        except Exception:
            continue

        if sent_at >= cutoff:
            cleaned[key] = value

    _save_store(cleaned)


def was_sent_recently(dedupe_key: str, dedupe_minutes: int = 180) -> bool:
    data = _load_store()
    raw = data.get(dedupe_key)
    if not raw:
        return False

    try:
        sent_at = timezone.datetime.fromisoformat(raw)
        if timezone.is_naive(sent_at):
            sent_at = timezone.make_aware(sent_at, timezone.get_current_timezone())
    except Exception:
        return False

    return sent_at >= timezone.now() - timedelta(minutes=dedupe_minutes)


def mark_sent(dedupe_keys: list[str]) -> None:
    data = _load_store()
    now_iso = timezone.now().isoformat()

    for key in dedupe_keys:
        data[key] = now_iso

    _save_store(data)