#!/bin/bash
# Rebuild an offline-loadable 2025 Taxonomy Package. Does not copy a hidden
# gold zip and does not read verifier fixtures.
set -euo pipefail
python3 /solution/repair.py
