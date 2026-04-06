"""
src/forecasting/lstm_model.py
──────────────────────────────────────────────────────────────────────────────
Stacked LSTM deep learning forecaster.

Architecture from paper (Section III-G):
    Input  → LSTM(128, return_sequences=True)
           → Dropout(0.3)
           → LSTM(64)
           → Dense(1, activation='linear')

Training:
    Optimizer : Adam(lr=1e-3)
    Loss      : MSE
    Stopping  : EarlyStopping(patience=10) + ReduceLROnPlateau(patience=5)

All hyperparameters come from config.yaml.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def _import_keras():
    """Lazy import to avoid loading TF at module import time."""
    try:
        import tensorflow as tf
        from tensorflow import keras
        return tf, keras
    except ImportError as exc:
        raise ImportError("Install TensorFlow: pip install tensorflow>=2.13") from exc


class LSTMForecaster:
    """
    Stacked LSTM model for one-step-ahead invocation count forecasting.

    Parameters
    ----------
    config : dict
        Parsed config.yaml.
    """

    def __init__(self, config: dict) -> None:
        self.cfg = config["lstm"]
        self.n_features: int | None = None
        self.model = None

    # ── build ─────────────────────────────────────────────────────────────────

    def build(self, n_features: int) -> None:
        """Construct the Keras model graph."""
        tf, keras = _import_keras()
        self.n_features = n_features
        seq_len = config_or_none(self.cfg, "sequence_length") or 10

        inputs = keras.Input(shape=(seq_len, n_features), name="invocation_sequence")

        x = keras.layers.RNN(
            keras.layers.LSTMCell(self.cfg["layer1_units"]),
            return_sequences=True,
            name="lstm_1",
        )(inputs)
        x = keras.layers.Dropout(self.cfg["dropout_rate"], name="dropout")(x)
        x = keras.layers.RNN(
            keras.layers.LSTMCell(self.cfg["layer2_units"]),
            name="lstm_2",
        )(x)
        outputs = keras.layers.Dense(1, activation="linear", name="count_output")(x)

        self.model = keras.Model(inputs=inputs, outputs=outputs, name="stacked_lstm")
        self.model.compile(
            optimizer=keras.optimizers.Adam(learning_rate=self.cfg["learning_rate"]),
            loss="mse",
            metrics=["mae"],
        )
        logger.info("LSTM model built: %d parameters.", self.model.count_params())

    # ── training ──────────────────────────────────────────────────────────────

    def fit(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_val: np.ndarray,
        y_val: np.ndarray,
        checkpoint_path: Path | str = "models/lstm_checkpoint.keras",
    ) -> Any:
        """
        Train the model with early stopping and LR reduction.

        Returns the Keras History object.
        """
        tf, keras = _import_keras()

        if self.model is None:
            self.build(X_train.shape[2])

        callbacks = [
            keras.callbacks.EarlyStopping(
                monitor="val_loss",
                patience=self.cfg["early_stopping_patience"],
                restore_best_weights=True,
                verbose=1,
            ),
            keras.callbacks.ReduceLROnPlateau(
                monitor="val_loss",
                factor=self.cfg["reduce_lr_factor"],
                patience=self.cfg["reduce_lr_patience"],
                verbose=1,
            ),
            keras.callbacks.ModelCheckpoint(
                filepath=str(checkpoint_path),
                monitor="val_loss",
                save_best_only=True,
                verbose=0,
            ),
        ]

        history = self.model.fit(
            X_train,
            y_train,
            validation_data=(X_val, y_val),
            epochs=self.cfg["max_epochs"],
            batch_size=self.cfg["batch_size"],
            callbacks=callbacks,
            verbose=1,
        )
        logger.info("LSTM training finished after %d epochs.", len(history.history["loss"]))
        return history

    # ── inference ─────────────────────────────────────────────────────────────

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Return predicted counts for each sample in X."""
        if self.model is None:
            raise RuntimeError("Model not built. Call build() or load().")
        preds = self.model.predict(X, verbose=0).flatten()
        return np.clip(preds, 0, None)

    def evaluate(
        self, X_test: np.ndarray, y_test: np.ndarray
    ) -> tuple[float, float]:
        """Compute MAE and RMSE on the test set."""
        preds = self.predict(X_test)
        mae = float(np.mean(np.abs(y_test - preds)))
        rmse = float(np.sqrt(np.mean((y_test - preds) ** 2)))
        logger.info("LSTM test — MAE: %.4f, RMSE: %.4f", mae, rmse)
        return mae, rmse

    # ── hyperparameter tuning ─────────────────────────────────────────────────

    def grid_search(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_val: np.ndarray,
        y_val: np.ndarray,
        param_grid: dict,
    ) -> dict:
        """
        Simple grid search over the parameter grid defined in config.yaml.
        Returns the best hyperparameter dict.
        """
        tf, keras = _import_keras()
        import itertools

        keys = list(param_grid.keys())
        values = list(param_grid.values())
        best_val_loss = float("inf")
        best_params = {}

        for combo in itertools.product(*values):
            params = dict(zip(keys, combo))
            logger.info("Grid search trial: %s", params)

            # Temporarily override config with trial params
            trial_cfg = {**self.cfg, **params}
            self.cfg = trial_cfg
            self.model = None
            self.build(X_train.shape[2])

            history = self.fit(X_train, y_train, X_val, y_val)
            val_loss = min(history.history["val_loss"])

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_params = params

        logger.info("Best params: %s  (val_loss=%.4f)", best_params, best_val_loss)
        self.cfg.update(best_params)
        return best_params

    # ── persistence ───────────────────────────────────────────────────────────

    def save(self, path: Path) -> None:
        if self.model is None:
            raise RuntimeError("No model to save.")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.model.save(str(path))
        logger.info("LSTM model saved to %s", path)

    def load(self, path: Path) -> None:
        tf, keras = _import_keras()
        self.model = keras.models.load_model(str(path))
        logger.info("LSTM model loaded from %s", path)


def config_or_none(cfg: dict, key: str):
    return cfg.get(key)
