"""
src/aws/lambda_warmer.py
──────────────────────────────────────────────────────────────────────────────
Dispatches async warm-up invocations to the cold-start-warmer Lambda function.

Uses InvocationType="Event" (fire-and-forget) so the warm-up call does not
block the prediction cycle. All resource names from config.yaml.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Sequence

import boto3
import yaml

logger = logging.getLogger(__name__)


def load_config(config_path: Path | None = None) -> dict:
    if config_path is None:
        config_path = Path(__file__).parents[2] / "configs" / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


class LambdaWarmer:
    """
    Dispatches warm-up invocations to the designated AWS Lambda function.

    Parameters
    ----------
    config : dict, optional
        Parsed config.yaml.
    """

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or load_config()
        aws_cfg = self.config["aws"]
        self.function_name: str = aws_cfg["lambda_function_name"]
        self.invocation_type: str = aws_cfg["warm_up_invocation_type"]
        self.region: str = aws_cfg["region"]
        self.client = boto3.client("lambda", region_name=self.region)

    def warm_up(self, job_ids: Sequence[str]) -> dict:
        """
        Fire asynchronous warm-up invocations for the given job IDs.

        Parameters
        ----------
        job_ids : sequence of str
            Function/job identifiers that need pre-warming.

        Returns
        -------
        dict with keys: dispatched (int), failed (int), job_ids (list)
        """
        dispatched, failed = 0, []

        for job_id in job_ids:
            success = self._invoke_one(job_id)
            if success:
                dispatched += 1
            else:
                failed.append(job_id)

        logger.info(
            "Warm-up cycle: %d dispatched, %d failed. Jobs: %s",
            dispatched,
            len(failed),
            list(job_ids)[:5],  # log first 5 for brevity
        )
        return {
            "dispatched": dispatched,
            "failed": len(failed),
            "failed_jobs": failed,
        }

    def _invoke_one(self, job_id: str) -> bool:
        """Send a single async invocation. Returns True on success."""
        payload = json.dumps({"job_id": job_id, "action": "keep_warm"})
        try:
            response = self.client.invoke(
                FunctionName=self.function_name,
                InvocationType=self.invocation_type,  # "Event" = async
                Payload=payload.encode(),
            )
            status = response.get("StatusCode", 0)
            # 202 = accepted for async invocation
            if status == 202:
                return True
            logger.warning("Unexpected status %d for job %s", status, job_id)
            return False
        except Exception as exc:
            logger.error("Warm-up invocation failed for job %s: %s", job_id, exc)
            return False
