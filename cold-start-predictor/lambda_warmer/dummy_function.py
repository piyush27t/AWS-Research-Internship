"""
lambda_warmer/dummy_function.py
──────────────────────────────────────────────────────────────────────────────
Deploy this as a SEPARATE Lambda function named: my-dummy-function
  Runtime : Python 3.11
  Memory  : 128 MB
  Timeout : 10 s

This simulates a real application Lambda (an API endpoint, worker, etc.)
that would normally suffer cold starts. The cold-start-warmer invokes this
function periodically to keep its container alive.

In the real world, this would be your actual application code.
For the research demo, it just logs and returns — proving the container
was already warm when invoked.

Deploy steps:
  1. In AWS Console → Lambda → Create function
  2. Name: my-dummy-function
  3. Runtime: Python 3.11
  4. Copy-paste this entire file as the function code
  5. Handler: dummy_function.handler
  6. Save
"""

from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Track if this is a cold start (container init) vs warm invocation
_CONTAINER_START_TIME = time.time()
_INVOCATION_COUNT = 0


def handler(event: dict, context) -> dict:
    """
    Simulated application Lambda function.

    Distinguishes between:
      - keep_warm pings (from cold-start-warmer) → fast response
      - real requests (from API Gateway etc.) → normal processing
    """
    global _INVOCATION_COUNT
    _INVOCATION_COUNT += 1

    action = event.get("action", "request")
    source = event.get("source", "unknown")

    # Calculate container age (how long since cold start)
    container_age_s = round(time.time() - _CONTAINER_START_TIME, 1)
    is_warm = container_age_s > 2  # If container is >2s old, it was pre-warmed

    if action == "keep_warm":
        # This is a pre-warming ping from the cold-start-warmer
        logger.info(
            "KEEP_WARM ping received | invocation=%d | container_age=%.1fs | warm=%s",
            _INVOCATION_COUNT, container_age_s, is_warm
        )
        return {
            "statusCode": 200,
            "body": json.dumps({
                "status": "warm",
                "action": "keep_warm",
                "invocation_count": _INVOCATION_COUNT,
                "container_age_seconds": container_age_s,
            }),
        }

    # Simulate a real application request
    logger.info(
        "REAL REQUEST received | invocation=%d | container_age=%.1fs | was_warm=%s",
        _INVOCATION_COUNT, container_age_s, is_warm
    )

    # Simulate some work (e.g. DB lookup, computation)
    time.sleep(0.05)

    response_body = {
        "message": "Hello from my-dummy-function!",
        "was_pre_warmed": is_warm,
        "container_age_seconds": container_age_s,
        "invocation_count": _INVOCATION_COUNT,
        "cold_start_avoided": is_warm,
    }

    if not is_warm:
        logger.warning("COLD START DETECTED on invocation %d", _INVOCATION_COUNT)
        response_body["warning"] = "Cold start occurred — container was not pre-warmed"

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(response_body),
    }
