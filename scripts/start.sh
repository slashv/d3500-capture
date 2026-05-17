#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")/.."

export D3500_HOST="${D3500_HOST:-127.0.0.1}"
export D3500_PORT="${D3500_PORT:-8000}"

exec uv run uvicorn app.main:app --host "$D3500_HOST" --port "$D3500_PORT" --reload
