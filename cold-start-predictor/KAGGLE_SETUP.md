# Kaggle AWS Cold Start Dataset - Setup Guide

## Overview

This project has been updated to work with **Kaggle AWS Cold Start dataset** instead of the Google Cluster Dataset. All download scripts have been removed.

## Dataset Format

Your Kaggle dataset should have the following columns:

```
srno, time, instance_events, collection_id, scheduling_class,
collection_type, priority, alloc_collection_id, instance_index, machine_id, ...
```

**Column Definitions:**

- `srno`: Serial number (row identifier)
- `time`: Timestamp (Unix epoch or datetime string)
- `instance_events`: Number of instance events at this timestamp
- `collection_id`: Identifier for the collection/job
- `scheduling_class`: Scheduling priority class (0-N)
- `collection_type`: Type of collection (categorical)
- `priority`: Priority level (0-N)
- `alloc_collection_id`: Allocated collection ID
- `instance_index`: Index of the instance
- `machine_id`: Identifier of the machine

## Quick Start

### 1. Prepare Your Dataset

Place your Kaggle dataset file in the `data/raw/` directory:

```bash
data/raw/
├── your_dataset.xlsx          # or .csv
└── raw/                        # subdirectory for raw data
    └── [auto-populated by preprocessing]
```

Supported formats:

- Excel: `.xlsx`
- CSV: `.csv`

**Important:** The script will auto-detect column names (case-insensitive).

### 2. Configure Settings (Optional)

Edit `configs/config.yaml` to adjust:

```yaml
data:
  top_n_collections: 100 # Keep top N collections by frequency
  chunk_size: 500_000 # Rows per chunk during loading

timeseries:
  window_seconds: 300 # 5-minute aggregation windows
  cold_start_threshold_min: 30 # Inactivity gap (minutes) → cold start

features:
  rolling_window_k: 6 # Rolling mean/std lookback
  sequence_length: 10 # LSTM input sequence length

split:
  train_ratio: 0.70 # 70% train
  val_ratio: 0.15 # 15% validation
  test_ratio: 0.15 # 15% test
```

### 3. Run Preprocessing

```bash
# Activate your Python environment
python scripts/preprocess.py
```

This will:

1. Load your dataset from `data/raw/`
2. Build 5-minute invocation time-series per collection
3. Simulate cold start labels (30-minute inactivity threshold)
4. Extract features (rolling stats, temporal, metadata)
5. Split into train/val/test
6. Normalize with MinMaxScaler (fit on train only)
7. Save processed parquet files to `data/processed/`

**Output files:**

```
data/processed/
├── train.parquet               # 70% - normalized features
├── val.parquet                 # 15% - normalized features
├── test.parquet                # 15% - normalized features
├── test_raw.parquet            # Test with raw counts (for evaluation)
└── timeseries_annotated.parquet # Full annotated time-series (for ARIMA)
```

### 4. Train Models

```bash
python scripts/train.py
```

This will:

1. Train ARIMA baseline models (per-collection)
2. Build LSTM sequences from features
3. Train stacked LSTM model with early stopping
4. Evaluate both models on test set
5. Generate pre-warm decisions
6. Save models and evaluation report

**Output models:**

```
models/
├── arima_models.pkl            # Per-collection ARIMA models
├── lstm_model.keras            # Trained LSTM model
├── scaler.pkl                  # MinMaxScaler (for inference)
├── training_metadata.json      # Training info
└── evaluation_report.json      # Results & metrics
```

### 5. Launch API

```bash
uvicorn src.api.app:app --reload --port 8000
```

Visit http://localhost:8000/docs for interactive API documentation.

---

## Code Changes Summary

### New Files

- `src/preprocessing/kaggle_loader.py` — Loads Kaggle Excel/CSV datasets

### Updated Files

**Configuration:**

- `configs/config.yaml` — Removed Google Cluster Dataset config, added Kaggle-specific settings

**Preprocessing:**

- `src/preprocessing/timeseries.py` — Uses `collection_id` instead of `job_id`
- `src/preprocessing/cold_start_sim.py` — Updated to work per-collection
- `src/preprocessing/features.py` — New feature columns (scheduling_class, priority)
- `scripts/preprocess.py` — Imports KaggleAWSLoader, handles new feature engineering

**Training:**

