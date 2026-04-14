# Cold-Start Prediction System — Complete Setup Guide

**Project**: Adaptive Cold-Start Prediction & Pre-Warming for AWS Lambda  
**Institution**: Pune Institute of Computer Technology  
**Stack**: Python 3.10 · FastAPI · TensorFlow/LSTM · AWS Lambda · EventBridge

---

## System Architecture

```
EventBridge Rule (every 5 min)
        │
        ▼
cold-start-warmer (Lambda)
        │
        ├──► POST /predict  →  FastAPI Backend (LSTM model)
        │                            │
        │         ┌──────────────────┘
        │         │  pre_warm_decisions: {fn: true/false}
        │         ▼
        ├──► Invoke my-dummy-function  (async, keep-warm ping)
        │
        └──► POST /feedback  →  FastAPI Backend
                                      │
                                      └──► logs/metrics_history.jsonl
                                                    │
                                                    ▼
                                            React Dashboard
                                         (localhost:3000/charts)
```

---

## Prerequisites

- Python 3.10 with a `.venv` virtual environment (already set up)
- Node.js >= 16 installed
- AWS account (Free Tier is sufficient)
- AWS CLI installed: https://aws.amazon.com/cli/
- ngrok installed (for exposing local backend to AWS)

---

## STEP 0 — Start the FastAPI Backend

Open a terminal in the project root (`cold-start-predictor/`) and run:

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.api.app:app --reload --port 8000
```

Verify it's healthy:
```
GET http://localhost:8000/health
```

Expected response:
```json
{"status": "ok", "model_loaded": true, "scaler_loaded": true}
```

> ⚠️ Always use `.venv\Scripts\python.exe` — never the bare `uvicorn` command,
> which picks up the wrong system Python version.

---

## STEP 1 — Create the Dummy Lambda Function (`my-dummy-function`)

This simulates a real application Lambda that is at risk of cold starts.

### 1a. Create the function

1. Open **AWS Console → Lambda → Create function**
2. Select **"Author from scratch"**
3. Fill in:
   - **Function name**: `my-dummy-function`
   - **Runtime**: Python 3.11
   - **Architecture**: x86_64
4. Click **Create function**

### 1b. Add the code

1. In the **Code** tab, delete all existing code
2. Paste the entire contents of `lambda_warmer/dummy_function.py`
3. Click **Deploy**

### 1c. Update the handler

1. Go to **Configuration → General configuration → Edit**
2. Change **Handler** to: `dummy_function.handler`
3. Click **Save**

### 1d. Test it

1. Click **Test** → **Create new test event** → use template: `hello-world`
2. Change the event JSON to: `{}`
3. Click **Test**
4. Expected output (first run = cold start):
   ```json
   {"was_pre_warmed": false, "cold_start_avoided": false, "invocation_count": 1}
   ```
5. Run **Test** again immediately:
   ```json
   {"was_pre_warmed": true, "cold_start_avoided": true, "invocation_count": 2}
   ```

✅ **Dummy function is working.**

---

## STEP 2 — Create the Warmer Orchestrator Lambda (`cold-start-warmer`)

This is the orchestrator that calls your backend every 5 minutes and warms target functions.

### 2a. Create the function

1. **AWS Console → Lambda → Create function**
2. Fill in:
   - **Function name**: `cold-start-warmer`
   - **Runtime**: Python 3.11
3. Click **Create function**

### 2b. Add the code

1. In the **Code** tab, delete all existing code
2. Paste the entire contents of `lambda_warmer/handler.py`
3. Change **Handler** to: `handler.handler`
4. Click **Deploy**

### 2c. Set the timeout

1. **Configuration → General configuration → Edit**
2. **Timeout**: 0 min **30** sec
3. Click **Save**

### 2d. Add IAM Permission (so the warmer can invoke my-dummy-function)

1. **Configuration → Permissions** → click the role name (opens IAM)
2. Click **Add permissions → Attach policies**
3. Search for and attach: **`AWSLambdaRole`**
4. Click **Add permissions**

### 2e. Set Environment Variables

1. **Configuration → Environment variables → Edit → Add environment variable**

| Key | Value |
|-----|-------|
| `API_URL` | `https://XXXX.ngrok.io` *(your ngrok URL — see Step 3)* |
| `WATCHED_FUNCTIONS` | `my-dummy-function` |
| `SEQ_LEN` | `10` |

2. Click **Save**

---

## STEP 3 — Expose the Local Backend via ngrok

AWS Lambda cannot reach `localhost`. Use ngrok to create a public tunnel.

### 3a. Install ngrok (one-time)

```powershell
winget install ngrok
```

Or download from: https://ngrok.com/download

### 3b. Create a free account & authenticate (one-time)

1. Sign up at https://ngrok.com
2. Copy your authtoken from the dashboard
3. Run:
   ```powershell
   ngrok config add-authtoken YOUR_TOKEN_HERE
   ```

### 3c. Start the tunnel

In a **new terminal** (keep backend running in another):

```powershell
ngrok http 8000
```

You will see output like:
```
Forwarding  https://a1b2-103-24-xx.ngrok.io → http://localhost:8000
```

Copy the `https://...ngrok.io` URL.

### 3d. Update Lambda Environment Variable

