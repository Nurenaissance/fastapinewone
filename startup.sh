#!/bin/bash
cd /home/site/wwwroot

# Activate the virtual environment created by Oryx
if [ -d "antenv" ]; then
    echo "Activating antenv virtual environment..."
    source antenv/bin/activate
    export PYTHONPATH="/home/site/wwwroot/antenv/lib/python3.11/site-packages:$PYTHONPATH"
elif [ -d ".python_packages" ]; then
    echo "Using .python_packages..."
    export PYTHONPATH="/home/site/wwwroot/.python_packages/lib/site-packages:$PYTHONPATH"
else
    echo "No virtual environment found, using system Python..."
fi

# Debug: Check if FastAPI is importable
echo "Checking FastAPI installation..."
python -c "import fastapi; print(f'FastAPI version: {fastapi.__version__}')" || echo "FastAPI import failed!"

# Start gunicorn with uvicorn workers
echo "Starting gunicorn with uvicorn workers..."

# Use WEB_CONCURRENCY env var if set, otherwise default to 4 workers
WORKERS=${WEB_CONCURRENCY:-4}
echo "Starting with $WORKERS workers..."

gunicorn main:app \
    --workers $WORKERS \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --graceful-timeout 30 \
    --keep-alive 65 \
    --max-requests 1000 \
    --max-requests-jitter 50 \
    --preload \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    --log-level info
