#!/bin/bash
set -e

echo "Starting CyberSec News Streamer..."

# Start the worker in the background
echo "Starting background worker..."
python -m src.worker &
WORKER_PID=$!

# Give worker time to initialize
sleep 2

# Start the web portal (foreground)
echo "Starting web portal on port ${PORTAL_PORT:-8080}..."
exec uvicorn src.web.app:app --host 0.0.0.0 --port ${PORTAL_PORT:-8080}
