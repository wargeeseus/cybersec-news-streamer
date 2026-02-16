FROM python:3.11-slim

# Install FFmpeg, curl and other dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-dejavu-core \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY assets/ ./assets/

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 8080

# Default command (can be overridden in docker-compose)
CMD ["uvicorn", "src.web.app:app", "--host", "0.0.0.0", "--port", "8080"]
