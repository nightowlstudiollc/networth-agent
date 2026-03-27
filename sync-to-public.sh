#!/usr/bin/env bash
# sync-to-public.sh
#
# One-way sync: private repo → public repo (code only, no personal config).
# Run this from the root of the private repo before publishing a change.
#
# ── Configuration ────────────────────────────────────────────────────────────
# The public repo URL. Change this if you rename the project.
# This value is also set as a git remote: git remote add public $PUBLIC_REPO_URL
PUBLIC_REPO_URL="https://github.com/nightowlstudiollc/networth-agent.git"
# ─────────────────────────────────────────────────────────────────────────────
#
# Usage:
#   ./sync-to-public.sh                        # dry run (shows what would change)
#   ./sync-to-public.sh --push                 # copy files and push
#   ./sync-to-public.sh --push --message "feat: add launchd scheduling"

set -euo pipefail

PRIVATE_ROOT="$(git rev-parse --show-toplevel)"
DRY_RUN=true
COMMIT_MESSAGE="sync: update from private repo"

for arg in "$@"; do
  case "$arg" in
    --push) DRY_RUN=false ;;
    --message=*) COMMIT_MESSAGE="${arg#*=}" ;;
  esac
done

# ── Files/dirs excluded from the public repo ─────────────────────────────────
EXCLUDE_PATTERNS=(
  "accounts.yaml"
  "config.yaml"
  ".mcp.json"
  ".claude/secrets.op"
  ".plaid_token.json"
  ".plaid_items.json"
  ".plaid_items_sandbox.json"
  ".plaid_proxy.pid"
  ".plaid_mcp_proxy.log"
  ".venv/"
  "__pycache__/"
  "*.pyc"
  "memory/"
  ".env"
)

EXCLUDE_ARGS=()
for pattern in "${EXCLUDE_PATTERNS[@]}"; do
  EXCLUDE_ARGS+=("--exclude=$pattern")
done

# ── Resolve remote URL ────────────────────────────────────────────────────────
# Prefer the 'public' git remote if set; fall back to PUBLIC_REPO_URL above.
REMOTE_URL=$(git remote get-url public 2>/dev/null || echo "$PUBLIC_REPO_URL")

TMP_DIR=$(mktemp -d)
trap "rm -rf $TMP_DIR" EXIT

echo "=== Syncing to public repo ==="
echo "  Source:  $PRIVATE_ROOT"
echo "  Target:  $REMOTE_URL"
echo "  Dry run: $DRY_RUN"
echo ""

echo "Cloning public repo..."
git clone --quiet "$REMOTE_URL" "$TMP_DIR/public"

echo "Syncing files..."
RSYNC_OPTS=(-av --delete "${EXCLUDE_ARGS[@]}" --exclude=".git/")
[[ "$DRY_RUN" == "true" ]] && RSYNC_OPTS+=(--dry-run)

rsync "${RSYNC_OPTS[@]}" "$PRIVATE_ROOT/" "$TMP_DIR/public/"

if [[ "$DRY_RUN" == "true" ]]; then
  echo ""
  echo "Dry run complete. Run with --push to apply."
  exit 0
fi

cd "$TMP_DIR/public"
git config user.name "$(git -C "$PRIVATE_ROOT" config user.name)"
git config user.email "$(git -C "$PRIVATE_ROOT" config user.email)"

if git diff --quiet && git diff --staged --quiet; then
  echo "No changes to sync."
  exit 0
fi

git add -A
git commit -m "$COMMIT_MESSAGE"
git push origin main
echo ""
echo "Sync complete → $REMOTE_URL"
