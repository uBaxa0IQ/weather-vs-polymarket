# Weather Market Analyzer

FastAPI + React project for continuous analysis of Polymarket high-temperature markets.

## What is implemented (MVP scaffold)

- City pool bootstrap: manual list + auto-fill to 20 cities from resolvable weather events.
- Market lifecycle:
  - one market is assigned per city,
  - market remains tracked until local target day ends,
  - then city rotates to next market.
- **Two resolution layers** (independent):
  - **Nominal** — `nominal_resolve_at_utc` (end of the local target day) and `status: nominally_resolved` when the pipeline rotates the city to the next event.
  - **Polymarket (UMA)** — `pm_*` columns on `markets` when the official Gamma/CTF outcome is available (submarket with Yes ≈ 1.0 for the winning temperature bucket). The worker reconciles this after each pipeline run for markets that are nominally resolved but not yet `pm_resolved_at_utc`.
- Hourly ingestion for tracked markets:
  - Tomorrow max forecast,
  - ECMWF max forecast,
  - Polymarket implied + top bucket.
- Global Tomorrow API rate limit: `3 req/sec`.
- PostgreSQL/Timescale schema for markets, snapshots, pipeline runs.
- API endpoints:
  - `GET /health`
  - `GET /markets`
  - `GET /markets/{event_slug}/timeseries`
  - `GET /analytics/probability-hit-vs-time`
  - `GET /ops/pipeline-health`
- React dashboard skeleton with market list/filter and time-series table.

## Run DB

```bash
docker compose up -d db
```

## Local run (without Docker for app)

```bash
cd backend
py -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
copy .env.example .env
# set TOMORROW_API_KEY in .env
# optional: align Alembic version table (schema also applied on app startup via sql/schema.sql)
alembic upgrade head
uvicorn app.main:app --reload --port 8000
# in second shell start scheduler worker:
python -m app.worker
```

## Database migrations (Alembic)

Migrations live under `backend/alembic/`. The app still applies `sql/schema.sql` on startup (idempotent), and Alembic revision `20260427_0001` adds Polymarket resolution columns in an idempotent way if they are missing.

```bash
cd backend
# DATABASE_URL in .env or default from alembic.ini
alembic upgrade head
```

On a server that only ever used `sql/schema.sql`, you can either run `alembic upgrade head` or, if the columns already exist, `alembic stamp 20260427_0001` to record the current revision without re-running DDL.

**Docker:** `docker compose exec backend alembic upgrade head` (from the repo root, with `backend` as workdir in the image: `/app`).

The **backend** container runs `alembic upgrade head` automatically on start (`docker-entrypoint.sh`) before `uvicorn`, so a plain `docker compose up --build` applies migrations. The **worker** overrides the entrypoint and does not run Alembic; it waits for the backend healthcheck so migrations have finished.

## Run frontend

```bash
cd frontend
npm install
npm run dev
```

## Notes

- Current analytics endpoint is a first-pass aggregate and should be refined into your three dashboard probability curves (main, main+1, main+2).
- Outcome evaluation table exists but final resolved-temperature backfill is not implemented yet.

## VPS run (recommended)

1) Prepare env:

```bash
cp backend/.env.example backend/.env
# edit backend/.env and set TOMORROW_API_KEY
```

2) Start all services:

```bash
docker compose up -d --build
```

This now includes `frontend` on port `5173` in addition to `db`, `backend`, and `worker`.

3) Verify:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ops/pipeline-health
curl http://localhost:8000/markets
docker compose logs -f worker
```

Production hardening included:
- separate API and scheduler worker processes,
- external API retries with exponential backoff and jitter,
- global Tomorrow limiter at `3 req/sec`,
- `restart: unless-stopped` for backend/worker/db.
