#!/bin/bash
set -euo pipefail

# Only run in remote (Claude Code on the web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Install shellcheck for linting shell scripts
if ! command -v shellcheck &> /dev/null; then
  apt-get update -qq && apt-get install -y -qq shellcheck > /dev/null 2>&1
fi

# Install Python dependencies for ECTP project
if [ -f "$CLAUDE_PROJECT_DIR/requirements.txt" ]; then
  python3 -m pip install --upgrade pip > /dev/null 2>&1
  python3 -m pip install -r "$CLAUDE_PROJECT_DIR/requirements.txt" > /dev/null 2>&1
fi
