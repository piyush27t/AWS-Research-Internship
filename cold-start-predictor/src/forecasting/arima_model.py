"""
src/forecasting/arima_model.py
──────────────────────────────────────────────────────────────────────────────
Per-collection ARIMA baseline models using pmdarima.auto_arima for automatic
order selection via AIC. Models are trained in parallel and serialized
as a dict { collection_id: fitted_model } via joblib.

Works with Kaggle AWS Cold Start dataset.

Equation from paper (Section III-F):
    φ(B)(1−B)^d C_t = θ(B)ε_t
"""

from __future__ import annotations

import logging
import pickle
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

try:
    import pmdarima as pm
except ImportError as exc:
    raise ImportError("Install pmdarima: pip install pmdarima") from exc

logger = logging.getLogger(__name__)

COL_COLLECTION_ID = "collection_id"
COL_WINDOW = "window"
COL_COUNT = "invocation_count"

# Minimum number of non-zero windows a series must have to be worth fitting.
# Collections whose training slice is almost entirely zeros carry no signal and
# will always produce a constant (all-zero) series that triggers pmdarima's
# "completely constant" warning. We skip them instead.
MIN_NONZERO_WINDOWS = 10

# Minimum variance a series must have to be worth fitting.
# A series with near-zero variance is statistically constant even if it has
# a few non-zero entries — ARIMA will still produce flat predictions.
MIN_SERIES_VARIANCE = 1e-6


