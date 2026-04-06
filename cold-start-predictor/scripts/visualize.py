#!/usr/bin/env python3
import json
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import pandas as pd
from pathlib import Path

# --- Configuration & Aesthetics ---
sns.set_theme(style="whitegrid", palette="deep")
plt.rcParams.update({
    'font.family': 'sans-serif',
    'axes.titlesize': 14,
    'axes.labelsize': 12,
    'legend.fontsize': 10,
    'figure.dpi': 150
})

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = PROJECT_ROOT / "models"
REPORTS_DIR = PROJECT_ROOT / "reports" / "figures"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

def plot_training_dynamics():
    """Plot Training vs Validation Loss from JSON history."""
    history_path = MODELS_DIR / "training_history.json"
    if not history_path.exists():
        print(f"Skipping history plot: {history_path} not found.")
        return

    with open(history_path) as f:
        history = json.load(f)

    plt.figure(figsize=(10, 5))
    epochs = range(1, len(history['loss']) + 1)
    
    plt.plot(epochs, history['loss'], 'b-', label='Training Loss (MSE)')
    plt.plot(epochs, history['val_loss'], 'r--', label='Validation Loss (MSE)')
    
    plt.title('LSTM Model Training Dynamics: MSE Loss vs Epochs')
    plt.xlabel('Epochs')
    plt.ylabel('Loss (MSE)')
    plt.yscale('log')
    plt.legend()
    plt.tight_layout()
    plt.savefig(REPORTS_DIR / "training_history.png")
    print(f"Saved: {REPORTS_DIR / 'training_history.png'}")

def plot_performance_summary():
    """Plot MAE/RMSE Comparison of ARIMA vs LSTM."""
    report_path = MODELS_DIR / "evaluation_report.json"
    if not report_path.exists():
        print(f"Skipping summary plot: {report_path} not found.")
        return

    with open(report_path) as f:
        report = json.load(f)

    metrics = {
        'Model': ['ARIMA', 'LSTM', 'ARIMA', 'LSTM'],
        'Metric': ['MAE', 'MAE', 'RMSE', 'RMSE'],
        'Value': [report['arima_mae'], report['lstm_mae'], report['arima_rmse'], report['lstm_rmse']]
    }
    df = pd.DataFrame(metrics)

    plt.figure(figsize=(10, 6))
    ax = sns.barplot(data=df, x='Metric', y='Value', hue='Model')
    
    plt.title('Forecasting Accuracy Comparison: ARIMA vs. LSTM')
    plt.ylabel('Model Error (Lower is better)')
    
    # Annotate values
    for p in ax.patches:
        ax.annotate(format(p.get_height(), '.4f'), 
                    (p.get_x() + p.get_width() / 2., p.get_height()), 
                    ha = 'center', va = 'center', 
                    xytext = (0, 9), 
                    textcoords = 'offset points')

    plt.tight_layout()
    plt.savefig(REPORTS_DIR / "performance_metrics.png")
    print(f"Saved: {REPORTS_DIR / 'performance_metrics.png'}")

def plot_cold_start_reduction():
    """Plot baseline vs adaptive cold start rates."""
    report_path = MODELS_DIR / "evaluation_report.json"
    if not report_path.exists():
        return

    with open(report_path) as f:
        report = json.load(f)

    data = {
        'Scenario': ['Baseline (No Warming)', 'Adaptive (Our System)'],
        'Cold Start Rate (%)': [report['baseline_cold_start_rate'] * 100, 
                                 report['adaptive_cold_start_rate'] * 100]
    }
    df = pd.DataFrame(data)

    plt.figure(figsize=(8, 6))
    colors = ['#ff9999', '#66b3ff']
    ax = sns.barplot(data=df, x='Scenario', y='Cold Start Rate (%)', palette=colors)
    
    plt.title('Impact Analysis: Cold Start Rate Reduction')
    plt.ylim(0, max(data['Cold Start Rate (%)']) * 1.3)
    
    # Add reduction annotation
    reduction = report['cold_start_reduction_pct']
    plt.text(0.5, max(data['Cold Start Rate (%)']) * 0.8, f"🚀 {reduction:.1f}% Reduction", 
             ha='center', fontsize=12, fontweight='bold', color='darkblue', 
             bbox=dict(facecolor='white', alpha=0.8, edgecolor='none'))

    plt.tight_layout()
    plt.savefig(REPORTS_DIR / "cold_start_reduction.png")
    print(f"Saved: {REPORTS_DIR / 'cold_start_reduction.png'}")

def plot_forecast_sample():
    """Plot actual vs predicted for a snippet of test data with research overlays."""
    samples_path = MODELS_DIR / "forecast_samples.json"
    if not samples_path.exists():
        print(f"Skipping forecast sample: {samples_path} not found.")
        return

    with open(samples_path) as f:
        data = json.load(f)

    actual = np.array(data["actual"])
    lstm_pred = np.array(data["lstm_pred"])
    pre_warmed = np.array(data.get("pre_warmed", []))
    threshold = data.get("threshold", 0.0)
    
    # Slice first 100 points for clarity
    slice_len = min(150, len(actual))
    time_steps = np.arange(slice_len)
    
    plt.figure(figsize=(14, 7))
    plt.plot(time_steps, actual[:slice_len], label='Actual Invocation Count', color='black', alpha=0.4, linewidth=2)
    plt.plot(time_steps, lstm_pred[:slice_len], label='LSTM Predicted (EMA-Smoothed)', color='crimson', linestyle='--', linewidth=1.5)
    
    # Research Overlay: Decision Threshold
    plt.axhline(y=threshold, color='green', linestyle=':', alpha=0.6, label=f'Policy Threshold ({threshold} reqs)')
    
    # Research Overlay: Pre-warm Triggers
    if len(pre_warmed) > 0:
        trigger_indices = np.where(pre_warmed[:slice_len])[0]
        plt.scatter(trigger_indices, [actual[i] for i in trigger_indices], 
                    color='green', marker='^', s=100, label='Adaptive Pre-warm Trigger', zorder=5)

    plt.title('High-Fidelity Research Trace: Adaptive Pre-warming vs. Dynamic Workload', fontsize=16)
    plt.xlabel('Windows (5-min intervals)', fontsize=12)
    plt.ylabel('Invocations / Window', fontsize=12)
    plt.grid(True, which='both', linestyle='--', alpha=0.5)
    plt.legend(loc='upper right', frameon=True, shadow=True)
    
    plt.tight_layout()
    plt.savefig(REPORTS_DIR / "forecast_trace.png")
    print(f"Saved: {REPORTS_DIR / 'forecast_trace.png'}")

if __name__ == "__main__":
    print(f"📊 Generating Research Figures in: {REPORTS_DIR}")
    plot_training_dynamics()
    plot_performance_summary()
    plot_cold_start_reduction()
    plot_forecast_sample()
    print("✅ All figures generated successfully.")
