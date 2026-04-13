import React, { useState } from "react";

/**
 * ControlPanel Component
 * Provides admin controls for manual interventions (retrain, threshold adjustment, etc.)
 */
export function ControlPanel({
  onRetrain,
  onThresholdChange,
  currentThreshold,
}) {
  const [showModal, setShowModal] = useState(false);
  const [newThreshold, setNewThreshold] = useState(currentThreshold || 0.5);
  const [retrainingInProgress, setRetrainingInProgress] = useState(false);

  const handleRetrain = async () => {
    setRetrainingInProgress(true);
    try {
      await onRetrain();
      alert("✓ Retraining job initiated. Check server logs for progress.");
    } catch (err) {
      alert("✗ Failed to start retraining job.");
    } finally {
      setRetrainingInProgress(false);
    }
  };

  const handleThresholdUpdate = async () => {
    if (onThresholdChange) {
      try {
        await onThresholdChange(newThreshold);
        alert(`✓ Threshold updated to ${newThreshold}`);
        setShowModal(false);
      } catch (err) {
        alert("✗ Failed to update threshold.");
      }
    }
  };

  return (
    <>
      <div className="control-panel">
        <h3>Admin Controls</h3>
        <div className="control-buttons">
          <button
            className="btn-primary"
            onClick={handleRetrain}
            disabled={retrainingInProgress}
          >
            {retrainingInProgress ? "⏳ Retraining..." : "↻ Retrain Model"}
          </button>
          <button className="btn-secondary" onClick={() => setShowModal(true)}>
            ⚙️ Adjust Threshold
          </button>
        </div>
      </div>

      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h2>Adjust Adaptive Threshold</h2>
            <p>
              Current: <strong>{currentThreshold?.toFixed(3)}</strong>
            </p>
            <input
              type="number"
              min="0"
              max="100"
              step="0.1"
              value={newThreshold}
              onChange={(e) => setNewThreshold(parseFloat(e.target.value))}
              placeholder="Enter new threshold"
            />
            <div className="modal-buttons">
              <button className="btn-primary" onClick={handleThresholdUpdate}>
                Update
              </button>
              <button
                className="btn-secondary"
                onClick={() => setShowModal(false)}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
