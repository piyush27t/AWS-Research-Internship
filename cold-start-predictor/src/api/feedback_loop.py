"""
src/api/feedback_loop.py
──────────────────────────────────────────────────────────────────────────────
Adaptive decision threshold updater.

Equation from paper (Section III-J):
    λ_{t+1} = λ_t + α · (OverProvRate_t − TargetRate)

The feedback loop:
  1. After each scheduling cycle, collects prediction error,
     cold start occurrences, and over-provisioning rate.
  2. Updates the threshold λ to steer OverProvRate toward TargetRate.
  3. Persists metrics to a JSONL log for dashboard consumption.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CycleMetrics:
    """Metrics captured for a single scheduling cycle."""
    cycle_id: int
    timestamp: float
    threshold: float
    predicted_invocations: dict[str, float]   # job_id → predicted count
    actual_invocations: dict[str, float]       # job_id → actual count
    pre_warmed_jobs: list[str]
    cold_starts_observed: int
    total_invocations: int

    # Derived (computed in FeedbackLoop)
    prediction_mae: float = 0.0
    cold_start_rate: float = 0.0
    over_provision_rate: float = 0.0


@dataclass
class ThresholdState:
    """Mutable threshold state persisted across cycles."""
    current: float
    alpha: float
    target_rate: float
    min_threshold: float
    max_threshold: float
    history: list[float] = field(default_factory=list)

    def update(self, over_prov_rate: float) -> float:
        """Apply threshold update rule and return new threshold."""
        delta = self.alpha * (over_prov_rate - self.target_rate)
        new_threshold = self.current + delta
        new_threshold = max(self.min_threshold, min(self.max_threshold, new_threshold))
        self.history.append(new_threshold)
        self.current = new_threshold
        return new_threshold


class FeedbackLoop:
    """
    Adaptive feedback controller for the pre-warming decision threshold.

    Parameters
    ----------
    config : dict
        Parsed config.yaml (decision and monitoring sections).
    """

    def __init__(self, config: dict) -> None:
        dc = config["decision"]
        self.threshold_state = ThresholdState(
            current=dc["initial_threshold"],
            alpha=dc["alpha"],
            target_rate=dc["target_over_provision_rate"],
            min_threshold=dc["min_threshold"],
            max_threshold=dc["max_threshold"],
        )
        metrics_path = config["monitoring"]["metrics_file"]
        self.metrics_file = Path(metrics_path)
        self.metrics_file.parent.mkdir(parents=True, exist_ok=True)
        self._cycle_counter: int = self._load_last_cycle_id()
        logger.info(
            "FeedbackLoop initialized. λ₀=%.3f, α=%.3f, target_rate=%.2f",
            self.threshold_state.current,
            self.threshold_state.alpha,
            self.threshold_state.target_rate,
        )

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def current_threshold(self) -> float:
        return self.threshold_state.current

    def process_cycle(
        self,
        predicted: dict[str, float],
        actual: dict[str, float],
        pre_warmed_jobs: list[str],
        cold_starts_observed: int,
        total_invocations: int,
    ) -> CycleMetrics:
        """
        Record a cycle's outcomes and update the adaptive threshold.

        Parameters
        ----------
        predicted : dict  job_id → predicted count
        actual    : dict  job_id → observed count
        pre_warmed_jobs : list of job_ids that were warmed this cycle
        cold_starts_observed : number of cold starts detected
        total_invocations : total invocations in this cycle

        Returns
        -------
        CycleMetrics with derived fields filled in.
        """
        self._cycle_counter += 1

        # Prediction error
        common = set(predicted) & set(actual)
        if common:
            errors = [abs(predicted[j] - actual[j]) for j in common]
            mae = sum(errors) / len(errors)
        else:
            mae = 0.0

        # Cold start rate
        cs_rate = cold_starts_observed / max(1, total_invocations)

        # Over-provisioning: warmed but not actually invoked
        over_provisioned = [j for j in pre_warmed_jobs if actual.get(j, 0) == 0]
        over_prov_rate = len(over_provisioned) / max(1, len(pre_warmed_jobs))

        # Update threshold
        new_threshold = self.threshold_state.update(over_prov_rate)

        metrics = CycleMetrics(
            cycle_id=self._cycle_counter,
            timestamp=time.time(),
            threshold=new_threshold,
            predicted_invocations=predicted,
            actual_invocations=actual,
            pre_warmed_jobs=pre_warmed_jobs,
            cold_starts_observed=cold_starts_observed,
            total_invocations=total_invocations,
            prediction_mae=mae,
            cold_start_rate=cs_rate,
            over_provision_rate=over_prov_rate,
        )

        self._persist(metrics)

        logger.debug(
            "Cycle %d | λ=%.3f | MAE=%.3f | CS_rate=%.2f%% | OverProv=%.2f%%",
            self._cycle_counter,
            new_threshold,
            mae,
            cs_rate * 100,
            over_prov_rate * 100,
        )
        return metrics

    def get_threshold_history(self) -> list[float]:
        return list(self.threshold_state.history)

    def load_recent_metrics(self, n: int = 50) -> list[dict]:
        """Return the last n cycle metric records."""
        if not self.metrics_file.exists():
            return []
        lines = self.metrics_file.read_text().strip().splitlines()
        records = []
        for line in lines[-n:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return records

    # ── private ───────────────────────────────────────────────────────────────

    def _persist(self, metrics: CycleMetrics) -> None:
        """Append metrics as a single JSON line."""
        record = {
            "cycle_id": metrics.cycle_id,
            "timestamp": metrics.timestamp,
            "threshold": metrics.threshold,
            "n_pre_warmed": len(metrics.pre_warmed_jobs),
            "cold_starts": metrics.cold_starts_observed,
            "total_invocations": metrics.total_invocations,
            "prediction_mae": metrics.prediction_mae,
            "cold_start_rate": metrics.cold_start_rate,
            "over_provision_rate": metrics.over_provision_rate,
        }
        with open(self.metrics_file, "a") as f:
            f.write(json.dumps(record) + "\n")

    def _load_last_cycle_id(self) -> int:
        records = self.load_recent_metrics(1)
        if records:
            return records[-1].get("cycle_id", 0)
        return 0
