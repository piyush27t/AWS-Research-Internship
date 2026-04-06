"""
src/forecasting/evaluator.py
──────────────────────────────────────────────────────────────────────────────
Computes evaluation metrics:
  - MAE / RMSE for forecast accuracy
  - Cold start rate under three conditions:
      1. No pre-warming (baseline)
      2. Always-warm (all containers always active)
      3. Adaptive pre-warming (proposed system)
  - Over-provisioning rate
  - Simulated provisioning cost (relative to baseline)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

COL_JOB_ID = "collection_id"
COL_WINDOW = "window"
COL_COUNT = "invocation_count"
COL_COLD_START = "is_cold_start"
COL_PRE_WARM = "pre_warmed"


@dataclass
class EvaluationReport:
    """Container for all evaluation metrics."""
    # Forecast accuracy
    arima_mae: float = 0.0
    arima_rmse: float = 0.0
    lstm_mae: float = 0.0
    lstm_rmse: float = 0.0

    # Cold start reduction
    baseline_cold_start_rate: float = 0.0       # no pre-warming
    always_warm_cold_start_rate: float = 0.0    # always warm (= 0)
    adaptive_cold_start_rate: float = 0.0       # proposed system

    # Cost
    baseline_cost_rel: float = 1.0
    always_warm_cost_rel: float = 0.0
    adaptive_cost_rel: float = 0.0

    # Over-provisioning
    over_provision_rate: float = 0.0

    # Summary
    cold_start_reduction_pct: float = 0.0

    def to_dict(self) -> dict:
        return {k: round(v, 4) for k, v in self.__dict__.items()}

    def log(self) -> None:
        logger.info("=" * 50)
        logger.info("EVALUATION REPORT")
        logger.info("  ARIMA   — MAE: %.4f  RMSE: %.4f", self.arima_mae, self.arima_rmse)
        logger.info("  LSTM    — MAE: %.4f  RMSE: %.4f", self.lstm_mae, self.lstm_rmse)
        logger.info("  Cold start: baseline=%.1f%% | adaptive=%.1f%% | always-warm=0%%",
                    self.baseline_cold_start_rate * 100,
                    self.adaptive_cold_start_rate * 100)
        logger.info("  Cold start reduction: %.1f%%", self.cold_start_reduction_pct)
        logger.info("  Provisioning cost (rel): adaptive=%.2f× | always-warm=%.2f×",
                    self.adaptive_cost_rel, self.always_warm_cost_rel)
        logger.info("  Over-provision rate: %.1f%%", self.over_provision_rate * 100)
        logger.info("=" * 50)


class Evaluator:
    """
    Computes all evaluation metrics defined in Section IV of the paper.

    Parameters
    ----------
    n_jobs_total : int
        Total number of monitored functions (for always-warm cost baseline).
    """

    def __init__(self, n_jobs_total: int) -> None:
        self.n_jobs_total = n_jobs_total

    def forecast_metrics(
        self, y_true: np.ndarray, y_pred: np.ndarray
    ) -> tuple[float, float]:
        """Return (MAE, RMSE)."""
        mae = float(np.mean(np.abs(y_true - y_pred)))
        rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
        return mae, rmse

    def cold_start_metrics(
        self,
        annotated_test_df: pd.DataFrame,
        pre_warm_decisions: pd.DataFrame,
    ) -> tuple[float, float, float, float]:
        """
        Compute cold start rates and provisioning metrics.

        Parameters
        ----------
        annotated_test_df : pd.DataFrame
            Test set with is_cold_start column (ground truth without pre-warming).
        pre_warm_decisions : pd.DataFrame
            DataFrame with columns: job_id, window, pre_warmed (bool).

        Returns
        -------
        (baseline_cs_rate, adaptive_cs_rate, always_warm_cost_rel, adaptive_cost_rel)
        """
        # Baseline: cold start rate with no intervention
        invoked = annotated_test_df[annotated_test_df[COL_COUNT] > 0]
        baseline_cs_rate = float(invoked[COL_COLD_START].mean()) if not invoked.empty else 0.0

        # Merge pre-warm decisions
        merged = annotated_test_df.merge(
            pre_warm_decisions, on=[COL_JOB_ID, COL_WINDOW], how="left"
        )
        merged[COL_PRE_WARM] = merged[COL_PRE_WARM].fillna(False).astype(bool)

        # A cold start is prevented if the function was pre-warmed
        merged["effective_cold_start"] = (
            merged[COL_COLD_START] & ~merged[COL_PRE_WARM]
        )
        invoked_m = merged[merged[COL_COUNT] > 0]
        adaptive_cs_rate = (
            float(invoked_m["effective_cold_start"].mean()) if not invoked_m.empty else 0.0
        )

        # Over-provisioning: pre-warmed but not invoked in next window
        pre_warmed = merged[merged[COL_PRE_WARM]]
        not_used = pre_warmed[pre_warmed[COL_COUNT] == 0]
        over_provision_rate = (
            float(len(not_used) / len(pre_warmed)) if not pre_warmed.empty else 0.0
        )

        # Relative provisioning cost
        n_windows = annotated_test_df[COL_WINDOW].nunique()
        adaptive_warm_events = int(merged[COL_PRE_WARM].sum())
        always_warm_events = self.n_jobs_total * n_windows
        baseline_events = 0

        adaptive_cost_rel = adaptive_warm_events / max(1, always_warm_events / self.n_jobs_total)
        always_warm_cost_rel = float(always_warm_events) / max(1, adaptive_warm_events)

        return baseline_cs_rate, adaptive_cs_rate, over_provision_rate, always_warm_cost_rel

    def build_report(
        self,
        arima_mae: float,
        arima_rmse: float,
        lstm_mae: float,
        lstm_rmse: float,
        annotated_test_df: pd.DataFrame,
        pre_warm_decisions: pd.DataFrame,
    ) -> EvaluationReport:
        baseline_cs, adaptive_cs, over_prov, always_warm_cost = self.cold_start_metrics(
            annotated_test_df, pre_warm_decisions
        )

        reduction = (
            (baseline_cs - adaptive_cs) / baseline_cs * 100 if baseline_cs > 0 else 0.0
        )

        # Approximate adaptive cost relative to baseline (1.0)
        total_windows = annotated_test_df[COL_WINDOW].nunique()
        warmed = pre_warm_decisions[COL_PRE_WARM].sum() if COL_PRE_WARM in pre_warm_decisions else 0
        adaptive_cost_rel = float(warmed) / max(1, total_windows) + 1.0

        report = EvaluationReport(
            arima_mae=arima_mae,
            arima_rmse=arima_rmse,
            lstm_mae=lstm_mae,
            lstm_rmse=lstm_rmse,
            baseline_cold_start_rate=baseline_cs,
            always_warm_cold_start_rate=0.0,
            adaptive_cold_start_rate=adaptive_cs,
            baseline_cost_rel=1.0,
            always_warm_cost_rel=always_warm_cost,
            adaptive_cost_rel=adaptive_cost_rel,
            over_provision_rate=over_prov,
            cold_start_reduction_pct=reduction,
        )
        report.log()
        return report
