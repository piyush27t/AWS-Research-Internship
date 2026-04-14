FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY configs/ ./configs/
COPY src/ ./src/
COPY models/ ./models/

# Create logs directory
RUN mkdir -p logs && chmod 777 logs

# Expose port (Render will use $PORT)
EXPOSE 8000

# Start application - Use shell form to expand environment variables
CMD uvicorn src.api.app:app --host 0.0.0.0 --port ${PORT:-8000}
