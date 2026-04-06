# Adaptive Cold-Start Prediction & Pre-Warming for AWS Lambda

End-to-end pipeline: Google Cluster trace → ARIMA + LSTM forecasting → AWS EventBridge pre-warming → real-time dashboard.

---

## Project Structure

```
cold-start-predictor/
├── configs/
│   └── config.yaml              # All tunable parameters
├── data/
│   ├── raw/
│   │   ├── task_events/         # Downloaded .csv.gz files go here
│   │   └── task_usage/          # Optional
│   └── processed/               # Auto-generated after preprocessing
├── models/                      # Saved model artifacts
├── src/
│   ├── preprocessing/
│   │   ├── __init__.py
│   │   ├── loader.py            # Chunked dataset loading
│   │   ├── timeseries.py        # Windowed aggregation
│   │   ├── cold_start_sim.py    # Cold start simulation
│   │   └── features.py          # Feature engineering
│   ├── forecasting/
│   │   ├── __init__.py
│   │   ├── arima_model.py       # ARIMA per-function baseline
│   │   ├── lstm_model.py        # Stacked LSTM model
│   │   └── evaluator.py         # MAE / RMSE / cold start metrics
│   ├── aws/
│   │   ├── __init__.py
│   │   ├── eventbridge.py       # EventBridge rule management
│   │   ├── lambda_warmer.py     # Warm-up invocation dispatcher
│   │   └── iam_validator.py     # Permission checks
│   └── api/
│       ├── __init__.py
│       ├── app.py               # FastAPI application
│       └── feedback_loop.py     # Adaptive threshold updater
├── dashboard/
│   └── frontend/                # React + Chart.js dashboard
│       ├── package.json
│       └── src/
│           ├── App.jsx
│           └── components/
├── scripts/
│   ├── download_data.py         # Automated dataset downloader
│   ├── wget_download.sh         # wget fallback
│   ├── preprocess.py            # Run full preprocessing pipeline
│   └── train.py                 # Train ARIMA + LSTM
├── tests/
│   ├── test_preprocessing.py
│   ├── test_forecasting.py
│   └── test_api.py
├── lambda_warmer/
│   └── handler.py               # Deploy this to AWS Lambda
├── requirements.txt
├── DATASET_DOWNLOAD.md
└── README.md
```

---

## Quick Start

### 1. Install Dependencies
```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Download Dataset
See **DATASET_DOWNLOAD.md** for full instructions.

Quick download (50 parts, ~1.5 GB):
```bash
python scripts/download_data.py --parts 50 --output data/raw/
```

### 3. Preprocess
```bash
python scripts/preprocess.py
```
Output: `data/processed/timeseries.parquet`, `data/processed/features.parquet`

### 4. Train Models
```bash
python scripts/train.py
```
Output: `models/arima_models.pkl`, `models/lstm_model.keras`, `models/scaler.pkl`

### 5. Run API Server
```bash
uvicorn src.api.app:app --reload --port 8000
```

### 6. Run Dashboard
```bash
cd dashboard/frontend
npm install && npm start
```

### 7. AWS Deployment (optional)
```bash
# Configure AWS credentials first
aws configure

# Deploy EventBridge rule
python -c "from src.aws.eventbridge import EventBridgeManager; EventBridgeManager().deploy()"
```

---

## Configuration

All parameters live in `configs/config.yaml`. No hardcoded values elsewhere.

---

## AWS Free Tier Compatibility

- EventBridge: 14M events/month free → 1 rule × 12/hour × 720h = 8,640 events/month ✓
- Lambda: 1M requests/month free → 8,640 warm-up calls ✓
- API Gateway: 1M calls/month free ✓

---

## References

See paper: *Adaptive Cold-Start Prediction and Pre-Warming for AWS*, Pune Institute of Computer Technology.
