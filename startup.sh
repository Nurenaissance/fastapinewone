#!/bin/bash
set -e

cd /home/site/wwwroot

# Install dependencies on Azure (avoids cross-platform venv issues)
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet

# Start the application
exec gunicorn main:app \
    --workers 2 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 120
