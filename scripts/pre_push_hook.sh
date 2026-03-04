#!/bin/bash
# pre-push hook: Block [AF] artifact commits from being pushed to main/origin/main
#
# Install: cp scripts/pre_push_hook.sh .git/hooks/pre-push && chmod +x .git/hooks/pre-push
# Or run: python3 scripts/install_hooks.py
#
# This hook reads push details from stdin and checks if any [AF] commits
# would land on the main branch. If so, it blocks the push with instructions.

REMOTE="$1"
REMOTE_URL="$2"

while read local_ref local_sha remote_ref remote_sha; do
    # Only check pushes targeting main
    if [[ "$remote_ref" != *"main"* ]]; then
        continue
    fi

    # Handle new branch (no upstream SHA yet)
    if [ "$remote_sha" = "0000000000000000000000000000000000000000" ]; then
        RANGE="$local_sha"
    else
        RANGE="${remote_sha}..${local_sha}"
    fi

    # Count [AF] commits in the push range
    AF_COUNT=$(git log --oneline "$RANGE" 2>/dev/null | grep -c "^\w\+ \[AF\]" || true)

    if [ "$AF_COUNT" -gt 0 ]; then
        echo ""
        echo "╔══════════════════════════════════════════════════════════╗"
        echo "║  ERROR: [AF] Artifact Commits Blocked from main branch   ║"
        echo "╚══════════════════════════════════════════════════════════╝"
        echo ""
        echo "  Found $AF_COUNT [AF] mission artifact commit(s) that would"
        echo "  be pushed to $remote_ref."
        echo ""
        echo "  [AF] commits belong on 'af-missions/checkpoints' branch,"
        echo "  NOT on main."
        echo ""
        echo "  To fix:"
        echo "    python3 scripts/clean_push.py           # Audit what's ahead"
        echo "    python3 scripts/clean_push.py --execute # Squash + push"
        echo ""
        echo "  To bypass (NOT recommended):"
        echo "    git push --no-verify origin main"
        echo ""
        exit 1
    fi
done

exit 0
