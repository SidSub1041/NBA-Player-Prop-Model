#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_cron.sh
#
# Installs a system cron job to run the NBA Prop Model every morning.
# Default: 9:00 AM local time. Change HOUR/MINUTE below to adjust.
#
# Usage:
#   chmod +x setup_cron.sh
#   ./setup_cron.sh            # Install at 9:00 AM
#   ./setup_cron.sh --remove   # Remove the cron job
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

HOUR=9
MINUTE=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$(which python3)"
LOG_DIR="$SCRIPT_DIR/logs"
CRON_LOG="$LOG_DIR/cron.log"

mkdir -p "$LOG_DIR"

CRON_CMD="$MINUTE $HOUR * * * cd \"$SCRIPT_DIR\" && $PYTHON main.py >> \"$CRON_LOG\" 2>&1"
MARKER="# NBA-Player-Prop-Model"

remove_cron() {
    echo "Removing existing NBA Prop Model cron job..."
    crontab -l 2>/dev/null | grep -v "$MARKER" | crontab - || true
    echo "Done."
}

install_cron() {
    # Remove any previous entry first
    remove_cron

    echo "Installing cron job: run every day at ${HOUR}:$(printf '%02d' $MINUTE)..."

    # Append new entry
    (
        crontab -l 2>/dev/null || true
        echo ""
        echo "$MARKER"
        echo "$CRON_CMD"
    ) | crontab -

    echo ""
    echo "✅  Cron job installed. Current crontab:"
    crontab -l
    echo ""
    echo "Logs will be written to: $CRON_LOG"
    echo ""
    echo "To remove the job later, run:  ./setup_cron.sh --remove"
}

if [[ "${1:-}" == "--remove" ]]; then
    remove_cron
else
    install_cron
fi
