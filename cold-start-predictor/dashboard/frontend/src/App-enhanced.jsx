// dashboard/frontend/src/App.jsx
// ──────────────────────────────────────────────────────────────────────────────
// Real-time monitoring dashboard for the cold-start prediction system.
// Features:
//   - Live charts (Cold Start Rate, Threshold, MAE, Over-Provisioning)
//   - Real-time metrics & system health
//   - Admin controls (Retrain, Threshold adjustment)
//   - Error handling & connection retry
//   - Auto-refresh every 30 seconds
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
import { MetricsPanel } from "./components/MetricsPanel";
import { ControlPanel } from "./components/ControlPanel";
import { AlertBox } from "./components/AlertBox";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./App.css";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
);

// ── Constants ──────────────────────────────────────────────────────────────────
const POLL_INTERVAL_MS = parseInt(
  process.env.REACT_APP_POLL_INTERVAL || "30000",
);
const API_BASE = process.env.REACT_APP_API_BASE || "";

// ── Chart Options ──────────────────────────────────────────────────────────────
const lineChartOptions = (titleText, yLabel, yMin = 0, yMax = null) => ({
  responsive: true,
  maintainAspectRatio: true,
  animation: false,
  plugins: {
    legend: {
      position: "top",
      labels: { usePointStyle: true, padding: 12 },
    },
    title: {
      display: true,
      text: titleText,
      font: { size: 13, weight: 600 },
    },
    tooltip: {
      backgroundColor: "rgba(0,0,0,0.8)",
      padding: 10,
      borderColor: "#3b82f6",
      borderWidth: 1,
    },
  },
  scales: {
    x: {
      title: { display: true, text: "Cycle", font: { size: 11 } },
      grid: { color: "#334155" },
    },
    y: {
      title: { display: true, text: yLabel, font: { size: 11 } },
      min: yMin,
      ...(yMax !== null ? { max: yMax } : {}),
      grid: { color: "#334155" },
    },
  },
});

// ── Helper Functions ───────────────────────────────────────────────────────────
function buildDataset(label, data, color) {
  return {
    label,
    data: data || [],
    borderColor: color,
    backgroundColor: color + "22",
    fill: true,
    tension: 0.3,
    pointRadius: 2,
    borderWidth: 2,
  };
}

function cycleLabels(n) {
  return Array.from({ length: n }, (_, i) => String(i + 1));
}

// ── StatCard Component ─────────────────────────────────────────────────────────
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

// ── Main App Component ─────────────────────────────────────────────────────────
function AppContent() {
  const [data, setData] = useState(null);
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [alert, setAlert] = useState(null);

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
      const msg = err.response?.statusText || err.message || "Unknown error";
      setError(`Cannot reach API (${msg}). Auto-retrying in 30s...`);
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
      setAlert({
        type: "success",
        message: "✓ Retraining initiated. Check server logs.",
      });
    } catch {
      setAlert({ type: "error", message: "✗ Failed to start retraining." });
    }
  };

  const handleThresholdChange = async (newThreshold) => {
    try {
      await axios.post(`${API_BASE}/feedback`, {
        adaptive_threshold: newThreshold,
      });
      setAlert({
        type: "success",
        message: `✓ Threshold updated to ${newThreshold}`,
      });
      await fetchData();
    } catch {
      setAlert({ type: "error", message: "✗ Failed to update threshold." });
    }
  };

  // Error state
  if (error && !data) {
    return (
      <div className="app error-screen">
        <h2>⚠️ Connection Error</h2>
        <p>{error}</p>
        <button onClick={fetchData} className="btn-primary">
          Retry Now
        </button>
      </div>
    );
  }

  if (!data) {
    return <div className="app loading">📊 Loading dashboard data…</div>;
  }

  // Extract data
  const n = data.n_cycles || 0;
  const labels = cycleLabels(n);
  const latest = data.latest_cycle || {};

  const latestCS =
    latest?.cold_start_rate !== undefined
      ? (latest.cold_start_rate * 100).toFixed(1)
      : null;
  const latestOP =
    latest?.over_provision_rate !== undefined
      ? (latest.over_provision_rate * 100).toFixed(1)
      : null;
  const latestMAE = latest?.prediction_mae
    ? latest.prediction_mae.toFixed(3)
    : null;
  const latestThreshold = data.current_threshold?.toFixed(3);

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div>
          <h1>🚀 Cold-Start Prediction Monitor</h1>
          <span className="subtitle">
            AWS Lambda · LSTM + ARIMA · Adaptive Pre-Warming
          </span>
        </div>
        <div className="header-right">
          {health && (
            <span
              className={`status-badge ${health.model_loaded ? "ok" : "warn"}`}
            >
              {health.model_loaded ? "● Ready" : "○ No Model"}
            </span>
          )}
          <span className="last-updated">Updated: {lastUpdated || "—"}</span>
        </div>
      </header>

      {/* Alert Messages */}
      {alert && (
        <AlertBox
          type={alert.type}
          message={alert.message}
          onDismiss={() => setAlert(null)}
        />
      )}

      {/* Control Panel */}
      <ControlPanel
        onRetrain={handleRetrain}
        onThresholdChange={handleThresholdChange}
        currentThreshold={data.current_threshold}
      />

      {/* Metrics Panel */}
      <MetricsPanel data={data} health={health} />

      {/* Key Stats */}
      <section className="stat-grid">
        <StatCard title="Cold Start Rate" value={latestCS} unit="%" />
        <StatCard title="Over-Provisioning Rate" value={latestOP} unit="%" />
        <StatCard title="Prediction MAE" value={latestMAE} unit="inv." />
        <StatCard title="Threshold (λ)" value={latestThreshold} highlight />
        <StatCard title="Cycles Recorded" value={n} />
        <StatCard
          title="Pre-Warmed (last)"
          value={latest?.n_pre_warmed || 0}
          unit="funcs"
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
                  "#ef4444",
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
                buildDataset("Threshold λ", data.threshold_series, "#3b82f6"),
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
                  "#8b5cf6",
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
                  "#f59e0b",
                ),
              ],
            }}
          />
        </div>
      </section>

      {/* Footer */}
      <footer className="footer">
        🔷 Adaptive Cold-Start Prediction · Pune Institute of Computer
        Technology
        <br />
        Data refreshes every {POLL_INTERVAL_MS / 1000}s
        {health?.model_loaded && ` · Model: ${health.model_type || "LSTM"}`}
      </footer>
    </div>
  );
}

// ── Export with Error Boundary ─────────────────────────────────────────────────
export default function App() {
  return (
    <ErrorBoundary>
      <AppContent />
    </ErrorBoundary>
  );
}
