# Wayfinder OS Backend

Wayfinder OS is an agentic travel planning workspace that turns messy trip conversations into structured itineraries, places, checklists, budgets, and shareable trip pages.

Current status: `v1.0.1 demo polish`

This backend powers the Wayfinder OS demo and case-study artifact. It provides authenticated trip APIs, durable PostgreSQL state, trip-aware chat, Redis-backed async agent workflows, and public read-only share payloads.

## Architecture Overview

The backend is a FastAPI application with a small worker process:

- FastAPI serves trip, chat, workflow, and public share APIs.
- Clerk session tokens are verified in the backend.
- SQLAlchemy models store users, trips, chat messages, itinerary days/items, places, checklist items, agent runs, and agent events.
- Alembic owns schema migrations.
- OpenAI Responses API powers chat and structured planning workflows.
- Redis/RQ runs long-running Build My Trip and regenerate-day jobs outside the request path.
- Structured JSON logs keep API and worker lifecycle events readable.

The frontend lives in `../frontend` as a separate repo.

## Key Backend Capabilities

- Real user identity through Clerk.
- Per-user private trip ownership.
- Durable PostgreSQL trip state.
- Trip-aware streaming chat.
- Build My Trip structured generation workflow.
- Editable day regeneration workflow.
- Agent run and event persistence.
- Redis-backed async worker jobs with retries.
- Public read-only share pages.
- Local development bypass for seeded demo data.

## Agentic Workflow Notes

Wayfinder OS separates conversational assistance from artifact generation.

Trip chat streams assistant text for direct interaction. The chat path records user and assistant messages, extracts simple planning context, loads recent trip context, and streams model output back to the frontend.

Build My Trip and regenerate-day are async workflows:

1. The API validates ownership and creates an `AgentRun`.
2. The API records an initial `AgentEvent`.
3. The API enqueues an RQ job in Redis.
4. The worker loads trip context from PostgreSQL.
5. The worker prepares a workflow-specific prompt.
6. The worker calls the OpenAI Responses API.
7. The response is parsed and validated with Pydantic schemas.
8. Validated output is persisted into itinerary, place, checklist, budget, and chat models.
9. The worker marks the run completed or failed and records lifecycle events.
10. The frontend polls run status and refreshes workspace data.

The model output is not treated as trusted application state until it has been parsed, validated, and merged by backend persistence code.

## Data Model Summary

Core tables:

- `users`: local identity mapped to Clerk users or local dev users.
- `trips`: user-owned planning workspaces with dates, status, progress, budget, planning context, and share state.
- `chat_messages`: private trip conversation history.
- `places` and `trip_places`: reusable place records and trip-specific place status.
- `itinerary_days` and `itinerary_items`: durable itinerary artifacts.
- `checklist_items`: trip preparation tasks.
- `agent_runs`: workflow run records for chat/build/regeneration.
- `agent_events`: user-visible and operational workflow timeline events.

## Local Setup

From the workspace root, start dependencies:

```bash
docker compose up -d postgres redis
```

From `backend/`, install and configure:

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python -m app.seed
```

Run the API:

```bash
uvicorn main:app --reload
```

Run the worker in a second backend terminal:

```bash
source .venv/bin/activate
python worker.py
```

The API defaults to `http://localhost:8000`.

## Environment Variables

Required for normal local/demo operation:

```bash
DATABASE_URL=postgresql+psycopg://wayfinder:wayfinder@localhost:5432/wayfinder
FRONTEND_ORIGIN=http://localhost:3000
CLERK_SECRET_KEY=
CLERK_JWT_KEY=
CLERK_AUTHORIZED_PARTIES=http://localhost:3000
AUTH_DEV_BYPASS=false
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.2
REDIS_URL=redis://localhost:6379/0
WORKFLOW_QUEUE_NAME=wayfinder
LOG_LEVEL=INFO
LOG_REQUESTS=false
```

Set `CLERK_SECRET_KEY` and `OPENAI_API_KEY` to real secret values only in local or deployment environment managers, never in committed files.

