# Weather Market Analyzer

FastAPI + React project for continuous analysis of Polymarket high-temperature markets.

## What is implemented (MVP scaffold)

- City pool bootstrap: manual list + auto-fill to 20 cities from resolvable weather events.
- Market lifecycle:
  - one market is assigned per city,
  - market remains tracked until local target day ends,
  - then city rotates to next market.
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
uvicorn app.main:app --reload --port 8000
# in second shell start scheduler worker:
python -m app.worker
```

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
