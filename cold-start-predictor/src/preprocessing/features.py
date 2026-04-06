"""
src/preprocessing/features.py
──────────────────────────────────────────────────────────────────────────────
Feature engineering for the LSTM forecasting model.

Works with Kaggle AWS Cold Start dataset.

Features per time window:
  - invocation_count
  - rolling_mean_k      (rolling mean over k preceding windows)
  - rolling_std_k       (rolling std  over k preceding windows)
  - time_of_day         (continuous, 0–1 mapped from window index within day)
  - day_of_week         (ordinal 0–6)
  - scheduling_class_norm  (normalized from Kaggle dataset)
  - priority_norm          (normalized from Kaggle dataset)

All statistics are computed per-collection. Normalization (min-max to [0,1]) is
fitted on training data only and stored for inference-time reuse.

Fix applied:
  - _rolling_features no longer uses a fragile index-based reassignment of
    collection_id. groupby().apply() now preserves collection_id correctly
    by operating only on non-grouping columns while keeping the group key in
    the DataFrame (include_groups=False on pandas >=2.2, or via explicit merge).
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler

logger = logging.getLogger(__name__)

COL_COLLECTION_ID = "collection_id"
COL_WINDOW = "window"
COL_COUNT = "invocation_count"
COL_COLD_START = "is_cold_start"


FEATURE_COLUMNS = [
    COL_COUNT,
    "rolling_mean",
    "rolling_std",
    "time_of_day",
    "day_of_week",
    "scheduling_class_norm",
    "priority_norm",
]


class FeatureEngineer:
    """
    Adds engineered features to the time-series DataFrame and
    fits / applies a per-feature MinMaxScaler.

    Parameters
    ----------
    rolling_k : int
        Number of preceding windows for rolling statistics.
    window_seconds : int
        Duration of each window in seconds (used to derive time-of-day).
    """

    def __init__(self, rolling_k: int, window_seconds: int) -> None:
        self.rolling_k = rolling_k
        self.window_seconds = window_seconds
        self._windows_per_day = int(86400 / window_seconds)
        self._windows_per_week = self._windows_per_day * 7
        self.scaler: MinMaxScaler | None = None

    # ── public API ────────────────────────────────────────────────────────────

    def build_features(
        self,
        ts_df: pd.DataFrame,
        metadata_df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """
        Compute all features. Does NOT normalize — call fit_transform / transform
        separately.

        Parameters
        ----------
        ts_df : pd.DataFrame
            Time-series DataFrame from ColdStartSimulator.annotate().
        metadata_df : pd.DataFrame, optional
            Metadata with collection attributes (scheduling_class, priority, etc.).

        Returns
        -------
        pd.DataFrame
            Feature DataFrame with one row per (collection_id, window).
        """
        df = ts_df.sort_values([COL_COLLECTION_ID, COL_WINDOW]).copy()

        # ── Fix: compute rolling features without losing collection_id ─────────
        # Previously the code did a groupby().apply() then reset_index() and
        # manually re-assigned collection_id via position — fragile if apply()
        # changes row order or count.  We now apply the rolling function and
        # concat the results with the group key preserved in each sub-DataFrame.
        rolling_parts = []
        for cid, group in df.groupby(COL_COLLECTION_ID, sort=False):
            group = group.copy()
            c = group[COL_COUNT]
            group["rolling_mean"] = c.rolling(self.rolling_k, min_periods=1).mean()
            group["rolling_std"] = (
                c.rolling(self.rolling_k, min_periods=1).std().fillna(0.0)
            )
            rolling_parts.append(group)

        df = pd.concat(rolling_parts, ignore_index=True)

        # Temporal features derived from window index
        df["time_of_day"] = (df[COL_WINDOW] % self._windows_per_day) / self._windows_per_day
        df["day_of_week"] = (df[COL_WINDOW] // self._windows_per_day) % 7

        # Collection metadata features
        if metadata_df is not None:
            df = self._merge_metadata(df, metadata_df)
        else:
            df["scheduling_class_norm"] = 0.0
            df["priority_norm"] = 0.0

        df[FEATURE_COLUMNS] = df[FEATURE_COLUMNS].fillna(0.0)
        return df

    def fit_transform(self, feature_df: pd.DataFrame) -> pd.DataFrame:
        """Fit scaler on feature_df and return normalized copy."""
        self.scaler = MinMaxScaler(feature_range=(0, 1))
        feature_df = feature_df.copy()
        feature_df[FEATURE_COLUMNS] = self.scaler.fit_transform(
            feature_df[FEATURE_COLUMNS]
        )
        logger.info("Scaler fitted on %d rows.", len(feature_df))
        return feature_df

    def transform(self, feature_df: pd.DataFrame) -> pd.DataFrame:
        """Apply pre-fitted scaler to new data."""
        if self.scaler is None:
            raise RuntimeError("Call fit_transform() before transform().")
        feature_df = feature_df.copy()
        feature_df[FEATURE_COLUMNS] = self.scaler.transform(
            feature_df[FEATURE_COLUMNS]
        )
        return feature_df

    def save_scaler(self, path: Path) -> None:
        with open(path, "wb") as f:
            pickle.dump(self.scaler, f)
        logger.info("Scaler saved to %s", path)

    def load_scaler(self, path: Path) -> None:
        with open(path, "rb") as f:
            self.scaler = pickle.load(f)
        logger.info("Scaler loaded from %s", path)

    # ── private helpers ───────────────────────────────────────────────────────

    def _merge_metadata(
        self, df: pd.DataFrame, metadata_df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Merge collection metadata (scheduling_class, priority) into features.
        Normalizes on 0-1 scale.
        """
        meta = metadata_df[
            [COL_COLLECTION_ID, "scheduling_class", "priority"]
        ].drop_duplicates(subset=[COL_COLLECTION_ID])

        # Normalize to 0-1 range
        if "scheduling_class" in meta.columns:
            max_sched = meta["scheduling_class"].max()
            meta = meta.copy()
            meta["scheduling_class_norm"] = (
                meta["scheduling_class"] / max(max_sched, 1)
            )
        else:
            meta = meta.copy()
            meta["scheduling_class_norm"] = 0.0

        if "priority" in meta.columns:
            max_priority = meta["priority"].max()
            meta["priority_norm"] = meta["priority"] / max(max_priority, 1)
        else:
            meta["priority_norm"] = 0.0

        df = df.merge(
            meta[[COL_COLLECTION_ID, "scheduling_class_norm", "priority_norm"]],
            on=COL_COLLECTION_ID,
            how="left",
        )
        df[["scheduling_class_norm", "priority_norm"]] = (
            df[["scheduling_class_norm", "priority_norm"]].fillna(0.0)
        )
        return df


