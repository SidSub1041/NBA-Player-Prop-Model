#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup_mac.sh — One-time Mac setup for the NBA Player Prop Model
#
# Usage:
#   chmod +x setup_mac.sh
#   ./setup_mac.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_LABEL="com.nba.prop.model"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
RUN_HOUR=9    # 9 AM — change this to adjust the daily run time
RUN_MINUTE=0

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo "══════════════════════════════════════════════════════"
echo "   NBA Player Prop Model — Mac Setup"
echo "══════════════════════════════════════════════════════"
echo ""

# ── 1. Check Python 3 ────────────────────────────────────────────────
echo "▶ Checking Python 3..."
if ! command -v python3 &>/dev/null; then
    echo "  Python 3 not found. Install it from https://www.python.org or via Homebrew:"
    echo "    brew install python3"
    exit 1
fi
PYTHON="$(command -v python3)"
echo "  Using: $PYTHON ($($PYTHON --version))"

# ── 2. Install dependencies ──────────────────────────────────────────
echo ""
echo "▶ Installing Python dependencies..."
"$PYTHON" -m pip install -r "$REPO_DIR/requirements.txt" --quiet
echo "  Done."

# ── 3. Create logs directory ─────────────────────────────────────────
mkdir -p "$REPO_DIR/logs"

# ── 4. Quick smoke test ──────────────────────────────────────────────
echo ""
echo "▶ Running import smoke test..."
"$PYTHON" -c "
import sys
sys.path.insert(0, '$REPO_DIR')
from src.config import SEASON, CONDITION_PASS_RATE, HITRATE_THRESHOLD
from src.scoring_engine import PropScore
print('  All imports OK.')
print(f'  Season: {SEASON}')
print(f'  Thresholds: conditions>={CONDITION_PASS_RATE:.0%}, hit rate>={HITRATE_THRESHOLD:.0%}')
"

# ── 5. Install macOS LaunchAgent (runs at 9 AM daily) ────────────────
echo ""
echo "▶ Installing macOS LaunchAgent for daily 9 AM run..."

# Remove existing agent if present
if launchctl list | grep -q "$PLIST_LABEL" 2>/dev/null; then
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
fi

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>${REPO_DIR}/main.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>${RUN_HOUR}</integer>
        <key>Minute</key>
        <integer>${RUN_MINUTE}</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>${REPO_DIR}/logs/launchd_stdout.log</string>

    <key>StandardErrorPath</key>
    <string>${REPO_DIR}/logs/launchd_stderr.log</string>

    <key>RunAtLoad</key>
    <false/>

    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
PLIST

launchctl load "$PLIST_PATH"
echo "  LaunchAgent installed: $PLIST_PATH"

# ── 6. Summary ───────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}   Setup complete!${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo ""
echo "  The model will run automatically every morning at"
echo "  $(printf '%02d:%02d' $RUN_HOUR $RUN_MINUTE) AM and save results to logs/props_YYYY-MM-DD.txt"
echo ""
echo "  Useful commands:"
echo -e "  ${YELLOW}Run now manually:${NC}"
echo "    cd $REPO_DIR && python3 main.py"
echo ""
echo -e "  ${YELLOW}Run now (fast mode, skips playtypes):${NC}"
echo "    cd $REPO_DIR && python3 main.py --fast"
echo ""
echo -e "  ${YELLOW}Run for a specific date:${NC}"
echo "    cd $REPO_DIR && python3 main.py --date 2026-04-08"
echo ""
echo -e "  ${YELLOW}Trigger the LaunchAgent manually:${NC}"
echo "    launchctl start $PLIST_LABEL"
echo ""
echo -e "  ${YELLOW}Check LaunchAgent status:${NC}"
echo "    launchctl list | grep nba"
echo ""
echo -e "  ${YELLOW}Remove the LaunchAgent:${NC}"
echo "    launchctl unload $PLIST_PATH && rm $PLIST_PATH"
echo ""
echo "  Logs: $REPO_DIR/logs/"
echo ""