- `src/forecasting/arima_model.py` — Per-collection instead of per-job
- `scripts/train.py` — Updated column names, logging

**Removed:**

- `scripts/download_data.py` — No longer needed
- `scripts/wget_download.sh` — No longer needed
- `src/preprocessing/loader.py` — Replaced by kaggle_loader.py (old file kept for reference)

---

## Feature Engineering

The system now extracts these features per (collection, time-window):

| Feature                 | Type        | Source                      |
| ----------------------- | ----------- | --------------------------- |
| `invocation_count`      | Count       | Instance events per window  |
| `rolling_mean`          | Float       | Rolling mean over 6 windows |
| `rolling_std`           | Float       | Rolling std over 6 windows  |
| `time_of_day`           | Float [0-1] | Window time mod 86400s      |
| `day_of_week`           | Int [0-6]   | Window day of week          |
| `scheduling_class_norm` | Float [0-1] | Normalized from metadata    |
| `priority_norm`         | Float [0-1] | Normalized from metadata    |

All features are normalized to [0, 1] using MinMaxScaler fitted on training data only.

---

## Performance Tuning

### Hyperparameters (config.yaml)

**ARIMA:**

```yaml
arima:
  max_p: 5
  max_d: 2
  max_q: 5
  information_criterion: "aic"
  n_jobs: -1 # All CPU cores
```

**LSTM:**

```yaml
lstm:
  layer1_units: 128
  layer2_units: 64
  dropout_rate: 0.3
  learning_rate: 0.001
  batch_size: 64
  max_epochs: 100
  early_stopping_patience: 10
  reduce_lr_patience: 5
```

### Grid Search (Optional)

To tune LSTM hyperparameters:

```bash
RUN_GRID_SEARCH=1 python scripts/train.py
```

---

## Troubleshooting

### FileNotFoundError: No .xlsx or .csv files found

- Ensure your dataset file is in `data/raw/` with `.xlsx` or `.csv` extension
- Example: `data/raw/kaggle_dataset.xlsx`

### Missing required columns

- Check that your dataset has: `time`, `collection_id`, `instance_events`, `scheduling_class`
- Column names are case-insensitive (auto-converted to lowercase)

### Memory errors on large datasets

- Reduce `data.chunk_size` in config.yaml
- Reduce `data.top_n_collections` to keep fewer collections

### LSTM training slow

- Reduce `data.top_n_collections`
- Increase `lstm.batch_size`
- Use GPU: Install `tensorflow[and-cuda]`

---

## API Endpoints

After starting the API server:

```
GET  /                          # Health check
GET  /docs                      # Interactive API docs
POST /predict                   # Forecast next-window invocation count
POST /cold_start_check          # Check if next window likely cold starts
GET  /models/status             # Check model status
```

Example prediction:

```bash
curl -X POST "http://localhost:8000/predict" \
  -H "Content-Type: application/json" \
  -d '{
    "collection_id": 123,
    "window": 100,
    "features": [1.5, 0.2, 0.1, 0.5, 2.0, 0.3, 0.4]
  }'
```

---

## Architecture

```
data/raw/
  └── kaggle_dataset.xlsx           ← Your input file

scripts/preprocess.py
  → src/preprocessing/kaggle_loader.py
  → src/preprocessing/timeseries.py
  → src/preprocessing/cold_start_sim.py
  → src/preprocessing/features.py
  → data/processed/*.parquet

scripts/train.py
  → src/forecasting/arima_model.py
  → src/forecasting/lstm_model.py
  → src/forecasting/evaluator.py
  → models/*

src/api/app.py
  ← models/lstm_model.keras
  ← models/scaler.pkl
  → 0.0.0.0:8000
```

---

## Testing

Run tests to verify the preprocessing and training pipeline:

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_preprocessing.py

# Verbose output
pytest -v tests/test_forecasting.py
```

---

## Next Steps

1. **Place your dataset** in `data/raw/` (xlsx or csv)
2. **Run preprocessing**: `python scripts/preprocess.py`
3. **Train models**: `python scripts/train.py`
4. **Start API**: `uvicorn src.api.app:app --reload`
5. **Monitor predictions** and query the API

---

## Support

For issues or questions:

1. Check logs in `logs/` directory
2. Review `configs/config.yaml` settings
3. Ensure dataset format matches specification
4. Check that data files are in expected locations
