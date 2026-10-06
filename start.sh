#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

PORT="${PORT:-8088}"
HOST="${HOST:-0.0.0.0}"

echo "Starting Ring IP Cam Dashboard on http://${HOST}:${PORT}..."
echo "Accessible over Tailscale at: http://100.79.108.38:${PORT}"

exec python3 -m uvicorn server:app --host "$HOST" --port "$PORT" --log-level info
