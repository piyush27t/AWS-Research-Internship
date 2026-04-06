#!/usr/bin/env python3
"""
scripts/preprocess.py
──────────────────────────────────────────────────────────────────────────────
Runs the full preprocessing pipeline for Kaggle AWS Cold Start dataset:
  1. Load raw dataset (Excel/CSV) from data/raw/
  2. Build invocation time-series (5-minute windows)
  3. Simulate cold start labels
  4. Engineer features (rolling stats, temporal, metadata)
  5. Split into train / val / test partitions
  6. Fit and apply MinMaxScaler (fit on train only)
  7. Save processed datasets to data/processed/ as Parquet

Run this script once after placing the Kaggle dataset in data/raw/.
Output artifacts are consumed by scripts/train.py.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd
import yaml

# ── project root on path ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.preprocessing.kaggle_loader import KaggleAWSLoader
from src.preprocessing.timeseries import TimeSeriesBuilder
from src.preprocessing.cold_start_sim import ColdStartSimulator
from src.preprocessing.features import FeatureEngineer, SequenceBuilder

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("preprocess")


def load_config() -> dict:
    with open(PROJECT_ROOT / "configs" / "config.yaml") as f:
        return yaml.safe_load(f)


def temporal_split(
    df: pd.DataFrame, train_ratio: float, val_ratio: float
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Split chronologically — NO shuffling.
    Ratios are applied to the sorted window index range.
    """
    w_min = df["window"].min()
    w_max = df["window"].max()
    span = w_max - w_min

    train_end = w_min + int(span * train_ratio)
    val_end = train_end + int(span * val_ratio)

    train = df[df["window"] <= train_end].copy()
    val = df[(df["window"] > train_end) & (df["window"] <= val_end)].copy()
    test = df[df["window"] > val_end].copy()

    logger.info(
        "Split: train=%d rows, val=%d rows, test=%d rows",
        len(train), len(val), len(test),
    )
    return train, val, test


def main() -> None:
    config = load_config()
    processed_dir = PROJECT_ROOT / config["data"]["processed_dir"]
    processed_dir.mkdir(parents=True, exist_ok=True)
    models_dir = PROJECT_ROOT / config["models"]["save_dir"]
    models_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Load raw Kaggle dataset ───────────────────────────────────────
    logger.info("Step 1/6 — Loading Kaggle dataset ...")
    loader = KaggleAWSLoader(config)
    events_df = loader.load_dataset()

    logger.info(
        "Dataset loaded: %d rows, %d collections, columns: %s",
        len(events_df),
        events_df["collection_id"].nunique(),
        list(events_df.columns),
    )

    # Store metadata for feature engineering (scheduling_class + priority)
    # These columns are guaranteed present by KaggleAWSLoader._validate_columns()
    metadata_df = events_df[["collection_id", "scheduling_class", "priority"]].copy()

    # ── Step 2: Build time-series ─────────────────────────────────────────────
    logger.info("Step 2/6 — Building invocation time-series ...")
    min_series_length = config["arima"].get("min_series_length", 10)
    ts_builder = TimeSeriesBuilder(
        window_seconds=config["timeseries"]["window_seconds"],
        min_series_length=min_series_length,
    )
    timeseries_df = ts_builder.build(events_df)
    del events_df  # free memory

    if timeseries_df.empty:
        raise RuntimeError(
            "Time-series DataFrame is empty after building. "
            "Check invocation_event_types filter and data/raw/ contents."
        )
    logger.info(
        "Time-series built: %d collections, %d windows, invocation_count stats: "
        "mean=%.2f, max=%.0f, zero_frac=%.1f%%",
        timeseries_df["collection_id"].nunique(),
        timeseries_df["window"].nunique(),
        timeseries_df["invocation_count"].mean(),
        timeseries_df["invocation_count"].max(),
        (timeseries_df["invocation_count"] == 0).mean() * 100,
    )

    # ── Step 3: Simulate cold starts ──────────────────────────────────────────
    logger.info("Step 3/6 — Simulating cold start labels ...")
    cs_sim = ColdStartSimulator(
        cold_start_threshold_min=config["timeseries"]["cold_start_threshold_min"],
        window_seconds=config["timeseries"]["window_seconds"],
    )
    annotated_df = cs_sim.annotate(timeseries_df)

    # ── Step 4: Feature engineering ───────────────────────────────────────────
    logger.info("Step 4/6 — Engineering features ...")
    fe = FeatureEngineer(
        rolling_k=config["features"]["rolling_window_k"],
        window_seconds=config["timeseries"]["window_seconds"],
    )
    feature_df = fe.build_features(annotated_df, metadata_df)

    # ── Step 5: Temporal split ────────────────────────────────────────────────
    logger.info("Step 5/6 — Splitting data ...")
    split_cfg = config["split"]
    train_df, val_df, test_df = temporal_split(
        feature_df,
        train_ratio=split_cfg["train_ratio"],
        val_ratio=split_cfg["val_ratio"],
    )

    # ── Step 6: Fit scaler on train, transform all ────────────────────────────
    logger.info("Step 6/6 — Fitting scaler and normalizing ...")
    train_norm = fe.fit_transform(train_df)
    val_norm = fe.transform(val_df)
    test_norm = fe.transform(test_df)

    scaler_path = models_dir / config["models"]["scaler_filename"]
    fe.save_scaler(scaler_path)

    # ── Save to Parquet ───────────────────────────────────────────────────────
    logger.info("Saving processed datasets to %s ...", processed_dir)

    train_norm.to_parquet(processed_dir / "train.parquet", index=False)
    val_norm.to_parquet(processed_dir / "val.parquet", index=False)
    test_norm.to_parquet(processed_dir / "test.parquet", index=False)

    # Also save un-normalized test set for cold start simulation evaluation
    test_df.to_parquet(processed_dir / "test_raw.parquet", index=False)

    # Save full annotated series for ARIMA (raw counts, chronological)
    annotated_df.to_parquet(processed_dir / "timeseries_annotated.parquet", index=False)

    logger.info("Preprocessing complete.")
    logger.info("  train : %s", processed_dir / "train.parquet")
    logger.info("  val   : %s", processed_dir / "val.parquet")
    logger.info("  test  : %s", processed_dir / "test.parquet")
    logger.info("  scaler: %s", scaler_path)
    logger.info("Next step: python scripts/train.py")


if __name__ == "__main__":
    main()