def _fit_single_arima(
    collection_id: int,
    series: np.ndarray,
    max_p: int,
    max_d: int,
    max_q: int,
    seasonal: bool,
    m: int,
    ic: str,
) -> tuple[int, Any]:
    """Fit one ARIMA model. Returns (collection_id, model)."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = pm.auto_arima(
                series,
                max_p=max_p,
                max_d=max_d,
                max_q=max_q,
                seasonal=seasonal,
                m=m,
                information_criterion=ic,
                suppress_warnings=True,
                error_action="ignore",
                stepwise=True,
            )
        return collection_id, model
    except Exception as exc:
        logger.warning("ARIMA fit failed for collection %s: %s", collection_id, exc)
        return collection_id, None


class ARIMAForecaster:
    """
    Trains and manages per-collection ARIMA models.

    Parameters
    ----------
    config : dict
        Parsed config.yaml (arima section).
    min_nonzero_windows : int
        Minimum number of non-zero windows required in the training series
        before attempting to fit an ARIMA model. Series below this threshold
        carry no meaningful signal (they are nearly-constant all-zero sequences
        produced by zero-padding sparse collections over the global window range)
        and are skipped with an info-level log message.
    """

    def __init__(self, config: dict, min_nonzero_windows: int = MIN_NONZERO_WINDOWS) -> None:
        cfg = config["arima"]
        self.max_p: int = cfg["max_p"]
        self.max_d: int = cfg["max_d"]
        self.max_q: int = cfg["max_q"]
        self.seasonal: bool = cfg.get("seasonal", False)
        self.m: int = cfg.get("m", 1)
        self.ic: str = cfg["information_criterion"]
        self.n_jobs: int = cfg["n_jobs"]
        # min_series_length from config overrides the default constant
        self.min_nonzero_windows: int = cfg.get("min_series_length", min_nonzero_windows)
        # max_series_len caps the series length fed to ARIMA (ARIMA is O(n²))
        # Using the last N windows captures the most recent seasonal patterns.
        self.max_series_len: int = cfg.get("max_series_len", 500)
        self.models: dict[int, Any] = {}

    # ── training ──────────────────────────────────────────────────────────────

    def fit(self, timeseries_df: pd.DataFrame) -> None:
        """
        Fit one ARIMA model per collection on training-partition data.

        Collections whose training series has fewer than `min_nonzero_windows`
        non-zero values are skipped — they are nearly-constant (all zeros) due
        to zero-padding of sparse collections across the global window range
        and would cause pmdarima to emit constant-series warnings.

        Parameters
        ----------
        timeseries_df : pd.DataFrame
            Long-form DataFrame (collection_id, window, invocation_count).
            Must already be sliced to the training partition.
        """
        collection_series = self._extract_series(timeseries_df)

        # Pre-filter: skip collections that are too sparse OR near-constant.
        # (1) Too sparse  → fewer than min_nonzero_windows non-zero entries
        # (2) Near-constant → variance below MIN_SERIES_VARIANCE even if it
        #     has a few non-zeros; pmdarima will emit 'constant data' warnings
        #     and produce flat predictions for such series.
        eligible: dict[int, np.ndarray] = {}
        skipped_sparse = 0
        skipped_constant = 0
        for cid, series in collection_series.items():
            nonzero_count = int(np.count_nonzero(series))
            variance = float(np.var(series))

            if nonzero_count < self.min_nonzero_windows:
                skipped_sparse += 1
                logger.debug(
                    "Skipping ARIMA for collection %s: only %d non-zero windows "
                    "(min required: %d). Series is too sparse.",
                    cid, nonzero_count, self.min_nonzero_windows,
                )
            elif variance < MIN_SERIES_VARIANCE:
                skipped_constant += 1
                logger.debug(
                    "Skipping ARIMA for collection %s: variance=%.2e is near-zero. "
                    "Series is effectively constant — ARIMA would produce flat predictions.",
                    cid, variance,
                )
            else:
                eligible[cid] = series

        total_skipped = skipped_sparse + skipped_constant
        if total_skipped:
            logger.info(
                "Skipped %d/%d collections (%d sparse, %d near-constant). "
                "%d eligible for ARIMA training.",
                total_skipped, len(collection_series),
                skipped_sparse, skipped_constant, len(eligible),
            )

        logger.info(
            "Training ARIMA for %d eligible collections (n_jobs=%d, max_series_len=%d).",
            len(eligible), self.n_jobs, self.max_series_len,
        )

        if not eligible:
            logger.warning("No eligible collections to train ARIMA on.")
            return

        # Truncate series to last max_series_len points before fitting.
        # ARIMA fitting is O(n²): a 6000-point series takes 20+ minutes;
        # the last 500 points capture recent patterns while fitting in seconds.
        truncated = {
            cid: series[-self.max_series_len:] if len(series) > self.max_series_len else series
            for cid, series in eligible.items()
        }
        n_truncated = sum(1 for cid, s in eligible.items() if len(s) > self.max_series_len)
        if n_truncated:
            logger.info(
                "Truncated %d/%d series to last %d windows for ARIMA fitting.",
                n_truncated, len(eligible), self.max_series_len,
            )

        results = Parallel(n_jobs=self.n_jobs, verbose=5)(
            delayed(_fit_single_arima)(
                collection_id, series, self.max_p, self.max_d, self.max_q, self.seasonal, self.m, self.ic
            )
            for collection_id, series in truncated.items()
        )

        self.models = {cid: mdl for cid, mdl in results if mdl is not None}
        logger.info("ARIMA training complete: %d models fitted.", len(self.models))

    # ── inference ─────────────────────────────────────────────────────────────

    def predict_one_step(self, collection_id: int) -> float:
        """Return a one-step-ahead forecast for the given collection."""
        model = self.models.get(collection_id)
        if model is None:
            return 0.0
        try:
            forecast = model.predict(n_periods=1)
            return float(max(0.0, forecast[0]))
        except Exception as exc:
            logger.warning("ARIMA predict failed for collection %s: %s", collection_id, exc)
            return 0.0

    def evaluate(
        self, test_df: pd.DataFrame
    ) -> tuple[float, float]:
        """
        Compute MAE and RMSE over the test partition.

        Returns
        -------
        (mae, rmse)
        """
        y_true, y_pred = [], []

        for collection_id, group in test_df.groupby(COL_COLLECTION_ID):
            model = self.models.get(collection_id)
            if model is None:
                continue
            series = group.sort_values(COL_WINDOW)[COL_COUNT].to_numpy()
            try:
                preds = model.predict(n_periods=len(series))
                preds = np.clip(preds, 0, None)
                y_true.extend(series.tolist())
                y_pred.extend(preds.tolist())
            except Exception as exc:
                logger.warning("ARIMA eval failed for collection %s: %s", collection_id, exc)

        if not y_true:
            logger.warning("No ARIMA predictions generated during evaluation.")
            return 0.0, 0.0

        y_true = np.array(y_true)
        y_pred = np.array(y_pred)
        mae = float(np.mean(np.abs(y_true - y_pred)))
        rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
        logger.info("ARIMA test — MAE: %.4f, RMSE: %.4f", mae, rmse)
        return mae, rmse

    # ── persistence ───────────────────────────────────────────────────────────

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self.models, f)
        logger.info("ARIMA models saved to %s", path)

    def load(self, path: Path) -> None:
        with open(path, "rb") as f:
            self.models = pickle.load(f)
        logger.info("ARIMA models loaded: %d collections.", len(self.models))

    # ── private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_series(df: pd.DataFrame) -> dict[int, np.ndarray]:
        return {
            cid: g.sort_values(COL_WINDOW)[COL_COUNT].to_numpy(dtype=float)
            for cid, g in df.groupby(COL_COLLECTION_ID)
        }
