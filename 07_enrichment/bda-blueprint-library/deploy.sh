#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 scripts/build_corpus.py
python3 scripts/deploy.py
