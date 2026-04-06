"""
src/preprocessing/kaggle_loader.py
──────────────────────────────────────────────────────────────────────────────
Loader for Kaggle AWS Cold Start dataset (Google Borg traces).

Dataset columns (actual CSV):
  - Unnamed: 0     : row index
  - time           : timestamp in microseconds (0 = sentinel/missing — dropped)
  - instance_events_type : event-type code (0=SUBMIT, 1=SCHEDULE, 2=REMOVE, ...)
  - collection_id  : identifier for the collection/job
  - scheduling_class : scheduling priority class (0–3)
  - collection_type : type of collection
  - priority       : priority level (integer)
  - alloc_collection_id : allocated collection ID
  - instance_index : index of the instance
  - machine_id     : identifier of the machine
  (plus many usage/resource columns that are ignored here)

Key fix: instance_events_type is an EVENT-TYPE CODE, not an invocation count.
We filter to rows whose event type is in `invocation_event_types` (SUBMIT=0,
SCHEDULE=1) so that TimeSeriesBuilder counts only real invocation events.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

import pandas as pd
import yaml

logger = logging.getLogger(__name__)


def load_config(config_path: Path) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


class KaggleAWSLoader:
    """
    Loads Kaggle AWS Cold Start dataset (Borg traces) from CSV/Excel files,
    filters to invocation events only, and drops invalid timestamps.

    Parameters
    ----------
    config : dict
        Parsed config.yaml content.
    """

    # The actual column name in the CSV for the event-type field
    COL_INSTANCE_EVENTS_TYPE = "instance_events_type"

    def __init__(self, config: dict) -> None:
        self.cfg = config["data"]
        self.ts_cfg = config["timeseries"]
        self.raw_dir = Path(self.cfg["raw_dir"])
        self.chunk_size: int = self.cfg["chunk_size"]
        self.top_n: int = self.cfg["top_n_collections"]

        # Event type codes that represent real invocations (SUBMIT=0, SCHEDULE=1)
        self.invocation_event_types: List[int] = self.cfg.get(
            "invocation_event_types", [0, 1]
        )

        # Column names (from config — kept for forward compat, but we use known names)
        self.col_time = "time"
        self.col_collection_id = "collection_id"
        self.col_instance_events_type = self.COL_INSTANCE_EVENTS_TYPE
        self.col_scheduling_class = "scheduling_class"
        self.col_collection_type = "collection_type"
        self.col_priority = "priority"
        self.col_machine_id = "machine_id"
        self.col_instance_index = "instance_index"
        self.col_alloc_collection_id = "alloc_collection_id"

    # ── public API ─────────────────────────────────────────────────────────────

    def load_dataset(self) -> pd.DataFrame:
        """
        Load the Kaggle dataset, filter to invocation events, drop bad timestamps.

        Returns
        -------
        pd.DataFrame
            Cleaned DataFrame with columns: time, collection_id,
            scheduling_class, priority, machine_id, (plus other metadata).
            The `time` column contains valid microsecond timestamps (> 0).
        """
        # Find data file (Excel or CSV)
        files = list(self.raw_dir.glob("*.xlsx")) + list(self.raw_dir.glob("*.csv"))

        if not files:
            raise FileNotFoundError(
                f"No .xlsx or .csv files found in {self.raw_dir}. "
                "Please place your Kaggle dataset in data/raw/"
            )

        file_path = files[0]  # Use first file found
        logger.info("Loading dataset from: %s", file_path.name)

        # Load file based on extension
        if file_path.suffix.lower() == ".xlsx":
            df = pd.read_excel(file_path)
        else:
            df = pd.read_csv(file_path, low_memory=False)

        logger.info("Loaded %d raw rows from dataset", len(df))

        # Normalize column names (lowercase, strip whitespace)
        df.columns = df.columns.str.lower().str.strip()

        # Validate required columns exist
        self._validate_columns(df)

        # ── Fix 1: Drop zero/null timestamps ──────────────────────────────────
        # time=0 is a sentinel value that means "unknown/missing". All events
        # with time=0 would land in window-0, creating a false spike and making
        # every collection's time-series look nearly constant (all zeros in
        # other windows). We drop them.
        before = len(df)
        df = df[df[self.col_time] > 0].copy()
        dropped_zeros = before - len(df)
        if dropped_zeros > 0:
            logger.info(
                "Dropped %d rows with time==0 (missing/sentinel timestamps).",
                dropped_zeros,
            )

        # ── Fix 2: Filter to invocation events only ───────────────────────────
        # instance_events_type is a CODE (0=SUBMIT, 1=SCHEDULE, 2=REMOVE, …),
        # NOT an invocation count. We keep only the event types that represent
        # an actual function invocation so the time-series reflects real traffic.
        before = len(df)
        df = df[df[self.col_instance_events_type].isin(self.invocation_event_types)].copy()
        dropped_types = before - len(df)
        logger.info(
            "Filtered to invocation events (types=%s): %d → %d rows (dropped %d).",
            self.invocation_event_types,
            before,
            len(df),
            dropped_types,
        )

        if df.empty:
            raise ValueError(
                f"No rows remain after filtering to invocation_event_types="
                f"{self.invocation_event_types}. Check your config or the dataset."
            )

        # ── Fix 3: Convert time column to microseconds (if not already) ───────
        df = self._process_timestamps(df)

        # ── Filter by top N collections ───────────────────────────────────────
        df = self._filter_top_collections(df)

        logger.info(
            "Dataset ready: %d events, %d collections, %d machines",
            len(df),
            df[self.col_collection_id].nunique(),
            df[self.col_machine_id].nunique() if self.col_machine_id in df.columns else 0,
        )

        return df

    # ── private helpers ────────────────────────────────────────────────────────

    def _validate_columns(self, df: pd.DataFrame) -> None:
        """Check that required columns exist after lowercasing."""
        required = [
            self.col_time,
            self.col_collection_id,
            self.col_instance_events_type,
            self.col_scheduling_class,
            self.col_priority,          # added: needed for feature engineering
        ]

        missing = [col for col in required if col not in df.columns]
        if missing:
            raise ValueError(
                f"Missing required columns: {missing}. "
                f"Available columns: {list(df.columns)}"
            )

        logger.info(
            "Column validation passed. Dataset has %d columns: %s",
            len(df.columns),
            list(df.columns),
        )

    def _process_timestamps(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Ensure the `time` column contains valid integer microsecond timestamps.

        The Borg trace CSV stores timestamps as large integers (microseconds).
        We detect the scale and convert accordingly.
        """
        df = df.copy()
        t = df[self.col_time]

        if t.dtype == object:
            # Try parsing datetime strings
            try:
                df[self.col_time] = (
                    pd.to_datetime(t, errors="coerce").astype("int64") // 1_000
                )
                logger.info("Converted datetime strings to microsecond timestamps.")
            except Exception as e:
                logger.warning("Could not convert time to numeric: %s. Using as-is.", e)
        else:
            # Already numeric — check scale
            max_val = t.max()
            if max_val < 1e10:
                # Looks like seconds or milliseconds — convert to microseconds
                df[self.col_time] = (t * 1_000_000).astype("int64")
                logger.info(
                    "Converted numeric timestamps to microseconds (max was %.2e).", max_val
                )
            # else: already in microseconds (Borg traces are ~2.6e12 µs)

        # Final guard: drop any NaT/NaN that appeared after conversion
        df = df[df[self.col_time] > 0].dropna(subset=[self.col_time]).copy()
        return df

    def _filter_top_collections(self, df: pd.DataFrame) -> pd.DataFrame:
        """Keep only the top-N most frequent collections."""
        top_collections = (
            df[self.col_collection_id]
            .value_counts()
            .nlargest(self.top_n)
            .index
        )
        filtered = df[df[self.col_collection_id].isin(top_collections)].copy()
        logger.info(
            "Filtered to top %d collections: %d → %d rows",
            self.top_n,
            len(df),
            len(filtered),
        )
        return filtered
