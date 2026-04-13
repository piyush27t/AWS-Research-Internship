import React from "react";

/**
 * MetricsPanel Component
 * Displays detailed metrics breakdown in a clean, expandable format
 */
export function MetricsPanel({ data, health }) {
  const [expanded, setExpanded] = React.useState(false);

  if (!data) return null;

  const latest = data.latest_cycle;
  const metrics = [
    {
      label: "Model Status",
      value: health?.model_loaded ? "✓ Ready" : "⚠ Not Loaded",
      type: "status",
    },
    { label: "Total Cycles", value: data.n_cycles, type: "number" },
    {
      label: "Cold Start Rate",
      value: latest ? `${(latest.cold_start_rate * 100).toFixed(2)}%` : "—",
      type: "metric",
    },
    {
      label: "Over-Provisioning",
      value: latest ? `${(latest.over_provision_rate * 100).toFixed(2)}%` : "—",
      type: "metric",
    },
    {
      label: "Pre-Warmed Functions",
      value: latest?.n_pre_warmed || 0,
      type: "number",
    },
    {
      label: "Current Threshold (λ)",
      value: data.current_threshold?.toFixed(3) || "—",
      type: "metric",
    },
  ];

  return (
    <div className="metrics-panel">
      <button
        className="metrics-toggle"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <span>📊 Detailed Metrics</span>
        <span className="toggle-icon">{expanded ? "▼" : "▶"}</span>
      </button>

      {expanded && (
        <div className="metrics-content">
          <div className="metrics-grid">
            {metrics.map((m, idx) => (
              <div key={idx} className={`metric-item metric-${m.type}`}>
                <div className="metric-label">{m.label}</div>
                <div className="metric-value">{m.value}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
