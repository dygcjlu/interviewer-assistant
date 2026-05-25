#!/usr/bin/env bash
# 启动后端（NiceGUI 前端已内嵌）（macOS / Linux / Git Bash）
# 用法: ./scripts/start-dev.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$ROOT/logs"
CONDA_ENV="${CONDA_ENV:-interview-assistant}"

mkdir -p "$LOGS"

read_env() {
  local key="$1" default="$2"
  if [[ -f "$ROOT/.env" ]]; then
    local val
    val="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$ROOT/.env" | tail -1 | sed -E 's/^[^=]+=//; s/^[[:space:]]+//; s/[[:space:]]+$//; s/^["'\'']|["'\'']$//g')"
    if [[ -n "$val" ]]; then
      echo "$val"
      return
    fi
  fi
  echo "$default"
}

resolve_python() {
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    echo "$ROOT/.venv/bin/python"
    return
  fi
  if command -v conda >/dev/null 2>&1; then
    local conda_py
    conda_py="$(conda run -n "$CONDA_ENV" which python 2>/dev/null || true)"
    if [[ -n "$conda_py" && -x "$conda_py" ]]; then
      echo "$conda_py"
      return
    fi
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi
  command -v python
}

HOST_ADDR="$(read_env HOST 127.0.0.1)"
PORT="$(read_env PORT 8000)"
BACKEND_URL="http://${HOST_ADDR}:${PORT}"
PYTHON="$(resolve_python)"

echo ""
echo "=== 面试助手 开发环境启动 ==="
echo "项目目录: $ROOT"
echo "后端地址: $BACKEND_URL"
echo "Python:   $PYTHON"
echo ""

if [[ ! -x "$PYTHON" ]] && ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "错误: 未找到 Python。请创建 .venv 或 conda 环境 $CONDA_ENV。" >&2
  exit 1
fi

# 后端
echo "启动后端..."
"$PYTHON" -m src.main >>"$LOGS/backend.out.log" 2>>"$LOGS/backend.err.log" &
BACKEND_PID=$!
echo $BACKEND_PID >"$LOGS/backend.pid"

echo "等待后端就绪..."
for _ in $(seq 1 60); do
  if curl -sf "${BACKEND_URL}/api/session/current" >/dev/null 2>&1; then
    echo "后端已就绪 (PID $BACKEND_PID)"
    break
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "后端进程已退出。日志:" >&2
    tail -20 "$LOGS/backend.err.log" >&2 || true
    exit 1
  fi
  sleep 1
done

echo ""
echo "访问: ${BACKEND_URL}"
echo "停止: ./scripts/stop-dev.sh"
echo ""
