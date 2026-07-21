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

## Authentication

Wayfinder OS v0.8 uses Clerk for login/signup and verifies Clerk session tokens in the FastAPI backend.

Required frontend env:

```bash
VITE_CLERK_PUBLISHABLE_KEY=pk_test_...
```

Required backend env:

```bash
CLERK_SECRET_KEY=sk_test_...
CLERK_AUTHORIZED_PARTIES=http://localhost:5173
```

For networkless JWT verification, also set `CLERK_JWT_KEY` to the PEM public key from the Clerk dashboard. Store it with escaped newlines if your environment requires a single-line value.

Protected API requests must include:

```text
Authorization: Bearer <clerk session token>
```

The backend maps the verified Clerk `sub` claim to a local `users.auth_provider='clerk'` and `users.auth_provider_user_id`.

For local demo work only, you may set:

```bash
AUTH_DEV_BYPASS=true
```

This re-enables the shared seeded beta user for protected routes and logs every bypassed request. Keep it unset or `false` outside local development.

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

The seeded Tokyo/Lisbon data remains attached to the local `dev/shared-beta` user. Real authenticated users start with their own private trip dashboard. This release intentionally does not add billing, credits, subscriptions, team workspaces, collaboration roles, or Celery.
