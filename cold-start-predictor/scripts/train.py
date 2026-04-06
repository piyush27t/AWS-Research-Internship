#!/usr/bin/env python3
"""
scripts/train.py
──────────────────────────────────────────────────────────────────────────────
Trains both ARIMA (baseline) and LSTM (primary) models on preprocessed data.

Pipeline:
  1. Load train / val / test Parquet files
  2. Train per-function ARIMA models in parallel
  3. Build LSTM sequence tensors
  4. Train stacked LSTM with early stopping
  5. (Optional) Grid-search LSTM hyperparameters
  6. Evaluate both models on test set
  7. Save models + metadata JSON

Expects scripts/preprocess.py to have been run first.
All hyperparameters from configs/config.yaml.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# ── project root ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.forecasting.arima_model import ARIMAForecaster
from src.forecasting.lstm_model import LSTMForecaster
from src.forecasting.evaluator import Evaluator
from src.preprocessing.features import SequenceBuilder, FEATURE_COLUMNS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("train")

COL_COLLECTION_ID = "collection_id"
COL_WINDOW = "window"
COL_COUNT = "invocation_count"
COL_COLD_START = "is_cold_start"


def load_config() -> dict:
    with open(PROJECT_ROOT / "configs" / "config.yaml") as f:
        return yaml.safe_load(f)


def load_processed(processed_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    logger.info("Loading preprocessed data from %s ...", processed_dir)
    train = pd.read_parquet(processed_dir / "train.parquet")
    val = pd.read_parquet(processed_dir / "val.parquet")
    test = pd.read_parquet(processed_dir / "test.parquet")
    test_raw = pd.read_parquet(processed_dir / "test_raw.parquet")
    logger.info(
        "Loaded — train: %d, val: %d, test: %d rows.",
        len(train), len(val), len(test),
    )
    return train, val, test, test_raw


def build_lstm_sequences(
    train: pd.DataFrame,
    val: pd.DataFrame,
    test: pd.DataFrame,
    seq_len: int,
) -> tuple:
    """Build (X, y, collection_ids) arrays for train / val / test partitions."""
    builder = SequenceBuilder(sequence_length=seq_len)
    X_train, y_train, collections_train = builder.build_sequences(train)
    X_val, y_val, collections_val = builder.build_sequences(val)
    X_test, y_test, collections_test = builder.build_sequences(test)
    return (X_train, y_train, collections_train), (X_val, y_val, collections_val), (X_test, y_test, collections_test)


def make_pre_warm_decisions(
    test_raw: pd.DataFrame,
    lstm: LSTMForecaster,
    X_test: np.ndarray,
    collections_test: np.ndarray,
    config: dict,
    models_dir: Path,
) -> pd.DataFrame:
    """
    Computes pre-warm decisions using a local adaptive threshold (Rolling Outlier Detection).
    """
    preds = lstm.predict(X_test)
    
    # ── Policy Configuration ─────────────────────────────────────────────────
    policy = config.get("decision", {})
    method = policy.get("policy_method", "rolling") # Default to new Dynamic
    
    roll_window = int(policy.get("rolling_window", 50))
    roll_quantile = float(policy.get("rolling_quantile", 90)) / 100.0
    k_factor = float(policy.get("k_factor", 1.5))
    
    # Global Parameters (for 'percentile' method)
    global_p_val = float(policy.get("policy_value", 85))
    
    use_inverse = policy.get("use_inverse_scale", True)
    persistence = int(policy.get("persistence_windows", 1))
    cooldown = int(policy.get("cooldown_windows", 1))
    ema_alpha = float(policy.get("ema_alpha", 0.3)) # Industrial low-pass filter
    
    # Optional Inverse Scaling
    display_preds = preds
    if use_inverse:
        scaler_path = models_dir / config["models"]["scaler_filename"]
        if scaler_path.exists():
            with open(scaler_path, "rb") as f:
                scaler = pickle.load(f)
            scale = scaler.data_max_[0] - scaler.data_min_[0]
            bias = scaler.data_min_[0]
            display_preds = preds * scale + bias
            logger.info("Using inverse-scaled predictions for policy (Actual counts).")
    
    # Calculate Global Percentiles
    global_threshold = 0.0
    smear_threshold = 0.0
    
    if method == "percentile":
        global_threshold = float(np.percentile(display_preds, global_p_val))
        
        # High-confidence threshold for smearing
        smear_perc = float(policy.get("smear_threshold", 92))
        smear_threshold = float(np.percentile(display_preds, smear_perc))
        
        logger.info("Applying GLOBAL Policy (Method: Percentile, p%.1f, Thr=%.4f) ...", 
                    global_p_val, global_threshold)
        logger.info("  Smear Threshold (p%.1f): %.4f", smear_perc, smear_threshold)
    else:
        logger.info("Applying DYNAMIC Policy (Method: Rolling, Win=%d, P%.1f, k=%.1f) ...", 
                    roll_window, roll_quantile*100, k_factor)

    # ── per-Collection Adaptive Filtering ────────────────────────────────────
    refined_decisions = np.zeros_like(display_preds, dtype=bool)
    seq_len = X_test.shape[1]
    records = []

    for collection_id, group in test_raw.groupby(COL_COLLECTION_ID, sort=True):
        group = group.sort_values(COL_WINDOW)
        windows = group[COL_WINDOW].to_numpy()
        n = len(windows)
        
        mask = collections_test == collection_id
        col_raw = display_preds[mask]
        
        if len(col_raw) == 0:
            continue
            
        # 1. EMA Smoothing
        ema_series = pd.Series(col_raw).ewm(alpha=ema_alpha, adjust=False).mean()
        
        # 2. Decision logic based on Method
        if method == "percentile":
            base_trigger = ema_series > global_threshold
            can_smear = ema_series > smear_threshold
        elif method == "rolling":
            # Local stats (Dynamic Outlier Detection)
            roll_mean = ema_series.rolling(window=roll_window, min_periods=1).mean()
            roll_perc = ema_series.rolling(window=roll_window, min_periods=1).quantile(roll_quantile)
            base_trigger = (ema_series > roll_perc) & (ema_series > k_factor * roll_mean)
            can_smear = base_trigger # always smear if dynamic trigger (safer default)
        else: # static physical threshold fallback
            base_trigger = ema_series > global_p_val # interpret p_val as physical counts
            can_smear = base_trigger

        # 3. State Machine (Persistence + Cooldown + Lead Buffer)
        col_refined = np.zeros_like(col_raw, dtype=bool)
        consecutive_count = 0
        cooldown_timer = 0
        
        lead_buffer = int(policy.get("lead_buffer", 1)) # Extra windows for high-confidence spikes
        buffer_timer = 0
        
        for i in range(len(col_raw)):
            if cooldown_timer > 0:
                cooldown_timer -= 1
            
            if buffer_timer > 0:
                buffer_timer -= 1
                col_refined[i] = True
            
            if base_trigger.iloc[i]:
                consecutive_count += 1
            else:
                consecutive_count = 0
            
            should_trigger = (consecutive_count >= persistence) and (cooldown_timer == 0)
            
            if should_trigger:
                col_refined[i] = True
                
                # Confidence-weighted smearing:
                # Top 5% (Ultra-burst) gets 2 windows. 
                # Top 25% (Standard) gets 0 extra windows to save cost.
                if ema_series.iloc[i] > smear_threshold:
                    buffer_timer = lead_buffer 
                else:
                    buffer_timer = 0
                    
                cooldown_timer = cooldown + buffer_timer + 1
                
            idx = np.where(mask)[0][i]
            refined_decisions[idx] = col_refined[i]
            
            target_window = windows[seq_len + i] if (seq_len + i) < n else windows[-1]
            records.append({
                COL_COLLECTION_ID: collection_id,
                COL_WINDOW: int(target_window),
                "pre_warmed": bool(col_refined[i]),
            })

    # --- Final Diagnostics ---
    n_triggered = int(np.sum(refined_decisions))
    trigger_rate = (n_triggered / len(preds) * 100) if len(preds) > 0 else 0
    logger.info("-" * 40)
    logger.info("DYNAMIC ADAPTIVE POLICY RESULTS:")
    logger.info("  Triggers: %d (%.2f%%)", n_triggered, trigger_rate)
    logger.info("-" * 40)

    return pd.DataFrame(records)


def save_metadata(
    models_dir: Path,
    config: dict,
    arima_mae: float,
    arima_rmse: float,
    lstm_mae: float,
    lstm_rmse: float,
    n_collections: int,
    train_duration_s: float,
) -> None:
    metadata = {
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": config,
        "n_collections": n_collections,
        "train_duration_seconds": round(train_duration_s, 1),
        "arima": {"mae": arima_mae, "rmse": arima_rmse},
        "lstm": {"mae": lstm_mae, "rmse": lstm_rmse},
    }
    meta_path = models_dir / config["models"]["metadata_filename"]
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Training metadata saved to %s", meta_path)


def main() -> None:
    t0 = time.time()
    config = load_config()

    processed_dir = PROJECT_ROOT / config["data"]["processed_dir"]
    models_dir = PROJECT_ROOT / config["models"]["save_dir"]
    models_dir.mkdir(parents=True, exist_ok=True)

    # ── Flags ─────────────────────────────────────────────────────────────────
    force_retrain = os.environ.get("RETRAIN", "0") == "1"
    arima_path = models_dir / config["models"]["arima_filename"]
    lstm_path = models_dir / config["models"]["lstm_filename"]

    # ── Load data ─────────────────────────────────────────────────────────────
    train_df, val_df, test_df, test_raw = load_processed(processed_dir)
    n_collections = train_df[COL_COLLECTION_ID].nunique()
    seq_len = config["features"]["sequence_length"]

    # ── Phase 1: ARIMA baseline ───────────────────────────────────────────────
    arima = ARIMAForecaster(config)
    if force_retrain or not arima_path.exists():
        logger.info("=" * 55)
        logger.info("PHASE 1: Training ARIMA baseline models (%d collections) ...", n_collections)
        
        annotated = pd.read_parquet(processed_dir / "timeseries_annotated.parquet")
        train_windows = train_df[COL_WINDOW].unique()
        arima_train = annotated[annotated[COL_WINDOW].isin(train_windows)]
        arima.fit(arima_train)
        arima.save(arima_path)
    else:
        logger.info("=" * 55)
        logger.info("PHASE 1: Loading existing ARIMA models from %s", arima_path)
        arima.load(arima_path)

    # Calculate ARIMA Evaluation (needed for the report regardless)
    annotated = pd.read_parquet(processed_dir / "timeseries_annotated.parquet")
    arima_test_windows = test_raw[COL_WINDOW].unique()
    arima_test = annotated[annotated[COL_WINDOW].isin(arima_test_windows)]
    arima_mae, arima_rmse = arima.evaluate(arima_test)

    # ── Phase 2: LSTM sequences ────────────────────────────────────────────────
    logger.info("=" * 55)
    logger.info("Building LSTM sequences (seq_len=%d) ...", seq_len)
    (X_train, y_train, collections_train), (X_val, y_val, collections_val), (X_test, y_test, collections_test) = \
        build_lstm_sequences(train_df, val_df, test_df, seq_len)

    # ── Phase 3: LSTM Model ───────────────────────────────────────────────────
    lstm = LSTMForecaster(config)
    if force_retrain or not lstm_path.exists():
        logger.info("=" * 55)
        logger.info("PHASE 3: Training LSTM model ...")
        lstm.build(n_features=X_train.shape[2])

        # Optional: grid search (set RUN_GRID_SEARCH=1 env var to enable)
        if os.environ.get("RUN_GRID_SEARCH") == "1":
            logger.info("Running hyperparameter grid search ...")
            best_params = lstm.grid_search(
                X_train, y_train, X_val, y_val,
                param_grid=config["lstm"]["grid_search"],
            )
            logger.info("Best hyperparameters: %s", best_params)
            # Rebuild model with best params
            lstm = LSTMForecaster(config)
            lstm.build(n_features=X_train.shape[2])

        history = lstm.fit(X_train, y_train, X_val, y_val, checkpoint_path=models_dir / "lstm_checkpoint.keras")
        lstm.save(lstm_path)
        logger.info("LSTM model trained and saved to %s", lstm_path)
        
        # Save training history for research paper plots
        history_dict = history.history
        with open(models_dir / "training_history.json", "w") as f:
            json.dump(history_dict, f, indent=2)
        logger.info("Training history saved to %s", models_dir / "training_history.json")
    else:
        logger.info("=" * 55)
        logger.info("PHASE 3: Loading existing LSTM model from %s", lstm_path)
        lstm.load(lstm_path)

    # Phase 4 Metrics (Inference)
    lstm_mae, lstm_rmse = lstm.evaluate(X_test, y_test)

    # ── Phase 5: Policy Optimization ─────────────────────────────────────────
    logger.info("=" * 55)
    logger.info("PHASE 4: Policy Optimization Trace (Fast Feedback) ...")

    pre_warm_df = make_pre_warm_decisions(test_raw, lstm, X_test, collections_test, config, models_dir)
    
    # Export sampling trace for visual results (Actual count, Predicted count, Pre-warm triggers)
    sample_size = min(500, len(y_test))
    actual_counts = y_test[:sample_size]
    if config["decision"]["use_inverse_scale"]:
        scaler_path = models_dir / config["models"]["scaler_filename"]
        if scaler_path.exists():
            with open(scaler_path, "rb") as f:
                scaler = pickle.load(f)
            scale = scaler.data_max_[0] - scaler.data_min_[0]
            bias = scaler.data_min_[0]
            actual_counts = y_test[:sample_size] * scale + bias

    # Decisions for the same first 500 records
    decisions_sample = pre_warm_df.head(sample_size)["pre_warmed"].tolist()
    
    # Predictions (re-calculate EMA-smoothed if necessary or just send raw + threshold)
    pred_sample = lstm.predict(X_test[:sample_size])
    if config["decision"]["use_inverse_scale"]:
        pred_sample = pred_sample * scale + bias
        
    plot_data = {
        "actual": actual_counts.tolist(),
        "lstm_pred": pred_sample.tolist(),
        "pre_warmed": decisions_sample,
        "threshold": config["decision"].get("rolling_quantile", 95)
    }
    with open(models_dir / "forecast_samples.json", "w") as f:
        json.dump(plot_data, f, indent=2)

    evaluator = Evaluator(n_jobs_total=n_collections)
    report = evaluator.build_report(
        arima_mae=arima_mae,
        arima_rmse=arima_rmse,
        lstm_mae=lstm_mae,
        lstm_rmse=lstm_rmse,
        annotated_test_df=test_raw,
        pre_warm_decisions=pre_warm_df,
    )

    # Save evaluation report
    report_path = models_dir / "evaluation_report.json"
    with open(report_path, "w") as f:
        json.dump(report.to_dict(), f, indent=2)
    logger.info("Evaluation report saved to %s", report_path)

    # ── Save metadata ─────────────────────────────────────────────────────────
    train_duration = time.time() - t0
    save_metadata(
        models_dir, config,
        arima_mae, arima_rmse,
        lstm_mae, lstm_rmse,
        n_collections, train_duration,
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info("=" * 55)
    logger.info("PROCESS COMPLETE in %.1fs", train_duration)
    logger.info("  BASELINE — MAE: %.4f | RMSE: %.4f", arima_mae, arima_rmse)
    logger.info("  LSTM     — MAE: %.4f | RMSE: %.4f", lstm_mae, lstm_rmse)
    logger.info("  Reduction: %.1f%%  |  Adaptive cost: %.2f×", 
                report.cold_start_reduction_pct, report.adaptive_cost_rel)
    logger.info("-" * 55)


if __name__ == "__main__":
    main()
