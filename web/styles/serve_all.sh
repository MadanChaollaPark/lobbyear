#!/usr/bin/env bash
# Launch one static HTTP server per variant on ports 3000..3005.
# Each variant talks to the FastAPI backend on http://localhost:8765 (CORS is open).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${HERE}/.pids"

variants=(v1 v2 v3 v4 v5 v6)
labels=(
  "Aurora Glass"
  "Spotlight Stars"
  "Desktop OS"
  "Liquid Flow"
  "Cyberpunk Neon"
  "Sakura Editorial"
)

start() {
  : > "${PID_FILE}"
  port=3000
  for i in "${!variants[@]}"; do
    v="${variants[$i]}"
    label="${labels[$i]}"
    dir="${HERE}/${v}"
    if [ ! -d "${dir}" ]; then
      echo "skip ${v}: ${dir} missing"
      port=$((port+1)); continue
    fi
    (cd "${dir}" && python3 -m http.server "${port}" >/dev/null 2>&1) &
    pid=$!
    echo "${pid} ${port} ${v}" >> "${PID_FILE}"
    printf "  http://localhost:%s  →  %s (%s)\n" "${port}" "${label}" "${v}"
    port=$((port+1))
  done
  echo
  echo "Backend must be running on http://localhost:8765:"
  echo "  uvicorn server.app:app --reload --port 8765"
  echo
  echo "Stop all variant servers with: ${HERE}/serve_all.sh stop"
}

stop() {
  if [ ! -f "${PID_FILE}" ]; then
    echo "no pid file at ${PID_FILE}"; return 0
  fi
  while read -r pid port v; do
    if kill "${pid}" 2>/dev/null; then
      echo "stopped ${v} on :${port} (pid ${pid})"
    fi
  done < "${PID_FILE}"
  rm -f "${PID_FILE}"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  *) echo "usage: $0 {start|stop|restart}"; exit 2 ;;
esac
