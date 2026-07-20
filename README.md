# Wayfinder OS Backend

## Local database

From the project root:

```bash
docker compose up -d postgres redis
```

From `backend/`:

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python -m app.seed
uvicorn main:app --reload
```

In a second backend terminal, run the worker:

```bash
cd backend
source .venv/bin/activate
python worker.py
```

The local worker uses RQ's no-fork `SimpleWorker` by default. This avoids macOS fork-safety crashes during SDK/network initialization. To use the standard forking RQ worker in a Linux environment, set:

```bash
RQ_WORKER_CLASS=forking
```

For local Redis, use:

```bash
REDIS_URL=redis://localhost:6379/0
```

For Railway Redis, set the Railway-provided `REDIS_URL` on both the API service and the worker service. Both processes must point at the same Redis instance.

`POST /dev/login` returns the shared beta user. This release intentionally does not add passwords, OAuth, private accounts, billing, production session security, or Celery.
