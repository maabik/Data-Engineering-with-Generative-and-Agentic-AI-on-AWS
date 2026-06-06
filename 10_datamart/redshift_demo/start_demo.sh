#!/bin/bash
# Serve the text-to-SQL frontend locally.
#
# The frontend talks directly to API Gateway (CORS is enabled in the CFN
# template), so no proxy is needed. Set the endpoint URL in the UI on first
# load — it persists in the browser's localStorage.

set -euo pipefail

PORT="${PORT:-8080}"

echo "Stopping any existing local web server on port ${PORT}..."
pkill -f "python3 -m http.server ${PORT}" 2>/dev/null || true

echo "Serving frontend on http://localhost:${PORT}/"
cd "$(dirname "$0")/frontend"
python3 -m http.server "${PORT}"
