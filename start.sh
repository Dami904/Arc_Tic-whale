#!/bin/bash
# Railway startup: Circle Agent Service (Node.js) + FastAPI backend
# For local dev: run with TRADE_DRY_RUN=true to skip auth and Circle API calls
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "[start] Launching Circle Agent Service on port ${AGENT_SERVICE_PORT:-3001}..."
cd "$ROOT/agent_service"
npm install --silent
node index.js &
AGENT_PID=$!
echo "[start] Agent Service PID: $AGENT_PID"

echo "[start] Waiting for Agent Service to be ready..."
for i in $(seq 1 20); do
  curl -sf "http://localhost:${AGENT_SERVICE_PORT:-3001}/health" > /dev/null && break
  sleep 1
done
echo "[start] Agent Service ready."

echo "[start] Launching FastAPI backend on port ${PORT:-8765}..."
cd "$ROOT"

# Use venv if present (local dev), otherwise use system Python (Railway)
if [ -f venv/bin/activate ]; then
    source venv/bin/activate
fi

uvicorn server.api:app --host 0.0.0.0 --port "${PORT:-8765}"

# Clean up agent service when FastAPI exits
kill $AGENT_PID 2>/dev/null || true
