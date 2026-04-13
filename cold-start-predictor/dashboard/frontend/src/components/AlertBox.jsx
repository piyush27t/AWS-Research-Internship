import React from "react";

/**
 * AlertBox Component
 * Displays dismissible alert messages
 */
export function AlertBox({ type = "info", message, onDismiss }) {
  const icons = {
    info: "🔵",
    warning: "⚠️",
    error: "❌",
    success: "✅",
  };

  return (
    <div className={`alert-box alert-${type}`}>
      <span className="alert-icon">{icons[type]}</span>
      <span className="alert-message">{message}</span>
      {onDismiss && (
        <button className="alert-close" onClick={onDismiss}>
          ×
        </button>
      )}
    </div>
  );
}
