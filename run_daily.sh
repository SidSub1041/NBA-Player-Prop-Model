#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_daily.sh — game-day runner for a machine with a normal internet
# connection (your Mac). stats.nba.com blocks cloud runners, so this is the
# reliable way to produce picks:
#
#   1. sync with the repo (recovering from any interrupted earlier run)
#   2. grade yesterday's picks
#   3. run today's board (exits quietly when there are no games)
#   4. refresh the CSV fact tables
#   5. commit + push the data — GitHub Actions then redeploys the site
#
# Installed as a LaunchAgent by ./setup_mac.sh; safe to run by hand:
#   ./run_daily.sh
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PYTHON="${PYTHON:-python3}"
STAMP="$(date -u +'%Y-%m-%d %H:%M UTC')"
echo "── run_daily.sh $STAMP ──"

# ── 1. Sync. Clear any rebase/merge left behind by an interrupted run, then
#       pull with autostash so leftover local edits can't block the pull.
git rebase --abort >/dev/null 2>&1 || true
git merge --abort  >/dev/null 2>&1 || true
git checkout --quiet -- web/public/data logs >/dev/null 2>&1 || true   # drop leftovers; regenerated below
git pull --rebase --autostash --quiet origin main \
    || echo "warning: git pull failed, continuing with local state"

# ── 2-4. Produce the data (each step is independent; failures are logged).
"$PYTHON" grade.py        || echo "grade.py: nothing to grade / failed"
"$PYTHON" main.py --fast  || echo "main.py: no games today / stats.nba.com unreachable / failed"
"$PYTHON" export_csv.py   || echo "export_csv.py: failed"

# ── 5. Commit and push. Local data is regenerated from source every run, so
#       on a conflict with the remote the local version wins.
git add web/public/data logs
if git diff --cached --quiet; then
    echo "No data changes to push."
    exit 0
fi

if ! git commit --quiet -m "chore: update props data $STAMP (local runner)"; then
    echo "warning: git commit failed (is git user.name / user.email configured?)"
    git reset --quiet
    exit 1
fi

if git push --quiet origin main; then
    echo "Pushed — GitHub Pages will redeploy."
    exit 0
fi

echo "push rejected — rebasing onto origin/main (local data wins) and retrying"
if git fetch --quiet origin main && git rebase -X theirs origin/main >/dev/null 2>&1; then
    if git push --quiet origin main; then
        echo "Pushed after rebase — GitHub Pages will redeploy."
        exit 0
    fi
else
    git rebase --abort >/dev/null 2>&1 || true
fi
echo "warning: push failed; the commit stays local and will be retried next run"
exit 1
