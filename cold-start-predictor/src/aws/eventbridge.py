"""
src/aws/eventbridge.py
──────────────────────────────────────────────────────────────────────────────
Manages the AWS EventBridge rule that fires every 5 minutes to trigger
the pre-warming prediction cycle.

Operates under principle of least privilege — no admin roles.
All resource names and schedule expressions come from config.yaml.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import boto3
import yaml

logger = logging.getLogger(__name__)


def load_config(config_path: Path | None = None) -> dict:
    if config_path is None:
        config_path = Path(__file__).parents[2] / "configs" / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


class EventBridgeManager:
    """
    Creates and manages the EventBridge rule that invokes the prediction Lambda.

    Parameters
    ----------
    config : dict, optional
        Parsed config.yaml. Loaded from default path if not provided.
    """

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or load_config()
        aws_cfg = self.config["aws"]
        self.region: str = aws_cfg["region"]
        self.rule_name: str = aws_cfg["eventbridge_rule_name"]
        self.schedule: str = aws_cfg["schedule_expression"]
        self.lambda_name: str = aws_cfg["lambda_function_name"]

        self.events_client = boto3.client("events", region_name=self.region)
        self.lambda_client = boto3.client("lambda", region_name=self.region)

    # ── public API ────────────────────────────────────────────────────────────

    def deploy(self) -> str:
        """
        Create or update the EventBridge rule and attach the Lambda target.
        Returns the rule ARN.
        """
        rule_arn = self._put_rule()
        lambda_arn = self._get_lambda_arn()
        self._put_target(rule_arn, lambda_arn)
        self._add_lambda_permission(rule_arn)
        logger.info("EventBridge rule deployed: %s (schedule: %s)", self.rule_name, self.schedule)
        return rule_arn

    def disable(self) -> None:
        """Disable the EventBridge rule (stop scheduling)."""
        self.events_client.disable_rule(Name=self.rule_name)
        logger.info("EventBridge rule disabled: %s", self.rule_name)

    def enable(self) -> None:
        """Re-enable a previously disabled rule."""
        self.events_client.enable_rule(Name=self.rule_name)
        logger.info("EventBridge rule enabled: %s", self.rule_name)

    def delete(self) -> None:
        """Remove the rule and all targets."""
        try:
            self.events_client.remove_targets(
                Rule=self.rule_name, Ids=["warm-up-target"]
            )
        except self.events_client.exceptions.ResourceNotFoundException:
            pass
        try:
            self.events_client.delete_rule(Name=self.rule_name)
            logger.info("EventBridge rule deleted: %s", self.rule_name)
        except self.events_client.exceptions.ResourceNotFoundException:
            logger.warning("Rule not found (already deleted?): %s", self.rule_name)

    def get_status(self) -> dict:
        """Return current rule status and next scheduled invocation."""
        try:
            response = self.events_client.describe_rule(Name=self.rule_name)
            return {
                "name": response["Name"],
                "state": response["State"],
                "schedule": response.get("ScheduleExpression"),
                "arn": response.get("Arn"),
            }
        except self.events_client.exceptions.ResourceNotFoundException:
            return {"name": self.rule_name, "state": "NOT_FOUND"}

    # ── private helpers ───────────────────────────────────────────────────────

    def _put_rule(self) -> str:
        response = self.events_client.put_rule(
            Name=self.rule_name,
            ScheduleExpression=self.schedule,
            State="ENABLED",
            Description=(
                "Fires every 5 minutes to trigger the cold-start pre-warming "
                "prediction cycle."
            ),
        )
        return response["RuleArn"]

    def _get_lambda_arn(self) -> str:
        response = self.lambda_client.get_function(FunctionName=self.lambda_name)
        return response["Configuration"]["FunctionArn"]

    def _put_target(self, rule_arn: str, lambda_arn: str) -> None:
        self.events_client.put_targets(
            Rule=self.rule_name,
            Targets=[
                {
                    "Id": "warm-up-target",
                    "Arn": lambda_arn,
                    "Input": json.dumps({"source": "eventbridge", "rule": self.rule_name}),
                }
            ],
        )

    def _add_lambda_permission(self, rule_arn: str) -> None:
        """Grant EventBridge permission to invoke the Lambda."""
        try:
            self.lambda_client.add_permission(
                FunctionName=self.lambda_name,
                StatementId=f"eventbridge-{self.rule_name}",
                Action="lambda:InvokeFunction",
                Principal="events.amazonaws.com",
                SourceArn=rule_arn,
            )
        except self.lambda_client.exceptions.ResourceConflictException:
            logger.debug("Lambda permission already exists, skipping.")
