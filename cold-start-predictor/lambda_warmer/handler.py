"""
lambda_warmer/handler.py
──────────────────────────────────────────────────────────────────────────────
AWS Lambda function handler for the warm-up target.

Deploy this file as a Lambda function (Python 3.11 runtime, 128 MB memory,
10s timeout). It is invoked asynchronously by EventBridge every 5 minutes
for each job that the prediction engine decides to pre-warm.

The function performs a minimal computation to keep the container alive
and returns immediately — its only purpose is to prevent container
deallocation by the Lambda platform.

NO hardcoded values. The function logs job_id for observability.
"""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict, context) -> dict:
    """
    Entry point invoked by EventBridge or LambdaWarmer.warm_up().

    Parameters
    ----------
    event : dict
        Expected fields:
          job_id  : str   — identifier of the function being kept warm
          action  : str   — "keep_warm" | "ping" (both treated identically)
    context : LambdaContext
        Standard Lambda context object.

    Returns
    -------
    dict with statusCode 200 and a body confirming the keep-alive action.
    """
    start = time.perf_counter()

    job_id = event.get("job_id", "unknown")
    action = event.get("action", "keep_warm")

    logger.info("Warm-up invocation | job_id=%s | action=%s", job_id, action)

    # Minimal work to keep container alive without consuming meaningful CPU
    _minimal_work()

    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info("Warm-up complete | job_id=%s | elapsed_ms=%.2f", job_id, elapsed_ms)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "job_id": job_id,
            "action": action,
            "elapsed_ms": round(elapsed_ms, 2),
            "status": "warm",
        }),
    }


def _minimal_work() -> None:
    """Trivial computation — just enough to confirm the container is alive."""
    _ = sum(range(100))
