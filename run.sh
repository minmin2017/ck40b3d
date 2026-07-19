#!/bin/bash
set -e

# Navigate to script directory
cd "$(dirname "$0")"

# Create .venv if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Upgrade pip and install requirements
echo "Installing dependencies..."
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# Start uvicorn server
echo "Starting CK40B-3D server on http://localhost:8360..."
exec .venv/bin/python3 -m uvicorn server:app --host 127.0.0.1 --port 8360
