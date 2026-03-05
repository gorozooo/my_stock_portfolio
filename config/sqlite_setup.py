# =========================================================
# [FILE] sqlite_setup.py
# [PATH] <project_root>/config/sqlite_setup.py
#
# このファイルは何？
# - SQLite接続が作られたタイミングで PRAGMA を流し、
#   WALモード + busy_timeout を有効化します。
# - 同時アクセス時の "database is locked" をかなり減らします。
# =========================================================

from django.db.backends.signals import connection_created


def _apply_sqlite_pragmas(connection):
    try:
        if connection.vendor != "sqlite":
            return
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA busy_timeout=30000;")  # 30秒
    except Exception:
        # pragma適用に失敗してもアプリを落とさない
        return


def _on_connection_created(sender, connection, **kwargs):
    _apply_sqlite_pragmas(connection)


connection_created.connect(_on_connection_created)