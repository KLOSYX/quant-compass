#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Building the frontend for production..."
cd frontend
npm run build
cd ..

echo "Copying frontend build to backend..."
# Remove old static directory if it exists
rm -rf backend/static
# Copy new build directory to backend/static
mv frontend/build backend/static

echo "Changing to backend directory..."
cd backend

echo "Updating akshare library with uv..."
uv pip install --upgrade akshare

echo "Starting the unified server with uv..."
# The server will now be accessible at http://localhost:8000
uv run uvicorn main:app --host 0.0.0.0 --port 8000
