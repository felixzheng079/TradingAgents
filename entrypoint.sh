#!/bin/sh
set -e

# Fix permissions (needs root)
chown -R appuser:appuser /home/appuser/.tradingagents 2>/dev/null || true

# Drop privileges and run the app
exec su appuser -c "$*"