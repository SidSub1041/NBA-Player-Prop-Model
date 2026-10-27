#!/usr/bin/env bash
# commit_data.sh "<commit message>" <path>... — stage the given paths, commit
# when anything changed, then rebase onto origin/main and push, up to three
# times. A conflict on a day's price cache or line history (a snapshot run and
# a board run finishing together) is resolved with src/logmerge.py; any other
# conflicted file keeps this run's version. If a merge fails, nothing is pushed:
# a file with conflict markers must never reach main.
set -u
msg="$1"; shift
PY="${PYTHON:-$(command -v python3 || command -v python)}"
git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
for p in "$@"; do git add -- $p 2>/dev/null || true; done
if git diff --cached --quiet; then
  echo "No data changes to commit."
  exit 0
fi
git commit -q -m "$msg"

resolve_conflicts() {
  local f
  for f in $(git diff --name-only --diff-filter=U); do
    case "$f" in
      logs/vegas_*.json|logs/lines_*.json)
        "$PY" -m src.logmerge "$f" || { echo "merge of $f failed"; return 1; } ;;
      *)
        git checkout --theirs -- "$f" || return 1 ;;   # during a rebase, "theirs" is this run's commit
    esac
    if grep -q '^<<<<<<< \|^>>>>>>> ' "$f"; then echo "conflict markers left in $f"; return 1; fi
    git add -- "$f"
  done
}

for attempt in 1 2 3; do
  if git pull --rebase -q origin main; then
    git push -q origin HEAD:main && { echo "Pushed."; exit 0; }
  elif resolve_conflicts && GIT_EDITOR=true git rebase --continue; then
    git push -q origin HEAD:main && { echo "Pushed after merging the day's logs."; exit 0; }
  else
    git rebase --abort >/dev/null 2>&1 || true
  fi
  sleep $((attempt * 10))
done
echo "Could not push after 3 attempts."
exit 1
