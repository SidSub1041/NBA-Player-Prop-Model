#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_cron.sh — install the game-day runner on any always-on Linux box
# (a VPS, a Raspberry Pi, a home server). It schedules run_daily.sh, which
# grades yesterday, builds today's board, exports CSVs and pushes the data;
# GitHub Actions then redeploys the site.
#
# Before installing, make sure this host can reach the NBA API:
#     python3 probe_nba.py           (cloud datacenter IPs are often blocked)
# and that `git push origin main` works non-interactively here (SSH deploy key
# with write access, or a fine-grained token in a credential store).
#
# Usage:
#   chmod +x setup_cron.sh
#   ./setup_cron.sh            # install (9:15, 12:00, 15:00, 18:00 local time)
#   ./setup_cron.sh --remove   # remove
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

RUN_TIMES=("9:15" "12:00" "15:00" "18:00")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$(command -v python3)"
LOG_DIR="$SCRIPT_DIR/logs"
CRON_LOG="$LOG_DIR/cron.log"
MARKER="# NBA-Player-Prop-Model"

mkdir -p "$LOG_DIR"
chmod +x "$SCRIPT_DIR/run_daily.sh"

remove_cron() {
    echo "Removing existing NBA Prop Model cron jobs..."
    crontab -l 2>/dev/null | grep -v "$MARKER" | crontab - || true
    echo "Done."
}

install_cron() {
    remove_cron
    echo "Installing cron jobs at ${RUN_TIMES[*]} (server local time)..."
    (
        crontab -l 2>/dev/null || true
        for t in "${RUN_TIMES[@]}"; do
            h="${t%%:*}"; m="${t##*:}"
            echo "$((10#$m)) $((10#$h)) * * * cd \"$SCRIPT_DIR\" && PYTHON=\"$PYTHON\" /bin/bash run_daily.sh >> \"$CRON_LOG\" 2>&1 $MARKER"
        done
        # Closing-line snapshots: every 30 minutes from 11:15 to 23:45 (re-price games about to tip)
        echo "15,45 11-23 * * * cd \"$SCRIPT_DIR\" && PYTHON=\"$PYTHON\" MODEL_ARGS=--snapshot /bin/bash run_daily.sh >> \"$CRON_LOG\" 2>&1 $MARKER"
    ) | crontab -

    echo ""
    echo "✅  Installed. Current crontab:"
    crontab -l | grep "$MARKER" || true
    echo ""
    echo "Each run logs to: $CRON_LOG"
    echo "Set MODEL_ARGS=--fast in the cron line to skip playtype analysis."
    echo "Remove later with:  ./setup_cron.sh --remove"
}

if [[ "${1:-}" == "--remove" ]]; then
    remove_cron
else
    install_cron
fi
