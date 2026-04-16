#!/usr/bin/env bash
# =========================================================
# [FILE] tradeai_notify_cycle.sh
# [PATH] <project_root>/scripts/tradeai_notify_cycle.sh
#
# このファイルは何？
# - tradeai の通知一括コマンドを cron から安全に実行するためのシェルです。
# - 二重起動防止の lock をかけてから、通知をまとめて実行します。
# =========================================================

set -euo pipefail

BASE="/home/gorozooo/my_stock_portfolio"
PY="$BASE/venv/bin/python"
LOG_DIR="$BASE/logs"
LOCK_FILE="/tmp/tradeai_notify_cycle.lock"

mkdir -p "$LOG_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[SKIP] tradeai_notify_cycle already running"
  exit 0
fi

cd "$BASE"

{
  echo "=================================================="
  date "+[%Y-%m-%d %H:%M:%S] tradeai_notify_cycle start"

  "$PY" manage.py tradeai_notify_all

  date "+[%Y-%m-%d %H:%M:%S] tradeai_notify_cycle end"
} >> "$LOG_DIR/tradeai_notify_all.log" 2>&1