Frontend env expected by the separate frontend repo:

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=<your_clerk_publishable_key>
```

For networkless Clerk JWT verification, set `CLERK_JWT_KEY` to the PEM public key from the Clerk dashboard. Store it with escaped newlines if the deployment environment requires a single-line value.

For local demo work only, `AUTH_DEV_BYPASS=true` re-enables the seeded shared beta user for protected routes. Keep this unset or `false` outside local development.

## Authentication And Privacy

Protected API requests must include:

```text
Authorization: Bearer <clerk session token>
```

The backend maps the verified Clerk `sub` claim to a local user with `auth_provider='clerk'` and `auth_provider_user_id=<clerk subject>`.

Trip queries are scoped by `Trip.user_id`. Public share pages use a separate share slug and only return the public trip packet. Private chat history is not included in public share responses.

## Worker And Redis

For local Redis:

```bash
REDIS_URL=redis://localhost:6379/0
```

The local worker uses RQ's no-fork `SimpleWorker` by default. This avoids macOS fork-safety crashes during SDK/network initialization. To use the standard forking RQ worker in Linux deployment, set:

```bash
RQ_WORKER_CLASS=forking
```

For Railway Redis, set the Railway-provided `REDIS_URL` on both the API service and the worker service. Both processes must point at the same Redis instance.

## Deployment Notes

A production-like deployment needs:

- API service running `uvicorn main:app`.
- Worker service running `python worker.py`.
- Shared PostgreSQL database.
- Shared Redis instance.
- Alembic migrations applied.
- `FRONTEND_ORIGIN` set to the deployed frontend origin.
- `CLERK_AUTHORIZED_PARTIES` set to the allowed frontend origin(s).
- `OPENAI_API_KEY` available to both chat/API paths and worker workflows.
- `REDIS_URL` available to both API and worker services.

Do not run seed scripts against production data unless the deployment is intentionally disposable.

## Logging

Logs are emitted as JSON through `app.logging`.

Keep:

- API error logs.
- Auth warning/error logs.
- Worker started/completed/failed/retrying logs.
- Workflow enqueue errors.
- Share enable/disable lifecycle logs.

Avoid:

- Logging raw Clerk tokens.
- Logging OpenAI API keys.
- Logging full prompts or private user trip text.
- Logging raw secrets, database URLs, Redis credentials, Railway tokens, or Stripe keys.
- Excessive success-path request logs in production unless temporarily debugging.

Use `LOG_LEVEL` to control verbosity. Set `LOG_REQUESTS=true` only when per-request success logging is useful for local debugging or temporary production diagnosis. Failed HTTP responses are still logged without enabling success-path request logs.

## Security Notes

- `.env` is gitignored.
- `.env.example` should contain placeholders only.
- Rotate any real secret that is accidentally committed or shared.
- Keep `AUTH_DEV_BYPASS=false` outside local development.
- Public share links are read-only but still public to anyone with the link.
- Generated travel plans should be reviewed before booking.

## Known Limitations

- No billing, credits, or subscriptions yet.
- No collaboration or team workspaces yet.
- No Google Places/place enrichment yet.
- No flight, hotel, restaurant, or activity checkout.
- No native mobile app.
- Workflow quality depends on LLM output and available trip context.
- Public share pages are read-only.
- Account/profile management is intentionally minimal.
- RQ is sufficient for the demo but is not a full workflow orchestration platform.

## Verification

Non-destructive checks:

```bash
python -m compileall app main.py worker.py
python -c "from main import app; print(app.title); print(len(app.routes))"
alembic current
```

Frontend checks are run from `../frontend`:

```bash
npm run build
```

## Version History

- `v1.0.1 demo polish`: documentation, case study, demo script, screenshot guidance, setup notes, known limitations, and security/log hygiene pass.
- `v1.0`: polished demo with private trips, durable planning artifacts, async agent workflows, and public share pages.
- `v0.9`: v0 template frontend remake and product UX hardening.
- `v0.8`: Clerk-backed real auth and private user trip ownership.
- `v0.7`: async worker reliability and persisted agent events.
- `v0.6`: durable trip state with itinerary, places, checklist, and budget artifacts.

The seeded Tokyo/Lisbon data remains attached to the local `dev/shared-beta` user. Real authenticated users start with their own private trip dashboard.
