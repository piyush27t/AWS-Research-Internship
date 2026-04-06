"""
tests/test_forecasting.py
──────────────────────────────────────────────────────────────────────────────
Unit tests for the forecasting and feedback modules.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_config() -> dict:
    with open(PROJECT_ROOT / "configs" / "config.yaml") as f:
        return yaml.safe_load(f)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def config() -> dict:
    return load_config()


@pytest.fixture
def synthetic_sequences():
    """Small synthetic LSTM input: 200 samples, seq_len=10, features=7."""
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 1, size=(200, 10, 7)).astype(np.float32)
    y = rng.uniform(0, 5, size=200).astype(np.float32)
    return X, y


@pytest.fixture
def tiny_timeseries() -> pd.DataFrame:
    """Single-job time series for ARIMA testing."""
    rng = np.random.default_rng(1)
    windows = np.arange(100)
    counts = np.clip(rng.poisson(lam=3, size=100), 0, None).astype(float)
    return pd.DataFrame({
        "collection_id": [42] * 100,
        "window": windows,
        "invocation_count": counts,
    })


# ── LSTMForecaster tests ──────────────────────────────────────────────────────

class TestLSTMForecaster:
    def test_build_creates_model(self, config, synthetic_sequences):
        from src.forecasting.lstm_model import LSTMForecaster
        X, y = synthetic_sequences
        fc = LSTMForecaster(config)
        fc.build(n_features=X.shape[2])
        assert fc.model is not None

    def test_predict_shape(self, config, synthetic_sequences):
        from src.forecasting.lstm_model import LSTMForecaster
        X, y = synthetic_sequences
        fc = LSTMForecaster(config)
        fc.build(n_features=X.shape[2])
        preds = fc.predict(X)
        assert preds.shape == (len(X),)

    def test_predict_non_negative(self, config, synthetic_sequences):
        from src.forecasting.lstm_model import LSTMForecaster
        X, y = synthetic_sequences
        fc = LSTMForecaster(config)
        fc.build(n_features=X.shape[2])
        preds = fc.predict(X)
        assert (preds >= 0).all()

    def test_predict_without_build_raises(self, config):
        from src.forecasting.lstm_model import LSTMForecaster
        fc = LSTMForecaster(config)
        X = np.zeros((5, 10, 7), dtype=np.float32)
        with pytest.raises(RuntimeError):
            fc.predict(X)

    def test_mae_rmse_types(self, config, synthetic_sequences):
        from src.forecasting.lstm_model import LSTMForecaster
        X, y = synthetic_sequences
        fc = LSTMForecaster(config)
        fc.build(n_features=X.shape[2])
        mae, rmse = fc.evaluate(X, y)
        assert isinstance(mae, float)
        assert isinstance(rmse, float)
        assert mae >= 0
        assert rmse >= mae  # RMSE ≥ MAE always


# ── ARIMAForecaster tests ─────────────────────────────────────────────────────

class TestARIMAForecaster:
    def test_fit_and_predict(self, config, tiny_timeseries):
        from src.forecasting.arima_model import ARIMAForecaster
        arima = ARIMAForecaster(config)
        arima.fit(tiny_timeseries)
        pred = arima.predict_one_step(collection_id=42)
        assert isinstance(pred, float)
        assert pred >= 0

    def test_unknown_job_returns_zero(self, config, tiny_timeseries):
        from src.forecasting.arima_model import ARIMAForecaster
        arima = ARIMAForecaster(config)
        arima.fit(tiny_timeseries)
        pred = arima.predict_one_step(collection_id=9999)
        assert pred == 0.0

    def test_models_stored(self, config, tiny_timeseries):
        from src.forecasting.arima_model import ARIMAForecaster
        arima = ARIMAForecaster(config)
        arima.fit(tiny_timeseries)
        assert 42 in arima.models

    def test_sparse_series_is_skipped(self, config):
        """Collections with too few non-zero windows should be skipped entirely."""
        from src.forecasting.arima_model import ARIMAForecaster
        arima = ARIMAForecaster(config, min_nonzero_windows=5)
        # Only 2 non-zero windows — below the min_nonzero_windows=5 threshold
        df = pd.DataFrame({
            "collection_id": [1] * 10,
            "window": list(range(10)),
            "invocation_count": [0.0] * 8 + [3.0, 0.0],  # 1 non-zero
        })
        arima.fit(df)
        # Should be skipped — no model stored, no warning from pmdarima
        assert 1 not in arima.models

    def test_sufficient_series_is_trained(self, config):
        """Collections with enough non-zero windows should produce a fitted model."""
        from src.forecasting.arima_model import ARIMAForecaster
        arima = ARIMAForecaster(config, min_nonzero_windows=5)
        rng = np.random.default_rng(42)
        counts = rng.poisson(lam=3, size=50).astype(float)
        df = pd.DataFrame({
            "collection_id": [99] * 50,
            "window": list(range(50)),
            "invocation_count": counts,
        })
        arima.fit(df)
        assert 99 in arima.models


# ── Evaluator tests ───────────────────────────────────────────────────────────

class TestEvaluator:
    def test_mae_rmse_correct(self, config):
        from src.forecasting.evaluator import Evaluator
        ev = Evaluator(n_jobs_total=10)
        y_true = np.array([1.0, 2.0, 3.0, 4.0])
        y_pred = np.array([1.5, 2.5, 2.5, 3.5])
        mae, rmse = ev.forecast_metrics(y_true, y_pred)
        assert pytest.approx(mae, abs=1e-4) == 0.5
        assert rmse >= mae

    def test_cold_start_reduction_with_perfect_decisions(self):
        """If all cold starts are pre-warmed, adaptive rate should be 0."""
        from src.forecasting.evaluator import Evaluator

        df = pd.DataFrame({
            "collection_id": [1, 1, 1, 2, 2],
            "window": [10, 20, 30, 10, 20],
            "invocation_count": [1, 1, 1, 1, 1],
            "is_cold_start": [True, False, True, True, False],
        })
        decisions = pd.DataFrame({
            "collection_id": [1, 1, 2],
            "window": [10, 30, 10],
            "pre_warmed": [True, True, True],
        })
        ev = Evaluator(n_jobs_total=2)
        baseline, adaptive, over_prov, always_warm_cost = ev.cold_start_metrics(df, decisions)
        assert adaptive == 0.0


# ── FeedbackLoop tests ────────────────────────────────────────────────────────

class TestFeedbackLoop:
    def test_threshold_increases_when_over_provisioned(self, config, tmp_path):
        """If over-provisioning exceeds target, threshold should increase."""
        cfg = dict(config)
        cfg["monitoring"] = {"metrics_file": str(tmp_path / "metrics.jsonl")}
        from src.api.feedback_loop import FeedbackLoop
        loop = FeedbackLoop(cfg)
        initial = loop.current_threshold

        # Simulate high over-provisioning (0.8 >> target 0.2)
        loop.process_cycle(
            predicted={"j1": 5.0},
            actual={"j1": 5.0},
            pre_warmed_jobs=["j1", "j2", "j3", "j4", "j5"],
            cold_starts_observed=0,
            total_invocations=5,
        )
        assert loop.current_threshold > initial

    def test_threshold_decreases_when_under_provisioned(self, config, tmp_path):
        """If over-provisioning is 0 (below target), threshold should decrease."""
        cfg = dict(config)
        cfg["monitoring"] = {"metrics_file": str(tmp_path / "metrics.jsonl")}
        from src.api.feedback_loop import FeedbackLoop
        loop = FeedbackLoop(cfg)
        initial = loop.current_threshold

        loop.process_cycle(
            predicted={"j1": 5.0},
            actual={"j1": 5.0},
            pre_warmed_jobs=["j1"],
            cold_starts_observed=0,
            total_invocations=10,
        )
        assert loop.current_threshold < initial

    def test_threshold_bounded(self, config, tmp_path):
        """Threshold must never exceed max or fall below min."""
        cfg = dict(config)
        cfg["monitoring"] = {"metrics_file": str(tmp_path / "metrics.jsonl")}
        from src.api.feedback_loop import FeedbackLoop
        loop = FeedbackLoop(cfg)

        for _ in range(200):
            loop.process_cycle(
                predicted={"j1": 100.0},
                actual={"j1": 0.0},
                pre_warmed_jobs=["j1"] * 20,
                cold_starts_observed=5,
                total_invocations=5,
            )

        assert loop.current_threshold <= cfg["decision"]["max_threshold"]
        assert loop.current_threshold >= cfg["decision"]["min_threshold"]

    def test_metrics_persisted(self, config, tmp_path):
        cfg = dict(config)
        metrics_file = tmp_path / "metrics.jsonl"
        cfg["monitoring"] = {"metrics_file": str(metrics_file)}
        from src.api.feedback_loop import FeedbackLoop
        loop = FeedbackLoop(cfg)
        loop.process_cycle({}, {}, [], 0, 10)
        assert metrics_file.exists()
        records = loop.load_recent_metrics()
        assert len(records) == 1
