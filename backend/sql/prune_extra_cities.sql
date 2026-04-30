-- Remove cities that are not in TRACKED_CITIES (Gamma "fill" leftovers).
-- Safe order: snapshots → outcome_eval → markets → cities.
-- Slugs: cities present on a typical overrun DB but not in env TRACKED_CITIES.

BEGIN;

CREATE TEMP TABLE _drop_city_slug (slug TEXT PRIMARY KEY);
INSERT INTO _drop_city_slug (slug) VALUES
  ('amsterdam'),
  ('beijing'),
  ('buenos-aires'),
  ('busan'),
  ('cape-town'),
  ('chongqing'),
  ('dallas');

DELETE FROM market_snapshots
WHERE market_id IN (
  SELECT m.id FROM markets m
  JOIN cities c ON c.id = m.city_id
  JOIN _drop_city_slug d ON d.slug = c.city_slug
);

DELETE FROM market_outcome_eval
WHERE market_id IN (
  SELECT m.id FROM markets m
  JOIN cities c ON c.id = m.city_id
  JOIN _drop_city_slug d ON d.slug = c.city_slug
);

DELETE FROM markets
WHERE city_id IN (SELECT c.id FROM cities c JOIN _drop_city_slug d ON d.slug = c.city_slug);

DELETE FROM cities
WHERE city_slug IN (SELECT slug FROM _drop_city_slug);

COMMIT;
