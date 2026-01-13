#!/bin/bash
cd /home/site/wwwroot

# Create venv if it doesn't exist (on Azure, not from deployment)
if [ ! -d "antenv" ]; then
    python -m venv antenv
fi

# Activate venv
source antenv/bin/activate

# Install dependencies
pip install -r requirements.txt --quiet --disable-pip-version-check

# Start the application
gunicorn main:app \
    --workers 2 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 120
