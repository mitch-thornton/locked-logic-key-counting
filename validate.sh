#!/bin/sh
# Author: Mitchell A. Thornton
# Copyright (c) 2026 Mitchell A. Thornton
# Convenience wrapper. Runs the repository self-check; see validate_repo.py.
exec python3 "$(dirname "$0")/validate_repo.py" "$@"
