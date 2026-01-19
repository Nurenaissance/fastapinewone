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
gunicorn main:app \
    --workers 2 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
