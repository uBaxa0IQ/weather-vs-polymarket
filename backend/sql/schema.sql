CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS cities (
  id BIGSERIAL PRIMARY KEY,
  city_slug TEXT UNIQUE NOT NULL,
  station_code TEXT,
  lat DOUBLE PRECISION NOT NULL,
  lon DOUBLE PRECISION NOT NULL,
  timezone_name TEXT NOT NULL,
  temp_unit TEXT NOT NULL DEFAULT 'C',
  created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS markets (
  id BIGSERIAL PRIMARY KEY,
  city_id BIGINT NOT NULL REFERENCES cities(id),
  event_slug TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  target_date_local DATE NOT NULL,
  timezone_name TEXT NOT NULL,
  nominal_resolve_at_utc TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('tracking', 'nominally_resolved', 'pm_resolved')),
  created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_markets_city_status ON markets(city_id, status);
CREATE INDEX IF NOT EXISTS idx_markets_target_date ON markets(target_date_local);

CREATE TABLE IF NOT EXISTS market_snapshots (
  id BIGSERIAL,
  market_id BIGINT NOT NULL REFERENCES markets(id),
  captured_at_utc TIMESTAMPTZ NOT NULL,
  tomorrow_max DOUBLE PRECISION,
  ecmwf_max DOUBLE PRECISION,
  poly_implied DOUBLE PRECISION,
  top_bucket TEXT,
  top_bucket_prob DOUBLE PRECISION,
  top_bucket_index INTEGER,
  bucket_labels_json JSONB,
  bucket_prices_json JSONB,
  buckets_count INTEGER NOT NULL DEFAULT 0,
  time_to_resolve_hours DOUBLE PRECISION,
  PRIMARY KEY (id, captured_at_utc)
);
SELECT create_hypertable('market_snapshots', by_range('captured_at_utc'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_snapshots_market_time ON market_snapshots(market_id, captured_at_utc DESC);

CREATE TABLE IF NOT EXISTS market_outcome_eval (
  id BIGSERIAL PRIMARY KEY,
  market_id BIGINT NOT NULL REFERENCES markets(id),
  evaluated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  final_temp DOUBLE PRECISION,
  tomorrow_hit_main BOOLEAN,
  ecmwf_hit_main BOOLEAN,
  tomorrow_hit_main_plus_1 BOOLEAN,
  ecmwf_hit_main_plus_1 BOOLEAN,
  tomorrow_hit_main_plus_2 BOOLEAN,
  ecmwf_hit_main_plus_2 BOOLEAN
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
  id BIGSERIAL PRIMARY KEY,
  started_at_utc TIMESTAMPTZ NOT NULL,
  finished_at_utc TIMESTAMPTZ,
  status TEXT NOT NULL,
  error_message TEXT,
  triggered_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs(started_at_utc DESC);

CREATE TABLE IF NOT EXISTS scheduler_state (
  name TEXT PRIMARY KEY,
  next_run_at_utc TIMESTAMPTZ NOT NULL,
  updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Real Polymarket (UMA) resolution; complements nominal_resolve_at_utc and status.
ALTER TABLE markets ADD COLUMN IF NOT EXISTS pm_resolved_at_utc TIMESTAMPTZ;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS pm_winning_label TEXT;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS pm_winning_bucket_index INTEGER;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS pm_resolution_checked_at_utc TIMESTAMPTZ;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS pm_resolution_meta JSONB;

-- App settings (key-value store for runtime flags)
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO app_settings (key, value) VALUES
  ('betting.execution_enabled', 'false'),
  ('risk.kill_switch', 'false'),
  ('betting.time_in_force', 'FOK'),
  ('betting.slippage_tolerance', '0.05')
ON CONFLICT (key) DO NOTHING;

-- Bets placed via the trading tab
CREATE TABLE IF NOT EXISTS bets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  condition_id TEXT NOT NULL DEFAULT '',
  token_id TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('yes', 'no')),
  bucket_label TEXT NOT NULL,
  market_title TEXT NOT NULL,
  event_slug TEXT,
  amount_usd DOUBLE PRECISION NOT NULL,
  theoretical_price DOUBLE PRECISION NOT NULL,
  actual_price DOUBLE PRECISION,
  clob_order_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('dry_run', 'pending', 'filled', 'failed', 'won', 'lost', 'cancelled')),
  pnl DOUBLE PRECISION,
  snapshot_json JSONB,
  placed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_bets_status ON bets(status);
CREATE INDEX IF NOT EXISTS idx_bets_placed_at ON bets(placed_at DESC);
CREATE INDEX IF NOT EXISTS idx_bets_event_slug ON bets(event_slug) WHERE event_slug IS NOT NULL;
