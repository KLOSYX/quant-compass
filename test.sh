#!/bin/bash
set -e

echo "Running tests with uv..."
cd backend
uv run pytest
