# =========================================================
# [FILE] cron_lock.py
# [PATH] <project_root>/autotrade/services/common/cron_lock.py
#
# このファイルは何？
# - django-crontab は OSコマンドとして flock を書けないため、
#   Python側で “非ブロッキング排他ロック（flock -n相当）” を実現するユーティリティです。
# - lockを取れない場合は即スキップして return します（重複実行しない）。
# =========================================================

from __future__ import annotations

import os
import fcntl
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def cron_file_lock(lock_path: str) -> Iterator[bool]:
    """
    non-blocking lock（flock -n相当）
    - 取れた: True
    - 取れない: False（即返す）
    """
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)

    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield True
        except BlockingIOError:
            yield False
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception:
                pass
    finally:
        try:
            os.close(fd)
        except Exception:
            pass