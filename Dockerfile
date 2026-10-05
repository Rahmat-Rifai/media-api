FROM python:3.12-slim

# Install system dependencies: ffmpeg, curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create data and storage directories
RUN mkdir -p /app/data /app/storage/tmp /app/storage/files

# Default environment variables
ENV NETWORK_PROFILE=standard \
    DATA_PATH=/app/data \
    STORAGE_PATH=/app/storage \
    PORT=8080

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
