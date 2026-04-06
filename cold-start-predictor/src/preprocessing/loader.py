"""
src/preprocessing/loader.py
──────────────────────────────────────────────────────────────────────────────
Chunked, memory-efficient loader for the Google Cluster Dataset 2011 v2.1.
Reads raw gzipped CSV files, applies column selection and event filtering,
and returns a clean DataFrame ready for time-series construction.

All configuration is consumed from configs/config.yaml — nothing is hardcoded.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator, Optional

import pandas as pd
import yaml

logger = logging.getLogger(__name__)


def load_config(config_path: Path) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


# ── Column name constants (used internally after renaming) ────────────────────
COL_TIMESTAMP = "timestamp"
COL_JOB_ID = "job_id"
COL_TASK_INDEX = "task_index"
COL_EVENT_TYPE = "event_type"
COL_SCHED_CLASS = "scheduling_class"
COL_CPU_REQUEST = "cpu_request"
COL_RAM_REQUEST = "ram_request"

# task_usage
COL_START_TIME = "start_time"
COL_CPU_RATE = "cpu_rate"
COL_MEMORY_USAGE = "memory_usage"


class GoogleClusterLoader:
    """
    Loads task_events (and optionally task_usage) from gzipped CSV parts.

    Parameters
    ----------
    config : dict
        Parsed config.yaml content (data section).
    """

    def __init__(self, config: dict) -> None:
        self.cfg = config["data"]
        self.ts_cfg = config["timeseries"]
        self.events_dir = Path(self.cfg["task_events_dir"])
        self.usage_dir = (
            Path(self.cfg["task_usage_dir"])
            if self.cfg.get("task_usage_dir")
            else None
        )
        self.chunk_size: int = self.cfg["chunk_size"]
        self.top_n: int = self.cfg["top_n_jobs"]
        self.trace_days: int = self.cfg["trace_days"]
        self.start_event: int = self.cfg["start_event_type"]

        # Column index → name maps from config
        ec = self.cfg["task_events_columns"]
        self._events_usecols = list(ec.values())
        self._events_col_map = {v: k for k, v in ec.items()}

        if self.cfg.get("task_usage_dir"):
            uc = self.cfg["task_usage_columns"]
            self._usage_usecols = list(uc.values())
            self._usage_col_map = {v: k for k, v in uc.items()}

    # ── public API ────────────────────────────────────────────────────────────

    def load_task_events(self) -> pd.DataFrame:
        """
        Read all task_events CSV parts, filter for START events, keep top-N jobs.

        Returns
        -------
        pd.DataFrame with columns: timestamp, job_id, task_index,
                                   scheduling_class, cpu_request, ram_request
        """
        parts = self._discover_parts(self.events_dir)
        if not parts:
            raise FileNotFoundError(
                f"No .csv.gz files found in {self.events_dir}. "
                "Run scripts/download_data.py first."
            )
        logger.info("Found %d task_events part files.", len(parts))

        frames = list(self._stream_events(parts))
        df = pd.concat(frames, ignore_index=True)
        logger.info("Loaded %d raw START events.", len(df))

        df = self._apply_trace_window(df)
        df = self._filter_top_jobs(df)
        logger.info(
            "After filtering: %d events across %d jobs.",
            len(df), df[COL_JOB_ID].nunique(),
        )
        return df

    def load_task_usage(self) -> Optional[pd.DataFrame]:
        """
        Read task_usage CSV parts (optional). Returns None if disabled.
        """
        if self.usage_dir is None or not self.usage_dir.exists():
            logger.info("task_usage loading disabled or directory missing.")
            return None

        parts = self._discover_parts(self.usage_dir)
        if not parts:
            logger.warning("No task_usage parts found; skipping usage features.")
            return None

        logger.info("Found %d task_usage part files.", len(parts))
        frames = list(self._stream_usage(parts))
        df = pd.concat(frames, ignore_index=True)
        logger.info("Loaded %d task_usage records.", len(df))
        return df

    # ── private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _discover_parts(directory: Path) -> list[Path]:
        return sorted(directory.glob("*.csv.gz"))

    def _stream_events(self, parts: list[Path]) -> Iterator[pd.DataFrame]:
        """Yield chunks from task_events files, renaming columns and filtering."""
        for part in parts:
            logger.debug("Reading %s", part.name)
            try:
                for chunk in pd.read_csv(
                    part,
                    header=None,
                    compression="gzip",
                    usecols=self._events_usecols,
                    dtype={
                        self._events_usecols[0]: "int64",   # timestamp
                        self._events_usecols[2]: "int64",   # job_id
                        self._events_usecols[3]: "int32",   # task_index
                        self._events_usecols[4]: "int8",    # event_type
                    },
                    na_values=[""],
                    chunksize=self.chunk_size,
                    low_memory=False,
                ):
                    chunk = chunk.rename(columns=self._events_col_map)
                    chunk = chunk[chunk[COL_EVENT_TYPE] == self.start_event].copy()
                    chunk.drop(columns=[COL_EVENT_TYPE], inplace=True)
                    if not chunk.empty:
                        yield chunk
            except Exception as exc:
                logger.error("Failed to read %s: %s", part.name, exc)

    def _stream_usage(self, parts: list[Path]) -> Iterator[pd.DataFrame]:
        for part in parts:
            logger.debug("Reading usage %s", part.name)
            try:
                for chunk in pd.read_csv(
                    part,
                    header=None,
                    compression="gzip",
                    usecols=self._usage_usecols,
                    chunksize=self.chunk_size,
                    low_memory=False,
                ):
                    chunk = chunk.rename(columns=self._usage_col_map)
                    yield chunk
            except Exception as exc:
                logger.error("Failed to read usage %s: %s", part.name, exc)

    def _apply_trace_window(self, df: pd.DataFrame) -> pd.DataFrame:
        """Keep only the first `trace_days` of microsecond timestamps."""
        window_us = self.trace_days * 24 * 3600 * 1_000_000
        return df[df[COL_TIMESTAMP] <= window_us].copy()

    def _filter_top_jobs(self, df: pd.DataFrame) -> pd.DataFrame:
        """Keep only the top-N most frequent jobs."""
        top_jobs = (
            df[COL_JOB_ID]
            .value_counts()
            .nlargest(self.top_n)
            .index
        )
        return df[df[COL_JOB_ID].isin(top_jobs)].copy()
