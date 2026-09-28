#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "=== Starting Comack Server ==="
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

exec uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload
