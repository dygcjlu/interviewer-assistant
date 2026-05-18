#!/usr/bin/env bash
# 停止 start-dev.sh 启动的进程
# 用法: ./scripts/stop-dev.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$ROOT/logs"

stop_pid_file() {
  local file="$1" label="$2"
  if [[ -f "$file" ]]; then
    local pid
    pid="$(cat "$file")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
      echo "已停止 $label (PID $pid)"
    fi
    rm -f "$file"
  fi
}

stop_pid_file "$LOGS/backend.pid" "后端"

# 兜底：按端口清理（需 lsof）
read_env_port() {
  if [[ -f "$ROOT/.env" ]]; then
    grep -E '^[[:space:]]*PORT[[:space:]]*=' "$ROOT/.env" | tail -1 | sed -E 's/^[^=]+=//; s/[^0-9]//g'
  else
    echo "8001"
  fi
}

if command -v lsof >/dev/null 2>&1; then
  PORT="$(read_env_port)"
  pids="$(lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "$pids" | xargs kill -9 2>/dev/null || true
    echo "已释放端口 $PORT"
  fi
fi

echo "完成。"
