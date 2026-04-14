"""
lambda_warmer/handler.py
──────────────────────────────────────────────────────────────────────────────
AWS Lambda function — the "orchestrator" warmer.

Deploy this as a Lambda function named: cold-start-warmer
  Runtime : Python 3.11
  Memory  : 128 MB
  Timeout : 30 s   (needs time to call backend + invoke target functions)

How it works (runs every 5 minutes via EventBridge):
  1. Reads the list of "watched" Lambda functions from env var WATCHED_FUNCTIONS.
  2. Fetches recent invocation counts for each from CloudWatch (or uses mock data).
  3. Calls your FastAPI /predict endpoint → gets a pre_warm_decisions dict.
  4. For each function the model says to pre-warm → invokes it async (fire-and-forget).
  5. Calls /feedback so the adaptive threshold updates and the dashboard gets data.

Required env vars (set in Lambda console → Configuration → Environment variables):
  API_URL           = http://<your-backend-ip>:8000   (or Render URL)
  WATCHED_FUNCTIONS = my-api-function,my-worker-function  (comma-separated names)
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── config from environment ────────────────────────────────────────────────────
API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
WATCHED_FUNCTIONS_RAW = os.environ.get("WATCHED_FUNCTIONS", "my-dummy-function")
WATCHED_FUNCTIONS: list[str] = [f.strip() for f in WATCHED_FUNCTIONS_RAW.split(",") if f.strip()]

# How many recent counts to send per function (must match LSTM seq_len in config)
SEQ_LEN = int(os.environ.get("SEQ_LEN", "10"))


# ── main handler ──────────────────────────────────────────────────────────────

def handler(event: dict, context: Any) -> dict:
    """
    Orchestrates one pre-warming cycle:
      predict → warm → feedback
    """
    cycle_start = time.perf_counter()
    logger.info("Pre-warming cycle started. Watching: %s", WATCHED_FUNCTIONS)

    # 1. Get recent invocation counts for each watched function
    windows = _build_prediction_windows()

    # 2. Call /predict on the FastAPI backend
    predict_result = _call_predict(windows)
    if predict_result is None:
        logger.error("Could not reach FastAPI backend at %s. Aborting cycle.", API_URL)
        return {"statusCode": 503, "body": "Backend unreachable"}

    decisions: dict[str, bool] = predict_result.get("pre_warm_decisions", {})
    predictions: dict[str, float] = predict_result.get("predictions", {})
    threshold: float = predict_result.get("threshold", 0.1)

    logger.info("Predictions: %s", predictions)
    logger.info("Pre-warm decisions (threshold=%.3f): %s", threshold, decisions)

    # 3. Invoke each function that needs pre-warming
    warmed_jobs: list[str] = []
    cold_starts_simulated = 0  # real cold starts can be read from CloudWatch later

    lambda_client = boto3.client("lambda")
    for fn_name, should_warm in decisions.items():
        if should_warm:
            success = _invoke_function(lambda_client, fn_name)
            if success:
                warmed_jobs.append(fn_name)
                logger.info("Pre-warmed: %s", fn_name)
        else:
            logger.info("Skipped (below threshold): %s", fn_name)

    # 4. Send feedback to backend so the threshold adapts and dashboard updates
    # For demo purposes: simulate actual_invocations ≈ predictions (realistic assumption)
    actual_invocations = {fn: max(0.0, predictions.get(fn, 0.0)) for fn in WATCHED_FUNCTIONS}
    _call_feedback(
        predicted=predictions,
        actual=actual_invocations,
        warmed_jobs=warmed_jobs,
        cold_starts=cold_starts_simulated,
        total_invocations=max(1, sum(int(v) for v in actual_invocations.values())),
    )

    elapsed_ms = round((time.perf_counter() - cycle_start) * 1000, 2)
    logger.info("Cycle complete in %.2f ms. Warmed %d function(s).", elapsed_ms, len(warmed_jobs))

    return {
        "statusCode": 200,
        "body": json.dumps({
            "warmed": warmed_jobs,
            "skipped": [f for f in decisions if f not in warmed_jobs],
            "threshold": threshold,
            "elapsed_ms": elapsed_ms,
        }),
    }


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_prediction_windows() -> list[dict]:
    """
    Build the request payload for /predict.

    In production: fetch real CloudWatch invocation counts.
    For demo/testing: use random-ish synthetic counts that simulate bursty traffic.
    """
    import random
    windows = []
    for fn_name in WATCHED_FUNCTIONS:
        # Simulate recent traffic: mostly low, occasional bursts
        counts = [
            round(random.choices([0, 0, 0, 1, 2, 3, 5], weights=[3,3,2,3,2,2,1])[0]
                  + random.gauss(0, 0.3), 2)
            for _ in range(SEQ_LEN)
        ]
        counts = [max(0.0, c) for c in counts]
        windows.append({"job_id": fn_name, "recent_counts": counts})
    return windows


def _call_predict(windows: list[dict]) -> dict | None:
    """POST /predict and return the parsed JSON response."""
    payload = json.dumps({"windows": windows}).encode()
    req = urllib.request.Request(
        f"{API_URL}/predict",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as exc:
        logger.error("predict call failed: %s", exc)
        return None


def _call_feedback(
    predicted: dict[str, float],
    actual: dict[str, float],
    warmed_jobs: list[str],
    cold_starts: int,
    total_invocations: int,
) -> None:
    """POST /feedback to update the adaptive threshold and log metrics."""
    payload = json.dumps({
        "predicted_invocations": predicted,
        "actual_invocations": actual,
        "pre_warmed_jobs": warmed_jobs,
        "cold_starts_observed": cold_starts,
        "total_invocations": total_invocations,
    }).encode()
    req = urllib.request.Request(
        f"{API_URL}/feedback",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            logger.info("Feedback accepted. New threshold: %.4f", result.get("new_threshold", -1))
    except urllib.error.URLError as exc:
        logger.warning("feedback call failed (non-critical): %s", exc)


def _invoke_function(lambda_client: Any, fn_name: str) -> bool:
    """
    Asynchronously invoke a Lambda function to keep its container warm.
    Returns True on success, False on error.
    """
    try:
        lambda_client.invoke(
            FunctionName=fn_name,
            InvocationType="Event",  # async, fire-and-forget
            Payload=json.dumps({"action": "keep_warm", "source": "cold-start-warmer"}),
        )
        return True
    except lambda_client.exceptions.ResourceNotFoundException:
        logger.warning("Function not found, skipping: %s", fn_name)
        return False
    except Exception as exc:
        logger.error("Failed to invoke %s: %s", fn_name, exc)
        return False
