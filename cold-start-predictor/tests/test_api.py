"""
tests/test_api.py
──────────────────────────────────────────────────────────────────────────────
Integration tests for the FastAPI endpoints.
Mocks the LSTM model and feedback loop so no actual model is required.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config() -> dict:
    with open(PROJECT_ROOT / "configs" / "config.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def client(config, tmp_path):
    """
    Create a TestClient with mocked model, scaler, and feedback loop.
    """
    import src.api.app as app_module

    # Mock LSTM model
    mock_model = MagicMock()
    mock_model.predict.return_value = np.array([3.5, 0.2, 8.0])

    # Mock feedback loop
    mock_loop = MagicMock()
    mock_loop.current_threshold = 2.0
    mock_loop.load_recent_metrics.return_value = [
        {"cycle_id": 1, "threshold": 2.0, "cold_start_rate": 0.05,
         "over_provision_rate": 0.15, "prediction_mae": 1.2,
         "cold_starts": 2, "total_invocations": 40, "n_pre_warmed": 5,
         "timestamp": 1700000000.0}
    ]
    mock_loop.get_threshold_history.return_value = [2.0, 2.05, 1.98]
    mock_loop.process_cycle.return_value = MagicMock(
        threshold=2.05, prediction_mae=1.1,
        cold_start_rate=0.04, over_provision_rate=0.12,
    )

    app_module._lstm_model = mock_model
    app_module._scaler = MagicMock()
    app_module._feedback_loop = mock_loop

    with TestClient(app_module.app) as c:
        yield c


# ── /health ───────────────────────────────────────────────────────────────────

def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True


# ── /predict ──────────────────────────────────────────────────────────────────

def test_predict_returns_decisions(client):
    payload = {
        "windows": [
            {"job_id": "job_1", "recent_counts": [1.0] * 10},
            {"job_id": "job_2", "recent_counts": [0.0] * 10},
        ]
    }
    resp = client.post("/predict", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert "predictions" in body
    assert "pre_warm_decisions" in body
    assert "threshold" in body
    assert set(body["predictions"].keys()) == {"job_1", "job_2"}


def test_predict_decisions_are_bool(client):
    payload = {
        "windows": [
            {"job_id": "job_a", "recent_counts": [5.0] * 10},
        ]
    }
    resp = client.post("/predict", json=payload)
    body = resp.json()
    for decision in body["pre_warm_decisions"].values():
        assert isinstance(decision, bool)


def test_predict_empty_request(client):
    resp = client.post("/predict", json={"windows": []})
    assert resp.status_code == 200
    assert resp.json()["predictions"] == {}


# ── /feedback ─────────────────────────────────────────────────────────────────

def test_feedback_returns_new_threshold(client):
    payload = {
        "actual_invocations": {"job_1": 4.0, "job_2": 0.0},
        "pre_warmed_jobs": ["job_1", "job_2"],
        "cold_starts_observed": 1,
        "total_invocations": 5,
        "predicted_invocations": {"job_1": 3.5, "job_2": 0.2},
    }
    resp = client.post("/feedback", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert "new_threshold" in body
    assert "prediction_mae" in body
    assert "cold_start_rate" in body
    assert "over_provision_rate" in body


# ── /metrics ──────────────────────────────────────────────────────────────────

def test_metrics_structure(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert "threshold" in body
    assert "recent_cycles" in body
    assert "threshold_history" in body
    assert isinstance(body["recent_cycles"], list)
    assert isinstance(body["threshold_history"], list)


# ── /dashboard-data ───────────────────────────────────────────────────────────

def test_dashboard_data(client):
    resp = client.get("/dashboard-data")
    assert resp.status_code == 200
    body = resp.json()
    expected_keys = {
        "current_threshold",
        "cold_start_rate_series",
        "threshold_series",
        "prediction_mae_series",
        "over_provision_rate_series",
        "n_cycles",
    }
    assert expected_keys.issubset(body.keys())
