#!/bin/bash
cd /home/site/wwwroot

# Add packaged dependencies to Python path
export PYTHONPATH="/home/site/wwwroot/.python_packages/lib/site-packages:$PYTHONPATH"

# Start gunicorn with uvicorn workers
gunicorn main:app \
    --workers 2 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 120
