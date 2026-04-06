"""
src/api/app.py
──────────────────────────────────────────────────────────────────────────────
FastAPI application exposing:
    POST /predict          — run LSTM inference + pre-warm decision
    GET  /metrics          — current performance stats
    POST /retrain          — trigger offline retraining job
    GET  /dashboard-data   — aggregated data for frontend dashboard

All parameters from config.yaml; no hardcoded values.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import time
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ── config ────────────────────────────────────────────────────────────────────
_CONFIG_PATH = Path(__file__).parents[2] / "configs" / "config.yaml"

def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)

CONFIG = _load_config()

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Cold-Start Prediction API",
    description="Adaptive pre-warming for AWS Lambda via LSTM forecasting.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CONFIG["api"]["cors_origins"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── global state (loaded at startup) ─────────────────────────────────────────
_lstm_model = None
_scaler = None
_feedback_loop = None
_recent_windows: dict[str, list[float]] = {}  # job_id → last seq_len counts


# ── startup / shutdown ────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    global _lstm_model, _scaler, _feedback_loop

    models_dir = Path(CONFIG["models"]["save_dir"])

    # Load LSTM
    lstm_path = models_dir / CONFIG["models"]["lstm_filename"]
    if lstm_path.exists():
        from src.forecasting.lstm_model import LSTMForecaster
        fc = LSTMForecaster(CONFIG)
        fc.load(lstm_path)
        _lstm_model = fc
        logger.info("LSTM model loaded.")
    else:
        logger.warning("No trained LSTM model found at %s. Train first.", lstm_path)

    # Load scaler
    scaler_path = models_dir / CONFIG["models"]["scaler_filename"]
    if scaler_path.exists():
        with open(scaler_path, "rb") as f:
            _scaler = pickle.load(f)
        logger.info("Scaler loaded.")

    # Initialize feedback loop
    from src.api.feedback_loop import FeedbackLoop
    _feedback_loop = FeedbackLoop(CONFIG)
    logger.info("Feedback loop initialized. λ=%.3f", _feedback_loop.current_threshold)


# ── request / response models ─────────────────────────────────────────────────

class InvocationWindow(BaseModel):
    job_id: str
    recent_counts: list[float]    # last seq_len invocation counts (normalized or raw)
    features: list[float] | None = None  # optional extra feature values


class PredictRequest(BaseModel):
    windows: list[InvocationWindow]


class PredictResponse(BaseModel):
    predictions: dict[str, float]          # job_id → predicted count
    pre_warm_decisions: dict[str, bool]    # job_id → should pre-warm?
    threshold: float
    timestamp: float


class MetricsResponse(BaseModel):
    threshold: float
    recent_cycles: list[dict[str, Any]]
    threshold_history: list[float]


class FeedbackRequest(BaseModel):
    actual_invocations: dict[str, float]   # job_id → observed count
    pre_warmed_jobs: list[str]
    cold_starts_observed: int
    total_invocations: int
    predicted_invocations: dict[str, float]


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.post("/predict", response_model=PredictResponse)
async def predict(request: PredictRequest) -> PredictResponse:
    """
    Run LSTM inference for each provided job window and return pre-warm decisions.
    """
    if _lstm_model is None:
        raise HTTPException(status_code=503, detail="Model not loaded. Run training first.")

    seq_len = CONFIG["lstm"].get("sequence_length", 10)
    n_features = len(CONFIG["data"]["task_events_columns"])  # fallback feature count

    predictions: dict[str, float] = {}
    decisions: dict[str, bool] = {}
    threshold = _feedback_loop.current_threshold if _feedback_loop else CONFIG["decision"]["initial_threshold"]

    for window in request.windows:
        counts = window.recent_counts[-seq_len:]
        if len(counts) < seq_len:
            counts = [0.0] * (seq_len - len(counts)) + counts

        # Build feature vector: counts + any extra features padded with zeros
        extra = window.features or []
        feature_row = counts[:]  # minimal: just use counts as feature per timestep
        # Shape: (1, seq_len, 1) or (1, seq_len, n_features)
        X = np.array([[c] for c in counts], dtype=np.float32)[np.newaxis, ...]

        try:
            pred = float(_lstm_model.predict(X)[0])
        except Exception as exc:
            logger.error("Prediction error for job %s: %s", window.job_id, exc)
            pred = 0.0

        pred = max(0.0, pred)
        predictions[window.job_id] = pred
        decisions[window.job_id] = pred > threshold

    return PredictResponse(
        predictions=predictions,
        pre_warm_decisions=decisions,
        threshold=threshold,
        timestamp=time.time(),
    )


@app.post("/feedback")
async def feedback(request: FeedbackRequest):
    """
    Submit cycle outcomes to update the adaptive threshold.
    """
    if _feedback_loop is None:
        raise HTTPException(status_code=503, detail="Feedback loop not initialized.")

    metrics = _feedback_loop.process_cycle(
        predicted=request.predicted_invocations,
        actual=request.actual_invocations,
        pre_warmed_jobs=request.pre_warmed_jobs,
        cold_starts_observed=request.cold_starts_observed,
        total_invocations=request.total_invocations,
    )

    return {
        "new_threshold": metrics.threshold,
        "prediction_mae": metrics.prediction_mae,
        "cold_start_rate": metrics.cold_start_rate,
        "over_provision_rate": metrics.over_provision_rate,
    }


@app.get("/metrics", response_model=MetricsResponse)
async def get_metrics():
    if _feedback_loop is None:
        raise HTTPException(status_code=503, detail="Not initialized.")

    return MetricsResponse(
        threshold=_feedback_loop.current_threshold,
        recent_cycles=_feedback_loop.load_recent_metrics(50),
        threshold_history=_feedback_loop.get_threshold_history(),
    )


@app.get("/dashboard-data")
async def dashboard_data():
    """Aggregated data for the React frontend dashboard."""
    if _feedback_loop is None:
        return {"status": "initializing"}

    recent = _feedback_loop.load_recent_metrics(100)

    cold_start_series = [r["cold_start_rate"] for r in recent]
    threshold_series = [r["threshold"] for r in recent]
    mae_series = [r["prediction_mae"] for r in recent]
    over_prov_series = [r["over_provision_rate"] for r in recent]

    return {
        "current_threshold": _feedback_loop.current_threshold,
        "cold_start_rate_series": cold_start_series,
        "threshold_series": threshold_series,
        "prediction_mae_series": mae_series,
        "over_provision_rate_series": over_prov_series,
        "n_cycles": len(recent),
        "latest_cycle": recent[-1] if recent else None,
    }


@app.post("/retrain")
async def retrain(background_tasks: BackgroundTasks):
    """Trigger an offline retraining job in the background."""
    background_tasks.add_task(_run_retrain)
    return {"status": "retrain job queued"}


async def _run_retrain():
    import subprocess
    logger.info("Starting offline retraining...")
    result = subprocess.run(
        ["python", "scripts/train.py"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        logger.info("Retraining completed successfully.")
    else:
        logger.error("Retraining failed: %s", result.stderr)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model_loaded": _lstm_model is not None,
        "scaler_loaded": _scaler is not None,
        "threshold": _feedback_loop.current_threshold if _feedback_loop else None,
    }
