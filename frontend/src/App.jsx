import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Cell,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import "./App.css";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

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
  if (lo == null && hi != null) return hi - 1;
  if (lo != null && hi == null) return lo + 1;
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

// ─── Custom Recharts tooltip ─────────────────────────────────────────────────

function ChartTooltip({ active, payload, label, labelFormatter, valueFormatter }) {
  if (!active || !payload?.length) return null;
  const displayLabel = labelFormatter ? labelFormatter(label) : label;
  return (
    <div className="chart-tooltip">
      {displayLabel && <div className="chart-tooltip-label">{displayLabel}</div>}
      {payload.map((entry) => (
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

function PipelineControl({ health, onAction }) {
  const [busy, setBusy] = useState(false);

  async function act(endpoint) {
    setBusy(true);
    try {
      await apiFetch(`/ops/scheduler/${endpoint}`, { method: "POST" });
      onAction();
    } catch (e) {
      console.error(e);
    } finally {
      setBusy(false);
    }
  }

  const sched = health?.scheduler ?? {};
  const isRunning = sched.is_running;
  const apiSchedulerEnabled = sched.api_scheduler_enabled !== false;
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
          <div className="stat-label">Snaps 24 h</div>
          <div className="stat-value">{health?.snapshots_24h ?? "—"}</div>
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

      <div className="btn-row">
        <button
          className="btn btn-primary"
          disabled={busy || isRunning || !apiSchedulerEnabled}
          onClick={() => act("trigger")}
          title={apiSchedulerEnabled ? "Run pipeline once immediately" : "Disabled: worker owns pipeline runs"}
        >
          ▶ Run once
        </button>
        {!isRunning ? (
          <button
            className="btn btn-success"
            disabled={busy || !apiSchedulerEnabled}
            onClick={() => act("start")}
            title={apiSchedulerEnabled ? "Start auto-loop every hour" : "Disabled: worker owns pipeline runs"}
          >
            ⟳ Start loop
          </button>
        ) : (
          <button
            className="btn btn-danger"
            disabled={busy}
            onClick={() => act("stop")}
          >
            ■ Stop
          </button>
        )}
      </div>
    </div>
  );
}

// ─── Market card ─────────────────────────────────────────────────────────────

function MarketCard({ market, selected, onClick }) {
  const isTracking = market.status === "tracking";
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
        {isTracking ? "Tracking" : "Nominal resolved"}
      </div>
    </button>
  );
}

// ─── Analytics (strategy curves) ─────────────────────────────────────────────

function StrategyCurves({ data }) {
  if (!data?.length) {
    return (
      <div className="empty-state" style={{ height: 200 }}>
        <p>No resolved markets yet — curves appear after first resolution.</p>
      </div>
    );
  }

  const charts = [
    { title: "Main bucket hit probability", k1: "tomorrow_main", k2: "ecmwf_main" },
    { title: "Main ±1 bucket hit probability", k1: "tomorrow_main_plus_1", k2: "ecmwf_main_plus_1" },
    { title: "Main ±2 buckets hit probability", k1: "tomorrow_main_plus_2", k2: "ecmwf_main_plus_2" },
  ];

  return (
    <div className="charts-grid">
      {charts.map(({ title, k1, k2 }) => (
        <div key={title} className="card">
          <div className="card-header">
            <span className="card-title">{title}</span>
            <span className="card-subtitle">{data.length} buckets</span>
          </div>
          <div className="card-body chart-wrap">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: -10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={CHART_THEME.grid} />
                <XAxis dataKey="hours_to_resolve" {...axisProps("bottom")} tickFormatter={(v) => `${v}h`} />
                <YAxis {...axisProps()} domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
                <Tooltip content={<ChartTooltip labelFormatter={(v) => `${v}h to resolve`} />} />
                <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-2)" }} />
                <Line type="monotone" dataKey={k1} stroke={CHART_THEME.tomorrow} dot={false} name="Tomorrow" strokeWidth={2} />
                <Line type="monotone" dataKey={k2} stroke={CHART_THEME.ecmwf} dot={false} name="ECMWF" strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── Market detail ────────────────────────────────────────────────────────────

function MarketDetail({ slug, market }) {
  const [timeseries, setTimeseries] = useState(null);
  const [selectedSnapshotTime, setSelectedSnapshotTime] = useState(null);
  const [error, setError] = useState(null);
  const [visibleSeries, setVisibleSeries] = useState({
    tomorrow: true,
    ecmwf: true,
    poly: true,
    topBucket: true,
  });

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
                <span className={`meta-chip ${market.status === "tracking" ? "green" : "dim"}`}>
                  {market.status === "tracking" ? "● Tracking" : "○ Nominal resolved"}
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
                  <YAxis yAxisId="temp" {...axisProps()} />
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
  const [selectedSlug, setSelectedSlug] = useState("");
  const [cityFilter, setCityFilter] = useState("");
  const [marketsError, setMarketsError] = useState(null);

  const fetchHealth = useCallback(() => {
    apiFetch("/ops/pipeline-health").then(setHealth).catch(console.error);
  }, []);

  const fetchMarkets = useCallback(() => {
    setMarketsLoading(true);
    const params = cityFilter ? `?city=${encodeURIComponent(cityFilter)}` : "";
    apiFetch(`/markets${params}`)
      .then((d) => { setMarkets(d); setMarketsError(null); })
      .catch((e) => setMarketsError(e.message))
      .finally(() => setMarketsLoading(false));
  }, [cityFilter]);

  // Initial data load
  useEffect(() => {
    fetchHealth();
    fetchMarkets();
    apiFetch("/analytics/strategy-curves").then(setStrategyCurves).catch(console.error);
  }, []);

  // Re-fetch markets when city filter changes
  useEffect(() => { fetchMarkets(); }, [fetchMarkets]);

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
          <PipelineControl health={health} onAction={() => { fetchHealth(); fetchMarkets(); }} />

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
                  onClick={setSelectedSlug}
                />
              ))
            )}
          </div>
        </aside>

        {/* ── Main ── */}
        <main className="main">
          {/* Strategy curves — always visible */}
          <section>
            <div style={{ marginBottom: 12 }}>
              <div className="section-label" style={{ fontSize: 11 }}>
                Analytics · Hit probability vs hours to resolution
              </div>
            </div>
            <StrategyCurves data={strategyCurves} />
          </section>

          <hr className="divider" />

          {/* Market detail */}
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
        </main>
      </div>
    </div>
  );
}