Go back to `cold-start-warmer` → **Configuration → Environment variables → Edit**  
Update `API_URL` to your ngrok URL (e.g., `https://a1b2-103-24-xx.ngrok.io`)

> ⚠️ ngrok URLs change every time you restart it (free tier). Update the env var each session.
> For a permanent URL, deploy the backend to Render/Railway instead.

---

## STEP 4 — Configure AWS CLI

```powershell
aws configure
```

Enter when prompted:
- **AWS Access Key ID**: (from IAM → Users → Security credentials)
- **AWS Secret Access Key**: (same page)
- **Default region**: `us-east-1`
- **Default output format**: `json`

### Create IAM credentials (if you don't have them)

1. AWS Console → IAM → Users → your user → **Security credentials**
2. **Create access key** → select "CLI" use case
3. Download or copy both keys

Your IAM user needs these permissions:
- `AmazonEventBridgeFullAccess`
- `AWSLambdaRole`

---

## STEP 5 — Deploy the EventBridge Rule

This creates the rule that fires `cold-start-warmer` every 5 minutes automatically.

Run from the project root (`cold-start-predictor/`):

```powershell
.\.venv\Scripts\python.exe -c "
from src.aws.eventbridge import EventBridgeManager
mgr = EventBridgeManager()
arn = mgr.deploy()
print('Rule deployed! ARN:', arn)
"
```

### Verify the rule was created

```powershell
.\.venv\Scripts\python.exe -c "
from src.aws.eventbridge import EventBridgeManager
import json
status = EventBridgeManager().get_status()
print(json.dumps(status, indent=2))
"
```

Expected output:
```json
{
  "name": "cold-start-prewarm-rule",
  "state": "ENABLED",
  "schedule": "rate(5 minutes)",
  "arn": "arn:aws:events:us-east-1:XXXX:rule/cold-start-prewarm-rule"
}
```

✅ **EventBridge is now firing the warmer every 5 minutes.**

---

## STEP 6 — Manual Test (Don't wait 5 minutes)

Test the full cycle immediately:

1. AWS Console → Lambda → `cold-start-warmer`
2. Click **Test** → Create new event:
   ```json
   {"source": "manual-test"}
   ```
3. Click **Test**

Expected output:
```json
{
  "statusCode": 200,
  "body": {
    "warmed": ["my-dummy-function"],
    "skipped": [],
    "threshold": 0.098,
    "elapsed_ms": 1243.5
  }
}
```

Check your **FastAPI backend terminal** — you should see:
```
INFO | LSTM model loaded.
INFO | Predictions: {'my-dummy-function': 1.23}
INFO | Pre-warm decisions (threshold=0.100): {'my-dummy-function': True}
INFO | Feedback accepted. New threshold: 0.0975
```

---

## STEP 7 — View the Dashboard

### Start the frontend

```powershell
cd dashboard\frontend
npm install     # first time only
npm start
```

Open: **http://localhost:3000**

The dashboard shows 4 live charts that update every 30 seconds:
1. **Cold Start Rate** — fraction of invocations that were cold starts
2. **Adaptive Threshold (λ)** — how the decision boundary evolves over time
3. **Prediction MAE** — LSTM forecast accuracy (lower is better)
4. **Over-Provisioning Rate** — fraction of wasted pre-warm invocations

> Charts are empty until at least 1 feedback cycle has run. After your manual test in Step 6, refresh the dashboard.

---

## Complete Flow Summary

| Every 5 minutes | what happens |
|----------------|--------------|
| EventBridge fires | → `cold-start-warmer` Lambda starts |
| Warmer generates traffic data | → sends to `POST /predict` |
| FastAPI LSTM predicts | → decides which functions to warm |
| Warmer invokes `my-dummy-function` | → container stays alive |
| Warmer sends results to `POST /feedback` | → threshold adapts |
| Dashboard reads `GET /dashboard-data` | → charts update |

---

## Stopping / Cleanup

**Disable the EventBridge rule** (stop automatic firing):
```powershell
.\.venv\Scripts\python.exe -c "from src.aws.eventbridge import EventBridgeManager; EventBridgeManager().disable()"
```

**Re-enable:**
```powershell
.\.venv\Scripts\python.exe -c "from src.aws.eventbridge import EventBridgeManager; EventBridgeManager().enable()"
```

**Delete everything:**
```powershell
.\.venv\Scripts\python.exe -c "from src.aws.eventbridge import EventBridgeManager; EventBridgeManager().delete()"
```

Then delete `my-dummy-function` and `cold-start-warmer` manually from AWS Console.

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `Backend unreachable` in Lambda logs | ngrok tunnel not running, or URL not updated in env var |
| `ResourceNotFoundException` invoking dummy function | Function name mismatch — check `WATCHED_FUNCTIONS` env var |
| Dashboard shows empty charts | No feedback cycles run yet — do manual test in Step 6 |
| `KeyError: initial_threshold` | Add `initial_threshold: 0.1` to `configs/config.yaml` under `decision:` |
| `ValueError: File not found: lstm_model.keras` | Run uvicorn with `.venv\Scripts\python.exe`, not system Python |
| EventBridge deploy fails | Check IAM permissions: `AmazonEventBridgeFullAccess` + `AWSLambdaRole` |

---

*Generated for the Adaptive Cold-Start Prediction & Pre-Warming research project.*
