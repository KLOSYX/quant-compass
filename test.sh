#!/bin/bash
set -e

echo "Running tests with uv..."
cd backend
uv run --extra test python -m pytest tests -s
