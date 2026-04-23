// dashboard/frontend/src/App.jsx
// ──────────────────────────────────────────────────────────────────────────────
// Real-time monitoring dashboard for the cold-start prediction system.
// Polls /dashboard-data every 30 seconds and renders live charts.
//
// Charts:
//   1. Cold Start Rate over time
//   2. Adaptive Threshold evolution
//   3. Prediction MAE over time
//   4. Over-Provisioning Rate over time
// ──────────────────────────────────────────────────────────────────────────────

import React, { useEffect, useState, useCallback } from "react";
import axios from "axios";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from "chart.js";
import { Line } from "react-chartjs-2";
import "./App.css";

ChartJS.register(
  CategoryScale, LinearScale, PointElement, LineElement,
  Title, Tooltip, Legend, Filler
);

// ── constants ─────────────────────────────────────────────────────────────────
const POLL_INTERVAL_MS = 30_000;
const API_BASE = process.env.REACT_APP_API_BASE || "https://aws-research-internship.onrender.com";

// ── helpers ───────────────────────────────────────────────────────────────────
const lineChartOptions = (titleText, yLabel, yMin = 0, yMax = null) => ({
  responsive: true,
  animation: false,
  plugins: {
    legend: { position: "top" },
    title: { display: true, text: titleText },
  },
  scales: {
    x: { title: { display: true, text: "Cycle" } },
    y: {
      title: { display: true, text: yLabel },
      min: yMin,
      ...(yMax !== null ? { max: yMax } : {}),
    },
  },
});

function buildDataset(label, data, color) {
  return {
    label,
    data,
    borderColor: color,
    backgroundColor: color + "22",
    fill: true,
    tension: 0.3,
    pointRadius: 2,
  };
}

function cycleLabels(n) {
  return Array.from({ length: n }, (_, i) => String(i + 1));
}

// ── StatCard ──────────────────────────────────────────────────────────────────
function StatCard({ title, value, unit, highlight }) {
  return (
    <div className={`stat-card ${highlight ? "highlight" : ""}`}>
      <div className="stat-title">{title}</div>
      <div className="stat-value">
        {value !== null && value !== undefined ? value : "—"}
        {unit && <span className="stat-unit"> {unit}</span>}
      </div>
    </div>
  );
}

// ── main App ──────────────────────────────────────────────────────────────────
export default function App() {
  const [data, setData] = useState(null);
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [dashRes, healthRes] = await Promise.all([
        axios.get(`${API_BASE}/dashboard-data`),
        axios.get(`${API_BASE}/health`),
      ]);
      setData(dashRes.data);
      setHealth(healthRes.data);
      setLastUpdated(new Date().toLocaleTimeString());
      setError(null);
    } catch (err) {
      setError("Cannot reach API server. Make sure uvicorn is running on port 8000.");
    }
  }, []);

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [fetchData]);

  const handleRetrain = async () => {
    try {
      await axios.post(`${API_BASE}/retrain`);
      alert("Retraining job queued. Check server logs for progress.");
    } catch {
      alert("Failed to queue retraining job.");
    }
  };

  if (error) {
    return (
      <div className="app error-screen">
        <h2>⚠ Connection Error</h2>
        <p>{error}</p>
        <button onClick={fetchData} className="btn-primary">Retry</button>
      </div>
    );
  }

  if (!data) {
    return <div className="app loading">Loading dashboard data…</div>;
  }

  const n = data.n_cycles;
  const labels = cycleLabels(n);
  const latest = data.latest_cycle;

  // Derived stats
  const latestCS = latest ? (latest.cold_start_rate * 100).toFixed(1) : null;
  const latestOP = latest ? (latest.over_provision_rate * 100).toFixed(1) : null;
  const latestMAE = latest ? latest.prediction_mae?.toFixed(3) : null;
  const latestThreshold = data.current_threshold?.toFixed(3);

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div>
          <h1>Cold-Start Prediction Monitor</h1>
          <span className="subtitle">AWS Lambda · LSTM + ARIMA · Adaptive Pre-Warming</span>
        </div>
        <div className="header-right">
          {health && (
            <span className={`status-badge ${health.model_loaded ? "ok" : "warn"}`}>
              {health.model_loaded ? "● Model Loaded" : "○ No Model"}
            </span>
          )}
          <button className="btn-secondary" onClick={handleRetrain}>
            ↻ Retrain
          </button>
          <span className="last-updated">Updated: {lastUpdated}</span>
        </div>
      </header>

      {/* Stat cards */}
      <section className="stat-grid">
        <StatCard title="Adaptive Threshold (λ)" value={latestThreshold} highlight />
        <StatCard title="Cold Start Rate" value={latestCS} unit="%" />
        <StatCard title="Over-Provisioning Rate" value={latestOP} unit="%" />
        <StatCard title="Prediction MAE" value={latestMAE} unit="invocations" />
        <StatCard title="Cycles Recorded" value={n} />
        <StatCard
          title="Pre-Warmed (last cycle)"
          value={latest ? latest.n_pre_warmed : null}
          unit="functions"
        />
      </section>

      {/* Charts */}
      <section className="chart-grid">
        <div className="chart-card">
          <Line
            options={lineChartOptions("Cold Start Rate", "Rate", 0, 1)}
            data={{
              labels,
              datasets: [
                buildDataset(
                  "Cold Start Rate",
                  data.cold_start_rate_series,
                  "#ef4444"
                ),
              ],
            }}
          />
        </div>

        <div className="chart-card">
          <Line
            options={lineChartOptions("Adaptive Threshold (λ)", "λ value")}
            data={{
              labels,
              datasets: [
                buildDataset(
                  "Threshold λ",
                  data.threshold_series,
                  "#3b82f6"
                ),
              ],
            }}
          />
        </div>

        <div className="chart-card">
          <Line
            options={lineChartOptions("Prediction MAE", "MAE (invocations)")}
            data={{
              labels,
              datasets: [
                buildDataset(
                  "LSTM Prediction MAE",
                  data.prediction_mae_series,
                  "#8b5cf6"
                ),
              ],
            }}
          />
        </div>

        <div className="chart-card">
          <Line
            options={lineChartOptions("Over-Provisioning Rate", "Rate", 0, 1)}
            data={{
              labels,
              datasets: [
                buildDataset(
                  "Over-Provisioning Rate",
                  data.over_provision_rate_series,
                  "#f59e0b"
                ),
              ],
            }}
          />
        </div>
      </section>

      <footer className="footer">
        Adaptive Cold-Start Prediction · Pune Institute of Computer Technology ·
        Data refreshes every {POLL_INTERVAL_MS / 1000}s
      </footer>
    </div>
  );
}
