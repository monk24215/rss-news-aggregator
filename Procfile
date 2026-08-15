web: python -m app.migrate && python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
worker: python -m app.worker
scheduler: python -m app.scheduler