class SequenceBuilder:
    """
    Converts the feature DataFrame into (X, y) numpy arrays for LSTM training.

    Parameters
    ----------
    sequence_length : int
        Number of past windows fed as input (look-back horizon).
    """

    def __init__(self, sequence_length: int) -> None:
        self.seq_len = sequence_length

    def build_sequences(
        self, feature_df: pd.DataFrame
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Build LSTM sequences for all collections.

        Returns
        -------
        X : np.ndarray, shape (n_samples, seq_len, n_features)
        y : np.ndarray, shape (n_samples,)
        collection_ids : np.ndarray, shape (n_samples,) — for per-collection tracking
        """
        X_list, y_list, collection_list = [], [], []

        for collection_id, group in feature_df.groupby(COL_COLLECTION_ID, sort=False):
            group = group.sort_values(COL_WINDOW)
            values = group[FEATURE_COLUMNS].to_numpy(dtype=np.float32)
            targets = group[COL_COUNT].to_numpy(dtype=np.float32)

            if len(values) <= self.seq_len:
                # Not enough windows to build even one sequence — skip
                logger.debug(
                    "Skipping collection %s: only %d windows (need > %d).",
                    collection_id, len(values), self.seq_len,
                )
                continue

            for i in range(self.seq_len, len(values)):
                X_list.append(values[i - self.seq_len : i])
                y_list.append(targets[i])
                collection_list.append(collection_id)

        if not X_list:
            raise ValueError(
                "No sequences built — all collections have fewer windows than "
                f"sequence_length={self.seq_len}. Reduce sequence_length or "
                "increase trace_days / top_n_collections."
            )

        X = np.stack(X_list, axis=0)
        y = np.array(y_list, dtype=np.float32)
        collection_ids = np.array(collection_list)
        logger.info(
            "Built %d sequences (seq_len=%d, features=%d).",
            len(X), self.seq_len, X.shape[2],
        )
        return X, y, collection_ids
