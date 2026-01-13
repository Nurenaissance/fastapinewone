#!/bin/bash

# FastAPI Startup Script for Azure App Service
echo "=== Starting FastAPI Application ==="

cd /home/site/wwwroot

# Create virtual environment if it doesn't exist
if [ ! -d "antenv" ]; then
    echo "Creating virtual environment..."
    python -m venv antenv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source antenv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Set environment variables
export PYTHONPATH=/home/site/wwwroot

# Start with gunicorn
echo "Starting Gunicorn with Uvicorn workers..."
exec gunicorn main:app \
    --workers 2 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --keep-alive 75 \
    --log-level info \
    --access-logfile - \
    --error-logfile -
