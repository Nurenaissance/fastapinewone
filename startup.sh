#!/bin/bash

# FastAPI Startup Script for Azure App Service
# This configures Gunicorn with multiple workers for better performance

echo "=== Starting FastAPI Application ==="

# Find and activate the virtual environment
if [ -d "/tmp/8de529f56c18966/antenv" ]; then
    echo "Activating virtual environment..."
    source /tmp/8de529f56c18966/antenv/bin/activate
elif [ -d "antenv" ]; then
    echo "Activating local antenv..."
    source antenv/bin/activate
elif [ -d "/home/site/wwwroot/antenv" ]; then
    echo "Activating wwwroot antenv..."
    source /home/site/wwwroot/antenv/bin/activate
fi

# Add Python packages to PATH (fallback)
export PATH="/tmp/8de529f56c18966/antenv/bin:$PATH"
export PATH="/home/site/wwwroot/antenv/bin:$PATH"

# Verify uvicorn is available
if ! command -v gunicorn &> /dev/null; then
    echo "gunicorn not found, installing dependencies..."
    pip install -r requirements.txt
fi

# Set the number of workers (2-4 workers recommended for Azure B1/S1 instances)
WORKERS=${WORKERS:-4}

# Set worker class to uvicorn workers (async support)
WORKER_CLASS="uvicorn.workers.UvicornWorker"

# Set timeout (increase if you have long-running operations)
TIMEOUT=${TIMEOUT:-120}

# Set keep-alive to prevent Azure from closing connections too quickly
KEEPALIVE=${KEEPALIVE:-75}

# Bind to the port Azure provides (default 8000 for local)
PORT=${PORT:-8000}
HOST=${HOST:-0.0.0.0}

# Log level
LOG_LEVEL=${LOG_LEVEL:-info}

echo "Starting FastAPI with Gunicorn..."
echo "Workers: $WORKERS"
echo "Worker Class: $WORKER_CLASS"
echo "Timeout: $TIMEOUT seconds"
echo "Keep-Alive: $KEEPALIVE seconds"
echo "Binding to: $HOST:$PORT"

# Start Gunicorn with Uvicorn workers
exec gunicorn main:app \
    --workers $WORKERS \
    --worker-class $WORKER_CLASS \
    --bind $HOST:$PORT \
    --timeout $TIMEOUT \
    --keep-alive $KEEPALIVE \
    --log-level $LOG_LEVEL \
    --access-logfile - \
    --error-logfile - \
    --preload
