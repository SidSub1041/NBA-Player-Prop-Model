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
GRADE_LABEL="com.nba.prop.grader"
GRADE_PLIST_PATH="$HOME/Library/LaunchAgents/${GRADE_LABEL}.plist"
# run_daily.sh fires at each of these local times: it grades yesterday, builds
# today's board, exports the CSVs and pushes the data (the site then redeploys).
RUN_TIMES=("9:15" "12:00" "15:00" "18:00")

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

# ── 5. Install macOS LaunchAgent (runs run_daily.sh on game days) ───
echo ""
echo "▶ Installing macOS LaunchAgent (${RUN_TIMES[*]} daily)..."

# Remove existing agents (including the old separate grader agent)
for label in "$PLIST_LABEL" "$GRADE_LABEL"; do
    if launchctl list | grep -q "$label" 2>/dev/null; then
        launchctl unload "$HOME/Library/LaunchAgents/${label}.plist" 2>/dev/null || true
    fi
done
rm -f "$GRADE_PLIST_PATH"
chmod +x "$REPO_DIR/run_daily.sh"
mkdir -p "$HOME/Library/LaunchAgents"

CALENDAR=""
for t in "${RUN_TIMES[@]}"; do
    h="${t%%:*}"; m="${t##*:}"
    CALENDAR+="        <dict><key>Hour</key><integer>$((10#$h))</integer><key>Minute</key><integer>$((10#$m))</integer></dict>
"
done

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
        <string>/bin/bash</string>
        <string>${REPO_DIR}/run_daily.sh</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>PYTHON</key>
        <string>${PYTHON}</string>
    </dict>

    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>

    <key>StartCalendarInterval</key>
    <array>
${CALENDAR}    </array>

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
echo "  run_daily.sh will run at ${RUN_TIMES[*]} every day: it grades yesterday,"
echo "  builds today's board, and pushes the data so the site redeploys."
echo "  (If the Mac is asleep at a run time, launchd runs it on wake.)"
echo ""
echo "  Useful commands:"
echo -e "  ${YELLOW}Run the full game-day cycle now:${NC}"
echo "    cd $REPO_DIR && ./run_daily.sh"
echo ""
echo -e "  ${YELLOW}Run just the model manually:${NC}"
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
