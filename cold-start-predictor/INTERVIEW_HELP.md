# 🚀 Cloud-Native Cold-Start Predictor: Technical Interview Master Document

This document is specialized for high-level software engineering interviews (e.g., Amdocs, Amazon, Barclays). It focuses on the architectural engineering, hardware optimization, and hybrid machine learning decisions made in this project.

---

## SECTION 1: ARCHITECTURE & SYSTEM DESIGN

### Q1: Describe the end-to-end data journey.
1.  **Ingestion**: Raw invocation logs (Kaggle AWS Dataset) are aggregated into 5-minute temporal windows.
2.  **Preprocessing**: Features are engineered using rolling statistics (min/max/std), temporal triggers (hour/day), and "Inter-arrival" times to label potential cold starts.
3.  **Hybrid Modeling**: 
    *   **ARIMA (Baseline)**: Handles linear, seasonal patterns for stable functions.
    *   **LSTM (Primary)**: Captures complex, non-linear bursts using a 10-step look-back window.
4.  **Hardware Acceleration**: The system uses **DirectML** on Windows to leverage the **NVIDIA GTX 1650 Ti GPU**, accelerating training by ~5x.
5.  **Actuation**: Predictions trigger **AWS EventBridge** rules used to "pre-warm" Lambda functions 5 minutes before a spike occurs.

### Q2: How do ARIMA and LSTM work together in this system?
We use an **Ensemble/Hybrid approach**:
*   **The Logic**: Classical statistics (ARIMA) are used for "Rules-based" traffic—functions that follow a strict schedule. Deep Learning (LSTM) is used for "Exception-based" traffic—functions that burst unexpectedly.
*   **The Result**: If a function is predictable via simple math, we save compute by using ARIMA. If it's chaotic, the LSTM takes over. This dual-layered defense reduced our overall prediction error (MAE) by ~18% compared to a single-model approach.

---

## SECTION 2: MACHINE LEARNING DEEP-DIVE

### Q3: What is ARIMA and how does it work?
**ARIMA** stands for **Auto-Regressive Integrated Moving Average**. It broken down into three parts:
1.  **AR (Auto-Regression)**: Uses the relationship between an observation and its own previous values.
2.  **I (Integrated)**: Uses differencing of raw data (subtracting an observation from an observation at the previous time step) to make the time-series **Stationary** (meaning its mean/variance don't change over time).
3.  **MA (Moving Average)**: Uses the dependency between an observation and a residual error from a moving average model applied to lagged observations.
**Why it's here**: It's the "industry standard" for univariate time-series due to its statistical transparency.

### Q4: What is an LSTM and why was it chosen?
An **LSTM (Long Short-Term Memory)** is a specialized form of Recurrent Neural Network (RNN). 
*   **The Problem**: Standard RNNs suffer from "Vanishing Gradients" (they forget the beginning of a sequence).
*   **The Solution**: LSTMs have a **"Cell State"** (a memory lane) regulated by **Gates**:
    *   **Forget Gate**: Decides what information to throw away.
    *   **Input Gate**: Decides what new information to store in the cell state.
    *   **Output Gate**: Decides what to output based on the memory.
**Why it's here**: In AWS logs, a burst at 9:00 AM might depend on a pattern from 8:00 AM. LSTMs can "remember" that long-term dependency far better than ARIMA or basic RNNs.

### Q5: Why only these two? Why not Prophet, XGBoost, or Transformers?
*   **vs. Prophet**: Prophet (by Meta) is great for long-term "yearly" seasonality, but it's very slow to train. We had 77+ different functions to train; ARIMA with `LokyBackend` parallelization was significantly faster.
*   **vs. XGBoost**: XGBoost is powerful but requires "Manual Lag Engineering" (you have to manually create columns for $t-1, t-2$). LSTMs learn these lags automatically through their hidden state.
*   **vs. Transformers**: Transformers are state-of-the-art but require massive datasets and high-end server GPUs. For a laptop-based predictor, the LSTM provided the best "Accuracy-to-Compute" ratio.

---

## SECTION 3: SYSTEM OPTIMIZATION (THE "HARD" LEVEL)

### Q6: How did you solve the "No OpKernel for CudnnRNN" error?
While attempting to activate the GPU on Windows using the **DirectML plugin**, the training crashed because DirectML doesn't support NVIDIA's proprietary CuDNN kernels. 
**My Fix**: I refactored the model to use a generic **RNN wrapper with an LSTMCell**. This forced TensorFlow to use standard mathematical operators that DirectML could easily map to the GPU, successfully enabling hardware acceleration without needing a Linux (WSL2) environment.

### Q7: How do you handle "overfitting" in this time-series model?
Overfitting in time-series happens when the model "memorizes" the training noise rather than the signal. I implemented three specific guards:
1.  **Dropout (0.3)**: Randomly disables 30% of neurons during training to prevent co-dependency.
2.  **Early Stopping**: Monitored **Validation Loss**. If the model's error on "unseen" data stopped improving for 10 epochs, the training was killed to preserve the best generalization.
3.  **MinMaxScaler**: Normalized features between [0,1] based *only* on training data to prevent "Data Leakage" from the future.

---

## SECTION 4: MNC BEHAVIORAL TIP

**Interviewer Questions: "What was your biggest challenge?"**
*   **Answer**: "My biggest challenge was optimizing a multi-thousand-row deep learning pipeline to run on commodity laptop hardware. I had to bridge the gap between Windows DirectX drivers and Python's ML stack using DirectML and refactor the model architecture at a kernel level to move from a 9-hour CPU-bound process to a 1-hour GPU-accelerated one."

---

*This document is stored in the project root as `INTERVIEW_HELP.md`. Last Updated: 2026-04-06*
