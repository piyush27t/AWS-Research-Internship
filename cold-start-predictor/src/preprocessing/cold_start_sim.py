"""
src/preprocessing/cold_start_sim.py
──────────────────────────────────────────────────────────────────────────────
Simulates cold start events using an inactivity-threshold rule.

Works with Kaggle AWS Cold Start dataset.

Equation from paper:
    ColdStart_{j,t} = 1  if (t - t_last) · W > τ
                      0  otherwise

where τ is the cold start threshold (default 30 minutes, matching AWS Lambda's
typical container keep-alive timeout).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

COL_COLLECTION_ID = "collection_id"
COL_WINDOW = "window"
COL_COUNT = "invocation_count"
COL_COLD_START = "is_cold_start"
COL_LAST_ACTIVE = "last_active_window"


class ColdStartSimulator:
    """
    Assigns a binary cold-start label to each (collection, window) row.

    Parameters
    ----------
    cold_start_threshold_min : int
        Inactivity gap in minutes that triggers a cold start (τ).
    window_seconds : int
        Time window width in seconds (W).
    """

    def __init__(self, cold_start_threshold_min: int, window_seconds: int) -> None:
        self.threshold_windows = int(
            (cold_start_threshold_min * 60) / window_seconds
        )
        logger.info(
            "Cold start threshold: %d min → %d windows",
            cold_start_threshold_min,
            self.threshold_windows,
        )

    def annotate(self, timeseries_df: pd.DataFrame) -> pd.DataFrame:
        """
        Add a cold-start indicator column to the time-series DataFrame.

        Parameters
        ----------
        timeseries_df : pd.DataFrame
            Long-form DataFrame with columns: collection_id, window, invocation_count.

        Returns
        -------
        pd.DataFrame
            Same DataFrame with additional column: is_cold_start (bool).
        """
        df = timeseries_df.copy().sort_values([COL_COLLECTION_ID, COL_WINDOW])
        results = []

        for collection_id, group in df.groupby(COL_COLLECTION_ID, sort=False):
            group = group.copy().reset_index(drop=True)
            cold_starts = self._compute_cold_starts(group)
            group[COL_COLD_START] = cold_starts
            results.append(group)

        annotated = pd.concat(results, ignore_index=True)
        cold_rate = annotated[COL_COLD_START].mean()
        logger.info(
            "Cold start annotation complete. Baseline rate: %.2f%%",
            cold_rate * 100,
        )
        return annotated

    def _compute_cold_starts(self, group: pd.DataFrame) -> np.ndarray:
        """
        Vectorised cold start computation for a single collection's time series.
        A cold start occurs at window t if the previous non-zero window is
        more than threshold_windows ago (or there was no previous activity).
        """
        counts = group[COL_COUNT].to_numpy()
        windows = group[COL_WINDOW].to_numpy()
        n = len(counts)
        cold = np.zeros(n, dtype=bool)

        last_active = -self.threshold_windows - 1  # ensures first invocation is cold

        for i in range(n):
            if counts[i] > 0:
                gap = windows[i] - last_active
                if gap > self.threshold_windows:
                    cold[i] = True
                last_active = windows[i]

        return cold

    def cold_start_rate(self, annotated_df: pd.DataFrame) -> float:
        """Return the fraction of windows that are cold starts (across all collections)."""
        invoked = annotated_df[annotated_df[COL_COUNT] > 0]
        if invoked.empty:
            return 0.0
        return invoked[COL_COLD_START].mean()
