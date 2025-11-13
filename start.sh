#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

PORT="${1:-8090}"
HOST="${2:-0.0.0.0}"
PATH_PREFIX="/mcp"

echo "Activating virtual environment..."
source "$REPO_ROOT/venv/bin/activate"

echo "Starting Secure MySQL MCP server on ${HOST}:${PORT}${PATH_PREFIX}"
exec python secure_mysql_mcp_server.py \
  --host "$HOST" \
  --port "$PORT" \
  --path "$PATH_PREFIX"
