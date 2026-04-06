#!/usr/bin/env python3
import json
import logging
import pickle
from pathlib import Path
import numpy as np
import pandas as pd
import yaml
import sys

# Project root setup
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Import logic from train.py to ensure consistency
from scripts.train import make_pre_warm_decisions, load_config, load_processed
from src.forecasting.lstm_model import LSTMForecaster
from src.forecasting.evaluator import Evaluator
from src.preprocessing.features import SequenceBuilder

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tune_policy")

def main():
    config = load_config()
    processed_dir = PROJECT_ROOT / config["data"]["processed_dir"]
    models_dir = PROJECT_ROOT / config["models"]["save_dir"]
    
    logger.info("--- ⚙️ Loading Data & Models ---")
    train, val, test, test_raw = load_processed(processed_dir)
    n_collections = test_raw["collection_id"].nunique()
    seq_len = config["features"]["sequence_length"]
    
    # Reload LSTM
    lstm = LSTMForecaster(config)
    lstm.load(models_dir / config["models"]["lstm_filename"])
    
    # Rebuild sequences for inference
    builder = SequenceBuilder(sequence_length=seq_len)
    X_test, y_test, collections_test = builder.build_sequences(test)
    
    # Fixed Metrics from training log
    arima_mae, arima_rmse = 0.0874, 0.5704
    lstm_mae, lstm_rmse = 0.0010, 0.0099
    
    evaluator = Evaluator(n_jobs_total=n_collections)
    
    # --- Sweep Strategy ---
    # Comparing Percentile with Persistence and Cooldown
    # We want to find the best tradeoff.
    percentiles = [85, 90, 95]
    persistences = [1, 2] # 1 window vs 2 window confirmation
    cooldowns = [0, 3]    # No cooldown vs 15 min cooldown
    
    logger.info("\n%-8s | %-7s | %-8s | %-12s | %-15s | %-10s" % ("Method", "Val", "Persist", "Reduc %", "Cost (Rel)", "Triggers"))
    logger.info("-" * 80)
    
    all_results = []
    
    for p in percentiles:
        for ps in persistences:
            for cd in cooldowns:
                # Temporarily override config
                config["decision"]["policy_method"] = "percentile"
                config["decision"]["policy_value"] = p
                config["decision"]["persistence_windows"] = ps
                config["decision"]["cooldown_windows"] = cd
                config["decision"]["use_inverse_scale"] = False
                
                # Inference is cached in the loop if we were careful, but here we re-predict (takes ~20s)
                pre_warm_df = make_pre_warm_decisions(test_raw, lstm, X_test, collections_test, config, models_dir)
                report = evaluator.build_report(
                    arima_mae=arima_mae, arima_rmse=arima_rmse,
                    lstm_mae=lstm_mae, lstm_rmse=lstm_rmse,
                    annotated_test_df=test_raw,
                    pre_warm_decisions=pre_warm_df
                )
                
                n_triggers = int(pre_warm_df["pre_warmed"].sum())
                row = {
                    "method": "percentile",
                    "value": p,
                    "persistence": ps,
                    "cooldown": cd,
                    "reduction": report.cold_start_reduction_pct,
                    "cost": report.adaptive_cost_rel,
                    "triggers": n_triggers
                }
                all_results.append(row)
                
                logger.info("%-8s | %-7.1f | %-8d | %-12.2f%% | %-15.2f | %-10d" % (
                    "p-tile", p, ps, report.cold_start_reduction_pct, report.adaptive_cost_rel, n_triggers
                ))
            
    logger.info("-" * 80)
    
    # Recommended settings for the paper (Best reduction with < 1.30 rel cost)
    candidates = [r for r in all_results if r["cost"] < 1.30]
    if candidates:
        best = max(candidates, key=lambda x: x["reduction"])
        logger.info(f"\n✅ OPTIMAL POLICY: Percentile={best['value']}, Persistence={best['persistence']}, Cooldown={best['cooldown']}")
        logger.info(f"   Baseline Cost: 1.00x")
        logger.info(f"   Projected Cost: {best['cost']:.2f}x")
        logger.info(f"   Cold Start Reduction: {best['reduction']:.2f}%")
    else:
        logger.warning("\nNo candidate found with cost < 1.30x. Try less aggressive percentiles.")

if __name__ == "__main__":
    main()
