#!/bin/bash

# Setup script for Ollama (runs on host machine, not in Docker)

set -e

echo "=== CyberSec News Streamer - Ollama Setup ==="
echo ""

# Check if Ollama is installed
if ! command -v ollama &> /dev/null; then
    echo "Ollama is not installed. Installing..."

    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        echo "Detected macOS. Installing via brew..."
        brew install ollama
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Linux
        echo "Detected Linux. Installing via curl..."
        curl -fsSL https://ollama.com/install.sh | sh
    else
        echo "Unsupported OS. Please install Ollama manually from https://ollama.com"
        exit 1
    fi
else
    echo "Ollama is already installed."
fi

echo ""
echo "Starting Ollama service..."

# Start Ollama in background if not running
if ! pgrep -x "ollama" > /dev/null; then
    ollama serve &
    sleep 3
fi

echo ""
echo "Pulling llama3:8b model (this may take a while)..."
ollama pull llama3:8b

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Ollama is running at http://localhost:11434"
echo "Model llama3:8b is ready."
echo ""
echo "You can now start the Docker containers:"
echo "  docker-compose up"
echo ""
