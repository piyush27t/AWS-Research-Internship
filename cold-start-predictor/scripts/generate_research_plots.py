import json
import os
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import numpy as np

# Set aesthetic style
sns.set_theme(style="whitegrid", palette="muted")
plt.rcParams.update({'font.size': 12, 'figure.autolayout': True})

# Paths
MODELS_DIR = "models"
REPORTS_DIR = "reports"
VISUALS_DIR = os.path.join(REPORTS_DIR, "visuals")

# Ensure directory exists
os.makedirs(VISUALS_DIR, exist_ok=True)

def load_data():
    with open(os.path.join(MODELS_DIR, "evaluation_report.json"), "r") as f:
        eval_data = json.load(f)
    with open(os.path.join(MODELS_DIR, "forecast_samples.json"), "r") as f:
        forecast_data = json.load(f)
    return eval_data, forecast_data

def plot_workload_forecast(forecast_data):
    """Fig 1: Predicted vs. Actual Invocations (Burst Tracking)"""
    actual = forecast_data["actual"][:200]  # First 200 samples for clarity
    pred = forecast_data["lstm_pred"][:200]
    
    plt.figure(figsize=(12, 5))
    plt.plot(actual, label="Actual Invocations", alpha=0.7, color='navy', linewidth=2)
    plt.plot(pred, label="LSTM Prediction", alpha=0.8, color='crimson', linestyle='--')
    
    plt.title("Workload Forecasting Accuracy (LSTM Model)", fontweight='bold')
    plt.xlabel("Time Cycle (5-min intervals)")
    plt.ylabel("Invocations per Cycle")
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(VISUALS_DIR, "fig1_workload_forecast.png"), dpi=300)
    plt.close()
    print("Generated Fig 1: Workload Forecast")

def plot_cold_start_reduction(eval_data):
    """Fig 2: Cold Start Mitigation Performance"""
    categories = ["Baseline (No Warmup)", "Adaptive Pre-warming"]
    values = [eval_data["baseline_cold_start_rate"] * 100, eval_data["adaptive_cold_start_rate"] * 100]
    
    plt.figure(figsize=(8, 6))
    bars = plt.bar(categories, values, color=['#ff9999', '#66b3ff'])
    
    plt.ylabel("Cold Start Rate (%)")
    plt.title("Cold Start Rate Comparison (Baseline vs. Adaptive)", fontweight='bold')
    
    # Add data labels
    for bar in bars:
        yval = bar.get_height()
        plt.text(bar.get_x() + bar.get_width()/2, yval + 1, f"{yval:.2f}%", ha='center', fontweight='bold')
    
    plt.ylim(0, max(values) * 1.3)
    plt.tight_layout()
    plt.savefig(os.path.join(VISUALS_DIR, "fig2_cold_start_reduction.png"), dpi=300)
    plt.close()
    print("Generated Fig 2: Cold Start Reduction")

def plot_efficiency_tradeoff(eval_data):
    """Fig 3: Efficiency vs. Overhead Trade-off"""
    # Compare percentage reduction vs costs
    reduction = eval_data["cold_start_reduction_pct"]
    over_provision = eval_data["over_provision_rate"] * 100
    
    metrics = ["Cold Start Reduction", "Over-provisioning Rate"]
    values = [reduction, over_provision]
    
    plt.figure(figsize=(8, 6))
    colors = sns.color_palette("viridis", 2)
    plt.barh(metrics, values, color=colors)
    
    plt.xlabel("Percentage (%)")
    plt.title("Policy Efficiency: Mitigation vs. Overhead", fontweight='bold')
    
    for i, v in enumerate(values):
        plt.text(v + 1, i, f"{v:.2f}%", va='center', fontweight='bold')
        
    plt.xlim(0, 105)
    plt.tight_layout()
    plt.savefig(os.path.join(VISUALS_DIR, "fig3_efficiency_tradeoff.png"), dpi=300)
    plt.close()
    print("Generated Fig 3: Efficiency Trade-off")

if __name__ == "__main__":
    eval_data, forecast_data = load_data()
    plot_workload_forecast(forecast_data)
    plot_cold_start_reduction(eval_data)
    plot_efficiency_tradeoff(eval_data)
    print(f"\nAll visuals saved to {VISUALS_DIR}")
