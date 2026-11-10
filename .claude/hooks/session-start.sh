#!/bin/bash
set -euo pipefail

# Only run in Claude Code remote environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Install Poetry dependencies (idempotent — skips if already installed)
poetry install

# Write environment variables for the session.
# The API key is never stored in this file: it comes from a local .env when
# present, otherwise it must be configured as a remote-environment secret.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  if [ -f .env ] && grep -q '^OPENROUTER_API_KEY=' .env; then
    echo "export $(grep '^OPENROUTER_API_KEY=' .env | head -1)" >> "$CLAUDE_ENV_FILE"
  fi
  echo 'export OPENROUTER_BASE_URL=https://openrouter.ai/api/v1' >> "$CLAUDE_ENV_FILE"
  echo 'export OPENROUTER_TIMEOUT_S=60' >> "$CLAUDE_ENV_FILE"
  echo 'export OPENROUTER_MAX_RETRIES=3' >> "$CLAUDE_ENV_FILE"
  echo 'export OPENROUTER_RETRY_BACKOFF_MS=500' >> "$CLAUDE_ENV_FILE"
fi
