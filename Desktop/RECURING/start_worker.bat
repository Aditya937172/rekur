@echo off
echo Starting Celery worker for RECURING...
echo Make sure Redis is running: redis-server
echo.
celery -A app.worker worker --loglevel=info --concurrency=4
