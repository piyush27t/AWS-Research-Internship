"""
src/preprocessing/timeseries.py
──────────────────────────────────────────────────────────────────────────────
Converts raw event DataFrames into fixed-width time-window invocation counts.

Works with Kaggle AWS Cold Start dataset (Google Borg traces).

Equation from paper:
    C_{j,t} = |{ e ∈ E_j | ⌊timestamp(e) / W⌋ = t }|

All window sizes and parameters come from config.yaml.

Fix applied:
  - Added explicit guard to drop time<=0 rows before windowing so that
    sentinel zero-timestamps don't pollute window-0 with a false spike.
  - Added min_series_length filter so ARIMA is never fed degenerate
    all-zero series from sparse/inactive collections.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

COL_COLLECTION_ID = "collection_id"
COL_TIME = "time"
COL_WINDOW = "window"
COL_COUNT = "invocation_count"


class TimeSeriesBuilder:
    """
    Aggregates events into fixed-width time windows per collection.

    Parameters
    ----------
    window_seconds : int
        Width of each time bucket (W in the paper). From config timeseries.window_seconds.
    min_series_length : int
        Minimum number of non-zero windows a collection must have to be kept.
        Collections below this threshold are almost entirely zero-padded and
        carry no signal for ARIMA or LSTM. Default 5.
    """

    def __init__(self, window_seconds: int, min_series_length: int = 5) -> None:
        self.window_seconds = window_seconds
        self.min_series_length = min_series_length
        # Timestamps are in microseconds
        self._window_us = window_seconds * 1_000_000

    def build(self, events_df: pd.DataFrame) -> pd.DataFrame:
        """
        Build a (collection_id × window) invocation count matrix.

        Parameters
        ----------
        events_df : pd.DataFrame
            Output of KaggleAWSLoader.load_dataset() — must contain columns
            `time` (microseconds, >0) and `collection_id`.

        Returns
        -------
        pd.DataFrame
            Long-form DataFrame with columns: collection_id, window, invocation_count.
            The `window` column is an integer index (0 = first interval after the
            global minimum non-zero timestamp).
        """
        df = events_df.copy()

        # Guard: drop any remaining zero or negative timestamps
        bad_time = df[COL_TIME] <= 0
        if bad_time.any():
            logger.warning(
                "Dropping %d rows with time<=0 before windowing.", bad_time.sum()
            )
            df = df[~bad_time].copy()

        if df.empty:
            raise ValueError(
                "No rows with valid timestamps (time > 0). "
                "Check event-type filtering in kaggle_loader.py."
            )

        # Compute window index relative to the global minimum timestamp so
        # window 0 corresponds to the first observed event, not the Unix epoch.
        t_min = df[COL_TIME].min()
        df[COL_WINDOW] = ((df[COL_TIME] - t_min) // self._window_us).astype(int)

        logger.info(
            "Window range: %d – %d (span: %d windows, W=%ds)",
            df[COL_WINDOW].min(),
            df[COL_WINDOW].max(),
            df[COL_WINDOW].max() - df[COL_WINDOW].min() + 1,
            self.window_seconds,
        )

        # Count events per (collection, window)
        counts = (
            df.groupby([COL_COLLECTION_ID, COL_WINDOW])
            .size()
            .rename(COL_COUNT)
            .reset_index()
        )

        # Fill missing windows with zero so every collection has a continuous sequence
        counts = self._fill_zero_windows(counts)

        # Filter out collections that are almost entirely zero (sparse/inactive)
        counts = self._filter_sparse_collections(counts)

        logger.info(
            "Built time-series: %d collections × %d windows (W=%ds)",
            counts[COL_COLLECTION_ID].nunique(),
            counts[COL_WINDOW].nunique(),
            self.window_seconds,
        )
        return counts

    def _fill_zero_windows(self, counts: pd.DataFrame) -> pd.DataFrame:
        """
        Ensure every collection has a row for every window in the global range,
        filling absent windows with invocation_count = 0.
        """
        w_min = counts[COL_WINDOW].min()
        w_max = counts[COL_WINDOW].max()
        all_windows = pd.RangeIndex(w_min, w_max + 1, name=COL_WINDOW)

        filled_parts = []
        for collection_id, group in counts.groupby(COL_COLLECTION_ID):
            group = group.set_index(COL_WINDOW).reindex(all_windows, fill_value=0)
            group[COL_COLLECTION_ID] = collection_id
            group.index.name = COL_WINDOW
            filled_parts.append(group.reset_index())

        return pd.concat(filled_parts, ignore_index=True)

    def _filter_sparse_collections(self, counts: pd.DataFrame) -> pd.DataFrame:
        """
        Remove collections with fewer than `min_series_length` non-zero windows.
        These are nearly-constant all-zero series that cause ARIMA to issue
        'completely constant data' warnings and produce flatline predictions.
        """
        nonzero_per_collection = (
            counts[counts[COL_COUNT] > 0]
            .groupby(COL_COLLECTION_ID)
            .size()
        )
        eligible = nonzero_per_collection[
            nonzero_per_collection >= self.min_series_length
        ].index

        before = counts[COL_COLLECTION_ID].nunique()
        counts = counts[counts[COL_COLLECTION_ID].isin(eligible)].copy()
        after = counts[COL_COLLECTION_ID].nunique()

        if before > after:
            logger.info(
                "Filtered out %d sparse collections with < %d non-zero windows "
                "(%d → %d collections remain).",
                before - after,
                self.min_series_length,
                before,
                after,
            )
        return counts

    def pivot(self, long_df: pd.DataFrame) -> pd.DataFrame:
        """
        Convert long-form to wide-form: rows=windows, columns=collection_ids.
        Useful for batch forecasting."""
        return long_df.pivot_table(
            index=COL_WINDOW,
            columns=COL_COLLECTION_ID,
            values=COL_COUNT,
            fill_value=0,
        )
