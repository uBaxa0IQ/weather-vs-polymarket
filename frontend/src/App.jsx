import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  Cell,
  CartesianGrid,
  ErrorBar,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import "./App.css";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

const LS_KEY = "weather-vsp:v1";
const POLY_RANK_LS = "polyRank";
function loadStored(key, fallback) {
  try {
    const raw = localStorage.getItem(`${LS_KEY}:${key}`);
    if (raw == null) return fallback;
    if (typeof fallback === "string") return raw;
    if (typeof fallback === "object" && fallback !== null && !Array.isArray(fallback)) {
      return { ...fallback, ...JSON.parse(raw) };
    }
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}
function saveStored(key, value) {
  try {
    if (typeof value === "string") {
      localStorage.setItem(`${LS_KEY}:${key}`, value);
    } else {
      localStorage.setItem(`${LS_KEY}:${key}`, JSON.stringify(value));
    }
  } catch {
    /* ignore */
  }
}

// ─── HTTP helpers ────────────────────────────────────────────────────────────

async function apiFetch(path, options) {
  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function useInterval(fn, delay) {
  const ref = useRef(fn);
  useEffect(() => { ref.current = fn; }, [fn]);
  useEffect(() => {
    if (delay == null) return;
    const id = setInterval(() => ref.current(), delay);
    return () => clearInterval(id);
  }, [delay]);
}

// ─── Formatters ──────────────────────────────────────────────────────────────

function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function fmtDateTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function fmtRelative(iso) {
  if (!iso) return null;
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function parseBucket(label) {
  const s = String(label || "").toLowerCase().replace("deg", "").replace(/\s+/g, "");
  const nums = [...s.matchAll(/(^|[^\d])(-?\d+)/g)].map((m) => Number(m[2]));
  if (s.includes("orbelow")) return { lo: null, hi: nums.length ? nums[0] : null };
  if (s.includes("orhigher")) return { lo: nums.length ? nums[0] : null, hi: null };
  if (nums.length >= 2) return { lo: Math.min(nums[0], nums[1]), hi: Math.max(nums[0], nums[1]) };
  if (nums.length === 1) return { lo: nums[0], hi: nums[0] };
  return { lo: null, hi: null };
}

function inferBucketUnit(labels) {
  for (const label of labels || []) {
    const s = String(label || "").toLowerCase().replace(/\s+/g, "");
    if (/-?\d+f(?:or|$|[^a-z])/.test(s)) return "F";
    if (/-?\d+c(?:or|$|[^a-z])/.test(s)) return "C";
  }
  return null;
}

function fmtTemp(value, unit) {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return `${value.toFixed(1)}${unit ? `°${unit}` : ""}`;
}

function bucketBounds(labels, idx) {
  const parsed = labels.map((l) => parseBucket(l));
  const cur = parsed[idx];
  if (!cur) return { lo: null, hi: null };
  // For single-point labels like "18c", infer interval by midpoint to neighbors.
  if (cur.lo != null && cur.hi != null && cur.lo === cur.hi) {
    const center = cur.lo;
    const prev = idx > 0 ? parsed[idx - 1] : null;
    const next = idx < parsed.length - 1 ? parsed[idx + 1] : null;
    const prevCenter = prev && prev.lo != null && prev.hi != null ? (prev.lo + prev.hi) / 2 : null;
    const nextCenter = next && next.lo != null && next.hi != null ? (next.lo + next.hi) / 2 : null;
    const lo = prevCenter != null ? (prevCenter + center) / 2 : center - 1;
    const hi = nextCenter != null ? (nextCenter + center) / 2 : center + 1;
    return { lo, hi };
  }
  return cur;
}

function bucketCenterFromLabel(label) {
  const { lo, hi } = parseBucket(label);
  if (lo != null && hi != null) return (lo + hi) / 2;
  // For open-ended buckets, render exactly at the threshold value.
  if (lo == null && hi != null) return hi;
  if (lo != null && hi == null) return lo;
  return null;
}

function bucketIndexForTemp(temp, labels) {
  if (temp == null || !Array.isArray(labels) || labels.length === 0) return null;
  const value = Number(temp);
  if (Number.isNaN(value)) return null;
  for (let i = 0; i < labels.length; i += 1) {
    const { lo, hi } = bucketBounds(labels, i);
    const ge = lo == null || value >= lo;
    const le = hi == null || value < hi;
    if (ge && le) return i;
  }
  // Inclusive upper bound fallback for the final bucket.
  const last = bucketBounds(labels, labels.length - 1);
  if ((last.lo == null || value >= last.lo) && (last.hi == null || value <= last.hi)) {
    return labels.length - 1;
  }
  return null;
}

function findWinningLabelIndex(pmLabel, labels) {
  if (pmLabel == null || !Array.isArray(labels) || labels.length === 0) return -1;
  const raw = String(pmLabel).trim();
  const compact = raw.toLowerCase().replace(/[^a-z0-9]/g, "");
  const norm = (s) => String(s || "").toLowerCase().replace(/\s+/g, "").replace("deg", "");
  let i = labels.findIndex((l) => norm(l) === norm(raw));
  if (i >= 0) return i;
  return labels.findIndex((l) => String(l).toLowerCase().replace(/[^a-z0-9]/g, "") === compact);
}

/** Shaded band [y1,y2] in chart temp units for resolved PM bucket; clips to chart extent. */
function resolvedOutcomeBand(pmLabel, labels, extent) {
  if (!extent || !labels?.length) return null;
  const idx = findWinningLabelIndex(pmLabel, labels);
  if (idx < 0) return null;
  const rawLabel = String(labels[idx]);
  const s = rawLabel.toLowerCase().replace(/\s+/g, "");
  const p = parseBucket(rawLabel);
  const bucketUnit = inferBucketUnit(labels);
  const isOpenEnded =
    s.includes("orbelow") || s.includes("forbelow") || s.includes("orhigher") || s.includes("forhigher");
  const isRangeBucket =
    p.lo != null
    && p.hi != null
    && p.hi > p.lo;
  const isSingleDiscrete =
    !isOpenEnded
    && p.lo != null
    && p.hi != null
    && Math.abs(p.lo - p.hi) < 1e-9;

  const emin = extent.min;
  const emax = extent.max;

  let y1;
  let y2;
  if (isOpenEnded) {
    // Open-ended resolved buckets must fill to chart boundary.
    y1 = p.lo == null ? emin : p.lo;
    y2 = p.hi == null ? emax : p.hi;
  } else if (bucketUnit === "F" && isRangeBucket) {
    // For Fahrenheit range labels like "84-85f", render exact band bounds.
    y1 = p.lo;
    y2 = p.hi;
  } else if (isSingleDiscrete) {
    const c = p.lo;
    y1 = c - 0.5;
    y2 = c + 0.5;
  } else {
    const b = bucketBounds(labels, idx);
    y1 = b.lo != null ? b.lo : emin;
    y2 = b.hi != null ? b.hi : emax;
  }

  y1 = Math.max(emin, Math.min(y1, emax));
  y2 = Math.max(emin, Math.min(y2, emax));
  if (y2 <= y1) {
    const pad = (emax - emin) * 0.02 || 0.5;
    y2 = Math.min(emax, y1 + pad);
  }
  return { y1, y2 };
}

function chartTempExtent(rows) {
  let mn = Infinity;
  let mx = -Infinity;
  for (const r of rows || []) {
    [r.tomorrow_max, r.ecmwf_max, r.poly_implied, r.top_bucket_value].forEach((v) => {
      if (typeof v === "number" && !Number.isNaN(v)) {
        mn = Math.min(mn, v);
        mx = Math.max(mx, v);
      }
    });
  }
  if (!Number.isFinite(mn)) return null;
  const pad = (mx - mn) * 0.08 || 3;
  return { min: mn - pad, max: mx + pad };
}

// ─── Custom Recharts tooltip ─────────────────────────────────────────────────

function ChartTooltip({ active, payload, label, labelFormatter, valueFormatter, metaFormatter }) {
  if (!active || !payload?.length) return null;
  const displayLabel = labelFormatter ? labelFormatter(label) : label;
  const metaText = metaFormatter ? metaFormatter(payload?.[0]?.payload) : null;
  const sortedPayload = [...payload].sort((a, b) => {
    const av = typeof a?.value === "number" ? a.value : Number.NEGATIVE_INFINITY;
    const bv = typeof b?.value === "number" ? b.value : Number.NEGATIVE_INFINITY;
    return bv - av;
  });
  return (
    <div className="chart-tooltip">
      {displayLabel && <div className="chart-tooltip-label">{displayLabel}</div>}
      {metaText && <div className="chart-tooltip-label" style={{ color: "var(--text-2)", fontWeight: 500 }}>{metaText}</div>}
      {sortedPayload.map((entry) => (
        <div key={entry.dataKey} className="chart-tooltip-row">
          <span className="chart-tooltip-dot" style={{ background: entry.color }} />
          <span style={{ color: "var(--text-2)" }}>{entry.name}</span>
          <strong style={{ color: "var(--text-1)", marginLeft: "auto", paddingLeft: 12 }}>
            {valueFormatter
              ? valueFormatter(entry.value)
              : (typeof entry.value === "number" ? entry.value.toFixed(2) : entry.value ?? "—")}
          </strong>
        </div>
      ))}
    </div>
  );
}

const CHART_THEME = {
  grid: "#21262d",
  tick: "#484f58",
  tomorrow: "#58a6ff",
  ecmwf: "#bc8cff",
  poly: "#39d353",
  topBucketValue: "#f2cc60",
};
const DIST_COLORS = ["#58a6ff", "#bc8cff", "#39d353", "#f2cc60", "#ff7b72", "#8b949e", "#7ee787", "#79c0ff"];

function axisProps(orientation = "left") {
  return {
    tick: { fill: CHART_THEME.tick, fontSize: 11 },
    axisLine: { stroke: CHART_THEME.grid },
    tickLine: false,
    orientation,
  };
}

// ─── Pipeline control panel ───────────────────────────────────────────────────

function PipelineControl({ health }) {
  const sched = health?.scheduler ?? {};
  const isRunning = sched.is_running;
  const lastRun = health?.last_run;

  return (
    <div className="sidebar-section">
      <div className="section-label">Pipeline control</div>

      <div className="pipeline-stats">
        <div className="stat-card">
          <div className="stat-label">Active</div>
          <div className="stat-value green">{health?.active_markets ?? "—"}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label" title="Polymarket (UMA) final outcome">
            PM resolved
          </div>
          <div className="stat-value">{health?.resolved_markets ?? "—"}</div>
          {health?.nominally_resolved_markets != null && health.nominally_resolved_markets > 0 && (
            <div className="stat-footnote" title="Past nominal resolve time; awaiting PM / UMA">
              Nominal only: {health.nominally_resolved_markets}
            </div>
          )}
        </div>
      </div>

      <div className="status-row">
        <span className={`status-badge ${isRunning ? "running" : "idle"}`}>
          <span className="status-dot" />
          {isRunning ? "Running" : "Idle"}
        </span>
        {lastRun?.started_at_utc && (
          <span className="last-run">{fmtRelative(lastRun.started_at_utc)}</span>
        )}
      </div>

      {lastRun?.status === "error" && (
        <div style={{ fontSize: 11, color: "var(--red)", marginBottom: 8, wordBreak: "break-word" }}>
          ✕ {lastRun.error_message?.slice(0, 120)}
        </div>
      )}

    </div>
  );
}

// ─── Market card ─────────────────────────────────────────────────────────────

function MarketCard({ market, selected, onClick }) {
  const isTracking = market.status === "tracking";
  const isPmResolved = market.status === "pm_resolved";
  const statusLabel = isTracking ? "Tracking" : (isPmResolved ? "PM resolved" : "Nominal resolved");
  return (
    <button
      className={`market-card${selected ? " selected" : ""}`}
      onClick={() => onClick(market.event_slug)}
    >
      <div className="card-top">
        <span className="card-city">{market.city_slug}</span>
        <span className="card-date">{fmtDate(market.target_date_local)}</span>
      </div>
      <div className="card-slug">{market.event_slug}</div>
      {market.pm_winning_label && (
        <div className="card-pm-outcome" title="Polymarket (UMA) resolved outcome">
          PM: {market.pm_winning_label}
        </div>
      )}
      <div className={`card-status ${market.status}`}>
        <span className="dot" />
        {statusLabel}
      </div>
    </button>
  );
}

// ─── Analytics (strategy curves) ─────────────────────────────────────────────

const STRATEGY_CURVE_SERIES_LS = "strategyCurveSeries";
const STRATEGY_CURVE_SERIES_DEFAULT = {
  tomorrow: true,
  ecmwf: true,
  polyTomorrow: false,
  polyEcmwf: false,
};

function ConsensusHitRateChart({ tomorrowData, ecmwfData }) {
  const merged = useMemo(() => {
    const map = new Map();
    for (const d of tomorrowData || []) {
      map.set(d.hours_to_resolve, { hours_to_resolve: d.hours_to_resolve, tomorrow_hit: d.hit_prob, tomorrow_n: d.samples_count });
    }
    for (const d of ecmwfData || []) {
      if (!map.has(d.hours_to_resolve)) {
        map.set(d.hours_to_resolve, { hours_to_resolve: d.hours_to_resolve });
      }
      const item = map.get(d.hours_to_resolve);
      item.ecmwf_hit = d.hit_prob;
      item.ecmwf_n = d.samples_count;
    }
    return Array.from(map.values()).sort((a, b) => b.hours_to_resolve - a.hours_to_resolve);
  }, [tomorrowData, ecmwfData]);

  if (!merged.length) {
    return (
      <div className="card">
        <div className="card-header">
          <span className="card-title">Consensus Accuracy vs Time</span>
          <span className="card-subtitle">Probability of correct outcome when model matches Polymarket top bucket</span>
        </div>
        <div className="empty-state" style={{ height: 180 }}>
          <p>No consensus data available yet.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Consensus Accuracy vs Time</span>
        <span className="card-subtitle">Probability of correct outcome when model matches Polymarket top bucket</span>
      </div>
      <div className="card-body chart-wrap">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={merged} margin={{ top: 4, right: 12, bottom: 0, left: -10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={CHART_THEME.grid} />
            <XAxis dataKey="hours_to_resolve" reversed {...axisProps("bottom")} label={{ value: "Hours to resolve", position: "insideBottom", offset: -5, fill: "var(--text-2)", fontSize: 11 }} />
            <YAxis {...axisProps()} domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
            <Tooltip
              content={(
                <ChartTooltip
                  labelFormatter={(v) => `${v}h to resolve`}
                  valueFormatter={(v) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—")}
                  metaFormatter={(row) => {
                    const nT = row?.tomorrow_n;
                    const nE = row?.ecmwf_n;
                    if (nT != null && nE != null) return `n=${nT} (Tom), n=${nE} (ECM)`;
                    if (nT != null) return `n=${nT} (Tom)`;
                    if (nE != null) return `n=${nE} (ECM)`;
                    return null;
                  }}
                />
              )}
            />
            <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-2)" }} />
            <Line type="monotone" dataKey="tomorrow_hit" stroke={CHART_THEME.tomorrow} dot={false} name="Tomorrow Consensus" strokeWidth={2} />
            <Line type="monotone" dataKey="ecmwf_hit" stroke={CHART_THEME.ecmwf} dot={false} name="ECMWF Consensus" strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function StrategyCurves({ data, polyRank, onPolyRankChange }) {
  const [curveSeries, setCurveSeries] = useState(() =>
    loadStored(STRATEGY_CURVE_SERIES_LS, STRATEGY_CURVE_SERIES_DEFAULT),
  );

  useEffect(() => {
    saveStored(STRATEGY_CURVE_SERIES_LS, curveSeries);
  }, [curveSeries]);

  const toggleCurveSeries = (key) => {
    setCurveSeries((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const mergedPolyData = useMemo(() => {
    if (!Array.isArray(data) || !data.length) return [];
    return data.map((r) => ({
      hours_to_resolve: r.hours_to_resolve,
      poly_rank_hit: r.poly_rank_hit,
      poly_rank_mean_price: r.poly_rank_mean_price,
      samples_count: r.samples_count,
    }));
  }, [data]);
  const hasStrategy = Array.isArray(data) && data.length > 0;
  const hasMerged = mergedPolyData.some(
    (r) => r.poly_rank_mean_price != null || r.poly_rank_hit != null,
  );

  if (!hasStrategy && !hasMerged) {
    return (
      <div className="empty-state" style={{ height: 200 }}>
        <p>No analytics data yet — hit curves need resolved markets; Polymarket rank chart needs bucket prices.</p>
      </div>
    );
  }

  const charts = [
    {
      title: "Main bucket hit probability",
      k1: "tomorrow_main",
      k2: "ecmwf_main",
      poly1: "tomorrow_main_poly_mass",
      poly2: "ecmwf_main_poly_mass",
      solo: false,
    },
    {
      title: "Main ±1 bucket hit probability",
      k1: "tomorrow_main_plus_1",
      k2: "ecmwf_main_plus_1",
      poly1: "tomorrow_main_plus_1_poly_mass",
      poly2: "ecmwf_main_plus_1_poly_mass",
      solo: false,
    },
    {
      title: "Main ±2 buckets hit probability",
      k1: "tomorrow_main_plus_2",
      k2: "ecmwf_main_plus_2",
      poly1: "tomorrow_main_plus_2_poly_mass",
      poly2: "ecmwf_main_plus_2_poly_mass",
      solo: false,
    },
    {
      title: "Polymarket rank: mean price vs hit rate",
      mergedPolyTop: true,
    },
  ];

  return (
    <>
      <div style={{ marginBottom: 10 }}>
        <div className="series-toggle-row" aria-label="Strategy curves series">
          <button
            type="button"
            className={`series-toggle${curveSeries.tomorrow ? " active" : ""}`}
            onClick={() => toggleCurveSeries("tomorrow")}
          >
            Tomorrow
          </button>
          <button
            type="button"
            className={`series-toggle${curveSeries.ecmwf ? " active" : ""}`}
            onClick={() => toggleCurveSeries("ecmwf")}
          >
            ECMWF
          </button>
          <button
            type="button"
            className={`series-toggle${curveSeries.polyTomorrow ? " active" : ""}`}
            onClick={() => toggleCurveSeries("polyTomorrow")}
          >
            Tomorrow Σp
          </button>
          <button
            type="button"
            className={`series-toggle${curveSeries.polyEcmwf ? " active" : ""}`}
            onClick={() => toggleCurveSeries("polyEcmwf")}
          >
            ECMWF Σp
          </button>
          <div style={{ display: "inline-flex", gap: 4, marginLeft: 10 }} aria-label="Polymarket price rank (merge chart)">
            {[1, 2, 3, 4, 5].map((rk) => (
              <button
                key={rk}
                type="button"
                className={`series-toggle${polyRank === rk ? " active" : ""}`}
                onClick={() => onPolyRankChange(rk)}
              >
                Top-{rk}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="charts-grid">
        {charts.map((cfg) => {
          if (cfg.mergedPolyTop) {
            return (
              <div key={cfg.title} className="card">
                <div className="card-header">
                  <span className="card-title">{`Polymarket top-${polyRank}: mean price vs hit rate`}</span>
                  <span className="card-subtitle">
                    {`${mergedPolyData.length} time buckets · rank by descending price (ties → higher index) · hit = PM final`}
                  </span>
                </div>
                <div className="card-body chart-wrap">
                  {!hasMerged ? (
                    <div className="empty-state" style={{ height: 200 }}>
                      <p>No bucket prices for this rank in snapshots yet.</p>
                    </div>
                  ) : (
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={mergedPolyData} margin={{ top: 4, right: 12, bottom: 0, left: -10 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke={CHART_THEME.grid} />
                        <XAxis dataKey="hours_to_resolve" {...axisProps("bottom")} tickFormatter={(v) => `${v}h`} />
                        <YAxis {...axisProps()} domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
                        <Tooltip
                          content={(
                            <ChartTooltip
                              labelFormatter={(v) => `${v}h to resolve`}
                              valueFormatter={(v) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—")}
                              metaFormatter={(row) => (
                                row?.samples_count != null ? `n=${row.samples_count}` : null
                              )}
                            />
                          )}
                        />
                        <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-2)" }} />
                        <Line
                          type="monotone"
                          dataKey="poly_rank_mean_price"
                          stroke={CHART_THEME.topBucketValue}
                          dot={false}
                          name={`Mean top-${polyRank} price`}
                          strokeWidth={2.2}
                        />
                        <Line
                          type="monotone"
                          dataKey="poly_rank_hit"
                          stroke={CHART_THEME.poly}
                          dot={false}
                          name={`Top-${polyRank} hit rate`}
                          strokeWidth={2}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  )}
                </div>
              </div>
            );
          }
          const { title, k1, k2, poly1, poly2 } = cfg;
          return (
            <div key={title} className="card">
              <div className="card-header">
                <span className="card-title">{title}</span>
                <span className="card-subtitle">{hasStrategy ? `${data.length} buckets` : "—"}</span>
              </div>
              <div className="card-body chart-wrap">
                {!hasStrategy ? (
                  <div className="empty-state" style={{ height: 200 }}>
                    <p>Hit curves need resolved markets (PM winning bucket).</p>
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: -10 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={CHART_THEME.grid} />
                      <XAxis dataKey="hours_to_resolve" {...axisProps("bottom")} tickFormatter={(v) => `${v}h`} />
                      <YAxis {...axisProps()} domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
                      <Tooltip
                        content={(
                          <ChartTooltip
                            labelFormatter={(v) => `${v}h to resolve`}
                            metaFormatter={(row) => (
                              row?.samples_count != null ? `${row.samples_count} records` : null
                            )}
                          />
                        )}
                      />
                      <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-2)" }} />
                      <>
                        {curveSeries.tomorrow && (
                          <Line type="monotone" dataKey={k1} stroke={CHART_THEME.tomorrow} dot={false} name="Tomorrow" strokeWidth={2} />
                        )}
                        {curveSeries.ecmwf && k2 && (
                          <Line type="monotone" dataKey={k2} stroke={CHART_THEME.ecmwf} dot={false} name="ECMWF" strokeWidth={2} />
                        )}
                        {curveSeries.polyTomorrow && poly1 && (
                          <Line
                            type="monotone"
                            dataKey={poly1}
                            stroke={CHART_THEME.tomorrow}
                            dot={false}
                            name="Tomorrow Σp"
                            strokeWidth={1.6}
                            strokeDasharray="6 4"
                            strokeOpacity={0.85}
                          />
                        )}
                        {curveSeries.polyEcmwf && poly2 && (
                          <Line
                            type="monotone"
                            dataKey={poly2}
                            stroke={CHART_THEME.ecmwf}
                            dot={false}
                            name="ECMWF Σp"
                            strokeWidth={1.6}
                            strokeDasharray="6 4"
                            strokeOpacity={0.85}
                          />
                        )}
                      </>
                    </LineChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

function CityDeviationBarTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  const fmt = (v) => (typeof v === "number" && Number.isFinite(v) ? `${v > 0 ? "+" : ""}${v.toFixed(2)}°` : "—");
  const title = String(row.city_slug ?? "").replace(/-/g, " ");
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-label">{title}</div>
      {row.samples_count != null && (
        <div className="chart-tooltip-label" style={{ color: "var(--text-2)", fontWeight: 500 }}>
          {row.samples_count} markets
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "auto auto auto", gap: "8px 12px", marginTop: 8 }}>
        <span style={{ color: CHART_THEME.tomorrow, fontWeight: 500 }}>Tomorrow</span>
        <span>Mean: {fmt(row.tomorrow_mean)}</span>
        <span>Range: [{fmt(row.tomorrow_mean_min)}, {fmt(row.tomorrow_mean_max)}]</span>
        
        <span style={{ color: CHART_THEME.ecmwf, fontWeight: 500 }}>ECMWF</span>
        <span>Mean: {fmt(row.ecmwf_mean)}</span>
        <span>Range: [{fmt(row.ecmwf_mean_min)}, {fmt(row.ecmwf_mean_max)}]</span>
      </div>
    </div>
  );
}

const CustomDotBar = (props) => {
  const { x, y, width, fill, value } = props;
  if (value == null) return null;
  const centerX = x + width / 2;
  return <circle cx={centerX} cy={y} r={4} fill={fill} />;
};

function CityForecastDeviationChart({ data }) {
  const sorted = useMemo(
    () => [...(data ?? [])].sort((a, b) => (b.tomorrow_mean ?? 0) - (a.tomorrow_mean ?? 0)),
    [data],
  );
  const formattedData = useMemo(() => {
    return sorted.map((d) => ({
      ...d,
      tomorrow_error: d.tomorrow_mean != null && d.tomorrow_mean_min != null && d.tomorrow_mean_max != null
        ? [d.tomorrow_mean - d.tomorrow_mean_min, d.tomorrow_mean_max - d.tomorrow_mean]
        : [0, 0],
      ecmwf_error: d.ecmwf_mean != null && d.ecmwf_mean_min != null && d.ecmwf_mean_max != null
        ? [d.ecmwf_mean - d.ecmwf_mean_min, d.ecmwf_mean_max - d.ecmwf_mean]
        : [0, 0],
    }));
  }, [sorted]);

  if (!formattedData.length) {
    return (
      <div className="empty-state" style={{ height: 220 }}>
        <p>No snapshot deviation data yet.</p>
      </div>
    );
  }
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">City forecast deviation from actual</span>
        <span className="card-subtitle">{`${formattedData.length} cities`}</span>
      </div>
      <div className="card-body chart-wrap" style={{ height: 340 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={formattedData}
            margin={{ top: 12, right: 10, bottom: 52, left: 4 }}
            barCategoryGap="20%"
          >
            <CartesianGrid strokeDasharray="3 3" stroke={CHART_THEME.grid} vertical={false} />
            <XAxis
              dataKey="city_slug"
              {...axisProps("bottom")}
              interval={0}
              tickFormatter={(slug) => String(slug).replace(/-/g, " ")}
              angle={-38}
              textAnchor="end"
              height={68}
            />
            <YAxis {...axisProps()} tickFormatter={(v) => `${v > 0 ? "+" : ""}${v}°`} width={44} />
            <ReferenceLine
              y={0}
              stroke="#d29922"
              strokeDasharray="8 6"
              strokeWidth={2}
              strokeOpacity={0.95}
            />
            <Tooltip content={<CityDeviationBarTooltip />} cursor={{ fill: "rgba(255,255,255,0.04)" }} />
            <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-2)" }} iconType="circle" />
            
            <Bar
              dataKey="tomorrow_mean"
              name="Tomorrow"
              fill={CHART_THEME.tomorrow}
              maxBarSize={12}
              shape={<CustomDotBar />}
            >
              <ErrorBar dataKey="tomorrow_error" width={4} strokeWidth={2} stroke={CHART_THEME.tomorrow} opacity={0.8} />
            </Bar>
            
            <Bar
              dataKey="ecmwf_mean"
              name="ECMWF"
              fill={CHART_THEME.ecmwf}
              maxBarSize={12}
              shape={<CustomDotBar />}
            >
              <ErrorBar dataKey="ecmwf_error" width={4} strokeWidth={2} stroke={CHART_THEME.ecmwf} opacity={0.8} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function AllBucketsCalibrationChart({ data, binPct, onChangeBinPct }) {
  const bins = data?.bins || [];
  if (!bins.length) {
    return (
      <div className="card">
        <div className="card-header">
          <span className="card-title">All-buckets probability calibration</span>
          <span className="card-subtitle">bin: {binPct}%</span>
        </div>
        <div className="empty-state" style={{ height: 180 }}>
          <p>No resolved snapshot data for calibration yet.</p>
        </div>
      </div>
    );
  }
  return (
    <div className="card">
      <div className="card-header" style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <div>
          <span className="card-title">All-buckets probability calibration</span>
          <span className="card-subtitle">{`samples: ${data.total_samples} · ECE ${((data.ece ?? 0) * 100).toFixed(2)}% · Brier ${(data.brier ?? 0).toFixed(4)}`}</span>
        </div>
        <div className="btn-row">
          <button type="button" className={`btn ${binPct === 1 ? "btn-primary" : ""}`} onClick={() => onChangeBinPct(1)}>1%</button>
          <button type="button" className={`btn ${binPct === 5 ? "btn-primary" : ""}`} onClick={() => onChangeBinPct(5)}>5%</button>
        </div>
      </div>
      <div className="card-body chart-wrap">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={bins} margin={{ top: 4, right: 12, bottom: 0, left: -10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={CHART_THEME.grid} />
            <XAxis dataKey="bin_mid" {...axisProps("bottom")} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
            <YAxis {...axisProps()} domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
            <Tooltip
              content={(
                <ChartTooltip
                  labelFormatter={(v) => `Pred ${Math.round(v * 100)}%`}
                  valueFormatter={(v) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—")}
                  metaFormatter={(row) => (
                    row?.samples_count != null ? `n=${row.samples_count}` : null
                  )}
                />
              )}
            />
            <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-2)" }} />
            <Line type="monotone" dataKey="ideal_prob" stroke="#8b949e" dot={false} name="Ideal (y=x)" strokeWidth={1.5} />
            <Line type="monotone" dataKey="observed_hit_rate" stroke={CHART_THEME.topBucketValue} dot={false} name="Observed hit rate" strokeWidth={2.3} />
            <Line type="monotone" dataKey="predicted_mean" stroke={CHART_THEME.poly} dot={false} name="Predicted mean" strokeWidth={1.7} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ─── Market detail ────────────────────────────────────────────────────────────

function MarketDetail({ slug, market }) {
  const [timeseries, setTimeseries] = useState(null);
  const [selectedSnapshotTime, setSelectedSnapshotTime] = useState(null);
  const [error, setError] = useState(null);
  const [visibleSeries, setVisibleSeries] = useState(() =>
    loadStored("chartSeries", { tomorrow: true, ecmwf: true, poly: true, topBucket: true }),
  );

  useEffect(() => {
    saveStored("chartSeries", visibleSeries);
  }, [visibleSeries]);

  useEffect(() => {
    if (!slug) return;
    setTimeseries(null);
    setSelectedSnapshotTime(null);
    setError(null);
    apiFetch(`/markets/${encodeURIComponent(slug)}/timeseries`)
      .then((ts) => {
        setTimeseries(ts);
        if (Array.isArray(ts) && ts.length) {
          setSelectedSnapshotTime(ts[ts.length - 1].captured_at_utc);
        }
      })
      .catch((e) => setError(e.message));
  }, [slug]);

  if (error) {
    return (
      <div className="empty-state">
        <p style={{ color: "var(--red)" }}>Failed to load: {error}</p>
      </div>
    );
  }

  const loading = timeseries === null;
  const chartSeries = useMemo(
    () => (timeseries || []).map((r) => ({
      ...r,
      top_bucket_value: bucketCenterFromLabel(r.top_bucket),
    })),
    [timeseries],
  );
  const tempExtent = useMemo(() => chartTempExtent(chartSeries), [chartSeries]);
  const resolveLabels = useMemo(() => {
    if (!Array.isArray(timeseries) || !market?.pm_winning_label) return [];
    for (let i = timeseries.length - 1; i >= 0; i -= 1) {
      const L = timeseries[i].bucket_labels_json;
      if (Array.isArray(L) && findWinningLabelIndex(market.pm_winning_label, L) >= 0) return L;
    }
    const last = timeseries[timeseries.length - 1];
    return last?.bucket_labels_json || [];
  }, [timeseries, market?.pm_winning_label]);
  const outcomeBand = useMemo(() => {
    if (market?.status !== "pm_resolved" || !market?.pm_winning_label || !tempExtent) return null;
    return resolvedOutcomeBand(market.pm_winning_label, resolveLabels, tempExtent);
  }, [market?.status, market?.pm_winning_label, resolveLabels, tempExtent]);
  const selectedSnapshot = useMemo(() => {
    if (!Array.isArray(timeseries) || timeseries.length === 0) return null;
    return (
      timeseries.find((r) => r.captured_at_utc === selectedSnapshotTime)
      ?? timeseries[timeseries.length - 1]
    );
  }, [timeseries, selectedSnapshotTime]);
  const selectedUnit = useMemo(
    () => inferBucketUnit(selectedSnapshot?.bucket_labels_json || []),
    [selectedSnapshot],
  );
  const distributionRows = useMemo(() => {
    const labels = selectedSnapshot?.bucket_labels_json || [];
    const prices = selectedSnapshot?.bucket_prices_json || [];
    if (!Array.isArray(labels) || !Array.isArray(prices) || labels.length === 0) return [];
    const rawRows = labels.map((label, idx) => {
      const probRaw = Number(prices[idx] ?? 0);
      const prob = Number.isFinite(probRaw) ? probRaw : 0;
      return {
        idx,
        label,
        rawProb: prob,
        isTop: idx === selectedSnapshot?.top_bucket_index,
      };
    });
    const total = rawRows.reduce((acc, r) => acc + r.rawProb, 0);
    return rawRows.map((r) => ({
      ...r,
      prob: total > 0 ? r.rawProb / total : 0,
    }));
  }, [selectedSnapshot]);
  const tomorrowBucketIdx = bucketIndexForTemp(selectedSnapshot?.tomorrow_max, selectedSnapshot?.bucket_labels_json);
  const ecmwfBucketIdx = bucketIndexForTemp(selectedSnapshot?.ecmwf_max, selectedSnapshot?.bucket_labels_json);
  const polyBucketIdx = selectedSnapshot?.top_bucket_index ?? null;
  const markerItems = [
    { key: "T", label: "Tomorrow", idx: tomorrowBucketIdx },
    { key: "E", label: "ECMWF", idx: ecmwfBucketIdx },
    { key: "P", label: "Poly top", idx: polyBucketIdx },
  ];
  const toggleSeries = (key) => {
    setVisibleSeries((prev) => ({ ...prev, [key]: !prev[key] }));
  };
  const hoursLeft = market?.nominal_resolve_at_utc
    ? ((new Date(market.nominal_resolve_at_utc) - Date.now()) / 3600000).toFixed(1)
    : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Header */}
      <div className="card">
        <div className="card-body">
          <div className="market-detail-header">
            <span className="market-detail-slug">{slug}</span>
            {market && (
              <>
                <span className="meta-chip">{market.city_slug}</span>
                <span className={`meta-chip ${market.status === "tracking" ? "green" : market.status === "pm_resolved" ? "pm" : "dim"}`}>
                  {market.status === "tracking"
                    ? "● Tracking"
                    : market.status === "pm_resolved"
                      ? "● PM resolved"
                      : "○ Nominal resolved"}
                </span>
                {market.pm_winning_label && (
                  <span className="meta-chip" title="Official Polymarket / UMA outcome">
                    PM outcome: {market.pm_winning_label}
                    {market.pm_winning_bucket_index != null ? ` (#${market.pm_winning_bucket_index})` : ""}
                  </span>
                )}
                <span className="meta-chip">Target: {fmtDate(market.target_date_local)}</span>
                {hoursLeft !== null && (
                  <span className="meta-chip">{hoursLeft}h to resolve</span>
                )}
                <span className="meta-chip text-muted">{timeseries?.length ?? "…"} snapshots</span>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div className="forecast-chart-header">
            <span className="card-title">Forecast timeseries</span>
            <div className="series-toggle-row" aria-label="Toggle chart lines">
              <button
                type="button"
                className={`series-toggle${visibleSeries.tomorrow ? " active" : ""}`}
                onClick={() => toggleSeries("tomorrow")}
              >
                Tomorrow
              </button>
              <button
                type="button"
                className={`series-toggle${visibleSeries.ecmwf ? " active" : ""}`}
                onClick={() => toggleSeries("ecmwf")}
              >
                ECMWF
              </button>
              <button
                type="button"
                className={`series-toggle${visibleSeries.poly ? " active" : ""}`}
                onClick={() => toggleSeries("poly")}
              >
                Poly implied
              </button>
              <button
                type="button"
                className={`series-toggle${visibleSeries.topBucket ? " active" : ""}`}
                onClick={() => toggleSeries("topBucket")}
              >
                Top-1 bucket temp
              </button>
            </div>
          </div>
          <span className="card-subtitle">
            {`Toggle lines as needed${selectedUnit ? ` · unit °${selectedUnit}` : ""}`}
          </span>
        </div>
        <div className="card-body market-forecast-layout">
          <div className="chart-wrap-tall">
            {loading ? (
              <div className="skeleton" style={{ height: "100%", borderRadius: 4 }} />
            ) : chartSeries.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartSeries} margin={{ top: 4, right: 12, bottom: 0, left: -10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_THEME.grid} />
                  <XAxis dataKey="captured_at_utc" hide />
                  <YAxis
                    yAxisId="temp"
                    {...axisProps()}
                    domain={tempExtent ? [tempExtent.min, tempExtent.max] : undefined}
                  />
                  {outcomeBand && (
                    <ReferenceArea
                      yAxisId="temp"
                      y1={outcomeBand.y1}
                      y2={outcomeBand.y2}
                      fill={CHART_THEME.poly}
                      fillOpacity={0.14}
                      stroke={CHART_THEME.poly}
                      strokeOpacity={0.45}
                      strokeDasharray="4 4"
                    />
                  )}
                  <Tooltip content={<ChartTooltip labelFormatter={fmtDateTime} valueFormatter={(v) => fmtTemp(v, selectedUnit)} />} />
                  <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-2)" }} />
                  {visibleSeries.tomorrow && (
                    <Line yAxisId="temp" type="monotone" dataKey="tomorrow_max" stroke={CHART_THEME.tomorrow} dot={false} name="Tomorrow" strokeWidth={2} />
                  )}
                  {visibleSeries.ecmwf && (
                    <Line yAxisId="temp" type="monotone" dataKey="ecmwf_max" stroke={CHART_THEME.ecmwf} dot={false} name="ECMWF" strokeWidth={2} />
                  )}
                  {visibleSeries.poly && (
                    <Line yAxisId="temp" type="monotone" dataKey="poly_implied" stroke={CHART_THEME.poly} dot={false} name="Poly implied" strokeWidth={2} />
                  )}
                  {visibleSeries.topBucket && (
                    <Line
                      yAxisId="temp"
                      type="monotone"
                      dataKey="top_bucket_value"
                      stroke={CHART_THEME.topBucketValue}
                      dot={false}
                      name="Top-1 bucket temp"
                      strokeWidth={2}
                    />
                  )}
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="empty-state" style={{ height: "100%" }}>
                <p>No data yet</p>
              </div>
            )}
          </div>

          <div className="dist-panel">
            <div className="dist-header">
              <span className="card-title" style={{ fontSize: 12 }}>Bucket probabilities</span>
              <span className="card-subtitle">
                {selectedSnapshot?.captured_at_utc ? fmtDateTime(selectedSnapshot.captured_at_utc) : "—"}
              </span>
            </div>
            {!distributionRows.length ? (
              <div className="empty-state" style={{ padding: "20px 10px" }}>
                <p>No bucket distribution yet</p>
              </div>
            ) : (
              <div className="dist-compact-wrap">
                <div className="dist-donut">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={distributionRows}
                        dataKey="prob"
                        nameKey="label"
                        innerRadius={42}
                        outerRadius={72}
                        paddingAngle={1}
                        stroke="none"
                      >
                        {distributionRows.map((row) => (
                          <Cell key={row.label} fill={DIST_COLORS[row.idx % DIST_COLORS.length]} opacity={row.isTop ? 1 : 0.8} />
                        ))}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="dist-mini-list">
                  {distributionRows.slice().sort((a, b) => b.prob - a.prob).slice(0, 5).map((row) => (
                    <div key={`${row.label}-${row.idx}`} className={`dist-mini-row${row.isTop ? " top" : ""}`}>
                      <span className="dist-color" style={{ background: DIST_COLORS[row.idx % DIST_COLORS.length] }} />
                      <span className="dist-label">{row.label}</span>
                      <span className="dist-prob">{(row.prob * 100).toFixed(1)}%</span>
                    </div>
                  ))}
                </div>
                <div className="dist-targets">
                  {markerItems.map((m) => {
                    const label = m.idx != null && distributionRows[m.idx] ? distributionRows[m.idx].label : "—";
                    return (
                      <div key={m.key} className="dist-target-row">
                        <span className={`marker on ${m.key === "T" ? "tomorrow" : m.key === "E" ? "ecmwf" : "poly"}`}>{m.key}</span>
                        <span className="dist-target-name">{m.label}</span>
                        <span className="dist-target-value">{label}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Snapshot table */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">Snapshots</span>
          <span className="card-subtitle">{`last 120${selectedUnit ? ` · unit °${selectedUnit}` : ""}`}</span>
        </div>
        {loading ? (
          <div className="skeleton" style={{ height: 120, margin: 16, borderRadius: 4 }} />
        ) : timeseries?.length ? (
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Time (UTC)</th>
                  <th style={{ textAlign: "right" }}>Tomorrow</th>
                  <th style={{ textAlign: "right" }}>ECMWF</th>
                  <th style={{ textAlign: "right" }}>Poly implied</th>
                  <th>Top bucket</th>
                </tr>
              </thead>
              <tbody>
                {[...timeseries].reverse().slice(0, 120).map((r) => (
                  <tr
                    key={r.captured_at_utc}
                    className={selectedSnapshotTime === r.captured_at_utc ? "table-row-selected" : ""}
                    onClick={() => setSelectedSnapshotTime(r.captured_at_utc)}
                  >
                    <td className="td-time">{fmtDateTime(r.captured_at_utc)}</td>
                    <td className="td-num">{fmtTemp(r.tomorrow_max, selectedUnit)}</td>
                    <td className="td-num">{fmtTemp(r.ecmwf_max, selectedUnit)}</td>
                    <td className="td-num">{fmtTemp(r.poly_implied, selectedUnit)}</td>
                    <td style={{ color: "var(--text-2)", fontSize: 11 }}>{r.top_bucket ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-state">
            <p>No snapshots yet</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Root app ─────────────────────────────────────────────────────────────────

export function App() {
  const [markets, setMarkets] = useState([]);
  const [marketsLoading, setMarketsLoading] = useState(true);
  const [health, setHealth] = useState(null);
  const [strategyCurves, setStrategyCurves] = useState([]);
  const [consensusHitTomorrow, setConsensusHitTomorrow] = useState([]);
  const [consensusHitEcmwf, setConsensusHitEcmwf] = useState([]);
  const [cityDeviation, setCityDeviation] = useState([]);
  const [calibrationBinPct, setCalibrationBinPct] = useState(() => Number(loadStored("calibrationBinPct", 5)) === 1 ? 1 : 5);
  const [bucketCalibration, setBucketCalibration] = useState(null);
  const [polyRank, setPolyRank] = useState(() => {
    const v = Number(loadStored(POLY_RANK_LS, 1));
    return Number.isFinite(v) && v >= 1 && v <= 5 ? Math.floor(v) : 1;
  });
  const [view, setView] = useState(() => loadStored("view", "markets"));
  const [selectedSlug, setSelectedSlug] = useState(() => loadStored("selectedSlug", ""));
  const [cityFilter, setCityFilter] = useState(() => loadStored("cityFilter", ""));
  const [marketsError, setMarketsError] = useState(null);

  const fetchHealth = useCallback(() => {
    apiFetch("/ops/pipeline-health").then(setHealth).catch(console.error);
  }, []);

  const fetchCityDeviation = useCallback(() => {
    apiFetch("/analytics/city-forecast-deviation")
      .then(setCityDeviation)
      .catch(console.error);
  }, []);

  const fetchBucketCalibration = useCallback((binPct) => {
    apiFetch(`/analytics/bucket-calibration?bin_pct=${binPct}`)
      .then(setBucketCalibration)
      .catch(console.error);
  }, []);

  const fetchMarkets = useCallback(() => {
    setMarketsLoading(true);
    const params = cityFilter ? `?city=${encodeURIComponent(cityFilter)}` : "";
    apiFetch(`/markets${params}`)
      .then((d) => { setMarkets(d); setMarketsError(null); })
      .catch((e) => setMarketsError(e.message))
      .finally(() => setMarketsLoading(false));
  }, [cityFilter]);

  useEffect(() => { saveStored("view", view); }, [view]);
  useEffect(() => { saveStored("selectedSlug", selectedSlug); }, [selectedSlug]);
  useEffect(() => { saveStored("cityFilter", cityFilter); }, [cityFilter]);
  useEffect(() => { saveStored("calibrationBinPct", calibrationBinPct); }, [calibrationBinPct]);
  useEffect(() => { saveStored(POLY_RANK_LS, polyRank); }, [polyRank]);

  const fetchStrategyCurves = useCallback(() => {
    apiFetch(`/analytics/strategy-curves?poly_rank=${polyRank}`)
      .then(setStrategyCurves)
      .catch(console.error);
  }, [polyRank]);

  const fetchConsensusHits = useCallback(() => {
    apiFetch("/analytics/consensus-hit-vs-time?model=tomorrow")
      .then(setConsensusHitTomorrow)
      .catch(console.error);
    apiFetch("/analytics/consensus-hit-vs-time?model=ecmwf")
      .then(setConsensusHitEcmwf)
      .catch(console.error);
  }, []);

  // Initial data load
  useEffect(() => {
    fetchHealth();
    fetchMarkets();
    fetchStrategyCurves();
    fetchConsensusHits();
    fetchCityDeviation();
    fetchBucketCalibration(calibrationBinPct);
  }, []);

  // Re-fetch markets when city filter changes
  useEffect(() => { fetchMarkets(); }, [fetchMarkets]);

  useEffect(() => {
    if (view === "dashboard") {
      fetchStrategyCurves();
      fetchConsensusHits();
      fetchCityDeviation();
      fetchBucketCalibration(calibrationBinPct);
    }
  }, [view, fetchStrategyCurves, fetchConsensusHits, fetchCityDeviation, fetchBucketCalibration, calibrationBinPct]);

  // Poll health every 30 s
  useInterval(fetchHealth, 30_000);

  const cities = useMemo(() => [...new Set(markets.map((m) => m.city_slug))].sort(), [markets]);

  const selectedMarket = useMemo(
    () => markets.find((m) => m.event_slug === selectedSlug) ?? null,
    [markets, selectedSlug],
  );

  const today = new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });

  return (
    <div className="app-shell">
      {/* ── Header ── */}
      <header className="header">
        <div className="header-brand">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
          </svg>
          Weather Market Analyzer
        </div>
        <div className="header-meta">
          <span>{today}</span>
          <span className="live-badge">
            <span className="live-dot" />
            Live
          </span>
        </div>
      </header>

      <div className="app-body">
        {/* ── Sidebar ── */}
        <aside className="sidebar">
          <div className="sidebar-nav" role="navigation" aria-label="App views">
            <button
              type="button"
              className={`nav-square${view === "dashboard" ? " active" : ""}`}
              title="Dashboard — analytics"
              aria-pressed={view === "dashboard"}
              onClick={() => setView("dashboard")}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="7" height="7" rx="1" />
                <rect x="14" y="3" width="7" height="7" rx="1" />
                <rect x="3" y="14" width="7" height="7" rx="1" />
                <rect x="14" y="14" width="7" height="7" rx="1" />
              </svg>
            </button>
            <button
              type="button"
              className={`nav-square${view === "markets" ? " active" : ""}`}
              title="Markets"
              aria-pressed={view === "markets"}
              onClick={() => setView("markets")}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="8" y1="6" x2="21" y2="6" />
                <line x1="8" y1="12" x2="21" y2="12" />
                <line x1="8" y1="18" x2="21" y2="18" />
                <line x1="3" y1="6" x2="3.01" y2="6" />
                <line x1="3" y1="12" x2="3.01" y2="12" />
                <line x1="3" y1="18" x2="3.01" y2="18" />
              </svg>
            </button>
          </div>

          <PipelineControl health={health} />

          {/* City filter */}
          <div className="market-filter">
            <select
              className="select-input"
              value={cityFilter}
              onChange={(e) => setCityFilter(e.target.value)}
            >
              <option value="">All cities ({markets.length})</option>
              {cities.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>

          {/* Market list */}
          {view === "markets" && (
            <div className="market-list">
              {marketsError ? (
                <div className="empty-state">
                  <p style={{ color: "var(--red)" }}>{marketsError}</p>
                </div>
              ) : marketsLoading ? (
                <div style={{ padding: 16 }}>
                  {[...Array(6)].map((_, i) => (
                    <div key={i} className="skeleton" style={{ height: 68, marginBottom: 6, borderRadius: 6 }} />
                  ))}
                </div>
              ) : markets.length === 0 ? (
                <div className="empty-state" style={{ padding: 20 }}>
                  <p>No markets yet</p>
                </div>
              ) : (
                markets.map((m) => (
                  <MarketCard
                    key={m.event_slug}
                    market={m}
                    selected={selectedSlug === m.event_slug}
                    onClick={(slug) => {
                      setSelectedSlug(slug);
                      setView("markets");
                    }}
                  />
                ))
              )}
            </div>
          )}
        </aside>

        {/* ── Main ── */}
        <main className="main">
          {view === "dashboard" && (
            <section>
              <div style={{ marginBottom: 12 }}>
                <div className="section-label" style={{ fontSize: 11 }}>
                  Analytics · Hit probability vs hours to resolution
                </div>
              </div>
              <ConsensusHitRateChart
                tomorrowData={consensusHitTomorrow}
                ecmwfData={consensusHitEcmwf}
              />
              <div style={{ marginTop: 12 }}>
                <StrategyCurves
                  data={strategyCurves}
                  polyRank={polyRank}
                  onPolyRankChange={setPolyRank}
                />
              </div>
              <div style={{ marginTop: 12 }}>
                <CityForecastDeviationChart data={cityDeviation} />
              </div>
              <div style={{ marginTop: 12 }}>
                <AllBucketsCalibrationChart
                  data={bucketCalibration}
                  binPct={calibrationBinPct}
                  onChangeBinPct={setCalibrationBinPct}
                />
              </div>
            </section>
          )}

          {view === "markets" && (
            <>
              {!selectedSlug ? (
                <div className="empty-state">
                  <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
                  </svg>
                  <p>Select a market from the sidebar</p>
                </div>
              ) : (
                <section>
                  <MarketDetail slug={selectedSlug} market={selectedMarket} />
                </section>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  );
}
