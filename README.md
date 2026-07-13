# Wayfinder OS Backend

## Local database

From the project root:

```bash
docker compose up -d postgres
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

`POST /dev/login` returns the shared beta user. This release intentionally does not add passwords, OAuth, private accounts, Redis, Celery, billing, or production session security.
