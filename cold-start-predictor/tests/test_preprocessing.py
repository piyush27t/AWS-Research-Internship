"""
tests/test_preprocessing.py
──────────────────────────────────────────────────────────────────────────────
Unit tests for the preprocessing pipeline.
Uses synthetic data — does NOT require the actual Google Cluster Dataset.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.preprocessing.timeseries import TimeSeriesBuilder
from src.preprocessing.cold_start_sim import ColdStartSimulator
from src.preprocessing.features import FeatureEngineer, SequenceBuilder


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_events() -> pd.DataFrame:
    """Minimal events DataFrame simulating loader output."""
    rng = np.random.default_rng(42)
    n = 500
    # Two jobs, timestamps spanning 2 hours (microseconds)
    timestamps = np.sort(rng.integers(0, 2 * 3600 * 1_000_000, size=n))
    job_ids = rng.choice([1001, 1002], size=n)
    return pd.DataFrame({
        "timestamp": timestamps,
        "job_id": job_ids,
        "task_index": rng.integers(0, 10, size=n),
        "scheduling_class": rng.integers(0, 4, size=n),
        "cpu_request": rng.uniform(0, 1, size=n),
        "ram_request": rng.uniform(0, 1, size=n),
    })


@pytest.fixture
def window_seconds() -> int:
    return 300  # 5-minute windows


@pytest.fixture
def timeseries_df(sample_events, window_seconds) -> pd.DataFrame:
    builder = TimeSeriesBuilder(window_seconds=window_seconds)
    return builder.build(sample_events)


@pytest.fixture
def annotated_df(timeseries_df, window_seconds) -> pd.DataFrame:
    sim = ColdStartSimulator(cold_start_threshold_min=30, window_seconds=window_seconds)
    return sim.annotate(timeseries_df)


# ── TimeSeriesBuilder tests ───────────────────────────────────────────────────

class TestTimeSeriesBuilder:
    def test_output_columns(self, timeseries_df):
        assert "job_id" in timeseries_df.columns
        assert "window" in timeseries_df.columns
        assert "invocation_count" in timeseries_df.columns

    def test_counts_non_negative(self, timeseries_df):
        assert (timeseries_df["invocation_count"] >= 0).all()

    def test_two_jobs_present(self, timeseries_df):
        assert timeseries_df["job_id"].nunique() == 2

    def test_continuous_windows_per_job(self, timeseries_df):
        """Each job should have a contiguous sequence of windows."""
        for job_id, group in timeseries_df.groupby("job_id"):
            windows = group["window"].sort_values().to_numpy()
            diffs = np.diff(windows)
            assert (diffs == 1).all(), f"Job {job_id} has gaps in windows."

    def test_total_count_matches_events(self, sample_events, timeseries_df):
        """Sum of all invocation counts should equal total input events."""
        assert timeseries_df["invocation_count"].sum() == len(sample_events)


# ── ColdStartSimulator tests ──────────────────────────────────────────────────

class TestColdStartSimulator:
    def test_cold_start_column_exists(self, annotated_df):
        assert "is_cold_start" in annotated_df.columns

    def test_cold_start_is_bool(self, annotated_df):
        assert annotated_df["is_cold_start"].dtype == bool

    def test_rate_between_zero_and_one(self, annotated_df):
        rate = annotated_df["is_cold_start"].mean()
        assert 0.0 <= rate <= 1.0

    def test_cold_start_only_on_invoked_windows(self, annotated_df):
        """Cold starts should only appear on windows with invocation_count > 0."""
        cold = annotated_df[annotated_df["is_cold_start"]]
        assert (cold["invocation_count"] > 0).all()

    def test_tight_threshold_increases_cold_starts(self, timeseries_df, window_seconds):
        """Tighter threshold (5 min) → more cold starts than 30 min."""
        sim_tight = ColdStartSimulator(cold_start_threshold_min=5, window_seconds=window_seconds)
        sim_loose = ColdStartSimulator(cold_start_threshold_min=60, window_seconds=window_seconds)
        tight = sim_tight.annotate(timeseries_df)["is_cold_start"].sum()
        loose = sim_loose.annotate(timeseries_df)["is_cold_start"].sum()
        assert tight >= loose


# ── FeatureEngineer tests ─────────────────────────────────────────────────────

class TestFeatureEngineer:
    def test_feature_columns_present(self, annotated_df, window_seconds):
        fe = FeatureEngineer(rolling_k=6, window_seconds=window_seconds)
        feature_df = fe.build_features(annotated_df)
        expected = ["invocation_count", "rolling_mean", "rolling_std",
                    "time_of_day", "day_of_week", "cpu_rate", "memory_usage"]
        for col in expected:
            assert col in feature_df.columns, f"Missing column: {col}"

    def test_time_of_day_range(self, annotated_df, window_seconds):
        fe = FeatureEngineer(rolling_k=6, window_seconds=window_seconds)
        feature_df = fe.build_features(annotated_df)
        assert (feature_df["time_of_day"] >= 0).all()
        assert (feature_df["time_of_day"] <= 1).all()

    def test_day_of_week_range(self, annotated_df, window_seconds):
        fe = FeatureEngineer(rolling_k=6, window_seconds=window_seconds)
        feature_df = fe.build_features(annotated_df)
        assert (feature_df["day_of_week"] >= 0).all()
        assert (feature_df["day_of_week"] <= 6).all()

    def test_scaler_normalizes_to_unit_interval(self, annotated_df, window_seconds):
        fe = FeatureEngineer(rolling_k=6, window_seconds=window_seconds)
        feature_df = fe.build_features(annotated_df)
        norm = fe.fit_transform(feature_df)
        from src.preprocessing.features import FEATURE_COLUMNS
        for col in FEATURE_COLUMNS:
            vals = norm[col].dropna()
            assert vals.min() >= -0.01 and vals.max() <= 1.01, f"{col} out of [0,1]"

    def test_transform_without_fit_raises(self, annotated_df, window_seconds):
        fe = FeatureEngineer(rolling_k=6, window_seconds=window_seconds)
        feature_df = fe.build_features(annotated_df)
        with pytest.raises(RuntimeError):
            fe.transform(feature_df)


# ── SequenceBuilder tests ─────────────────────────────────────────────────────

class TestSequenceBuilder:
    def test_sequence_shape(self, annotated_df, window_seconds):
        fe = FeatureEngineer(rolling_k=6, window_seconds=window_seconds)
        feature_df = fe.build_features(annotated_df)
        norm = fe.fit_transform(feature_df)
        builder = SequenceBuilder(sequence_length=10)
        X, y, job_ids = builder.build_sequences(norm)
        assert X.ndim == 3
        assert X.shape[1] == 10
        assert X.shape[0] == y.shape[0]
        assert X.shape[0] == job_ids.shape[0]

    def test_y_non_negative(self, annotated_df, window_seconds):
        fe = FeatureEngineer(rolling_k=6, window_seconds=window_seconds)
        feature_df = fe.build_features(annotated_df)
        norm = fe.fit_transform(feature_df)
        builder = SequenceBuilder(sequence_length=5)
        _, y, _ = builder.build_sequences(norm)
        assert (y >= 0).all()
