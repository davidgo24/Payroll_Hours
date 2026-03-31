web: gunicorn -k uvicorn.workers.UvicornWorker main:app -b 0.0.0.0:${PORT:-8000} --workers 2
