#!/usr/bin/env bash
# sync-to-public.sh
#
# One-way sync: private repo → public repo via branch → PR workflow.
# Syncs only files tracked by git (HEAD), so gitignored files are
# structurally excluded — no explicit exclude list required.
#
# ── Configuration ────────────────────────────────────────────────────
# The public repo (owner/name). Change here and update the 'public' git remote
# if you rename the project.
PUBLIC_REPO="nightowlstudiollc/networth-agent"
# ─────────────────────────────────────────────────────────────────────────────
#
# Usage:
#   ./sync-to-public.sh                        # dry run — shows what would change
#   ./sync-to-public.sh --push                 # create branch, commit, push, open PR
#   ./sync-to-public.sh --push --message "feat: add launchd scheduling"
#
# Requirements:
#   - gh CLI (https://cli.github.com) authenticated
#   - 'public' git remote set: git remote add public git@github.com:${PUBLIC_REPO}.git

set -euo pipefail

PRIVATE_ROOT="$(git rev-parse --show-toplevel)"
DRY_RUN=true
COMMIT_MESSAGE="sync: update from private repo"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --push) DRY_RUN=false ;;
    --message=*) COMMIT_MESSAGE="${1#*=}" ;;
    --message)
      COMMIT_MESSAGE="${2:?--message requires a value}"
      shift
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
  shift
done

# ── Sanity checks ─────────────────────────────────────────────────────────────
if ! command -v gh &>/dev/null; then
  echo "ERROR: gh CLI not found. Install from https://cli.github.com"
  exit 1
fi

if ! gh auth status &>/dev/null; then
  echo "ERROR: gh CLI not authenticated. Run: gh auth login"
  exit 1
fi

porcelain_output=$(git -C "${PRIVATE_ROOT}" status --porcelain)
if [[ -n "${porcelain_output}" ]]; then
  echo "ERROR: Uncommitted changes in private repo. Commit or stash before syncing."
  git -C "${PRIVATE_ROOT}" status --short
  exit 1
fi

# ── Resolve public repo remote URL ───────────────────────────────────────────
REMOTE_URL=$(git -C "${PRIVATE_ROOT}" remote get-url public 2>/dev/null \
  || echo "git@github.com:${PUBLIC_REPO}.git")

# ── Stage: extract tracked files from git into a temp dir ────────────────────
# git archive produces exactly what git tracks at HEAD — gitignored files
# are structurally absent; no exclusion list needed or used.
stage() {
  local dest="$1"
  mkdir -p "${dest}"
  git -C "${PRIVATE_ROOT}" archive HEAD | tar -x -C "${dest}"
}

# ── Dry run ───────────────────────────────────────────────────────────────────
if [[ "${DRY_RUN}" == "true" ]]; then
  TMP_DIR=$(mktemp -d)
  trap 'rm -rf "${TMP_DIR}"' EXIT

  echo "=== Dry run — files that would be in the public repo ==="
  echo "  Source:  ${PRIVATE_ROOT} (HEAD)"
  echo "  Target:  ${REMOTE_URL}"
  echo ""

  git clone --quiet "${REMOTE_URL}" "${TMP_DIR}/public"
  stage "${TMP_DIR}/stage"

  # Show diff between what's in public and what would be synced
  diff_output=$(diff -rq \
    --exclude=".git" \
    "${TMP_DIR}/public" "${TMP_DIR}/stage" 2>/dev/null || true)

  if [[ -z "${diff_output}" ]]; then
    echo "  No changes — public repo is already up to date."
  else
    echo "${diff_output}" | sed \
      -e "s|Only in ${TMP_DIR}/stage|  new:    |" \
      -e "s|Only in ${TMP_DIR}/public|  removed:|" \
      -e "s|Files ${TMP_DIR}/stage/\\(.*\\) and.*differ|  changed: \\1|"
  fi

  echo ""
  echo "Run with --push to create a branch and open a PR."
  exit 0
fi

# ── Push workflow ─────────────────────────────────────────────────────────────
TMP_DIR=$(mktemp -d)
trap 'rm -rf "${TMP_DIR}"' EXIT

SLUG=$(echo "${COMMIT_MESSAGE}" \
  | sed 's/^[a-z]*: //' \
  | tr '[:upper:]' '[:lower:]' \
  | tr -cs 'a-z0-9' '-' \
  | sed 's/^-//;s/-$//' \
  | cut -c1-40)
BRANCH="sync/$(date +%Y-%m-%d)-${SLUG}"

echo "=== Syncing to public repo ==="
echo "  Source:  ${PRIVATE_ROOT} (HEAD)"
echo "  Target:  ${REMOTE_URL}"
echo "  Branch:  ${BRANCH}"
echo ""

echo "Cloning public repo..."
git clone --quiet "${REMOTE_URL}" "${TMP_DIR}/public"

cd "${TMP_DIR}/public"
git config user.name "$(git -C "${PRIVATE_ROOT}" config user.name || true)"
git config user.email "$(git -C "${PRIVATE_ROOT}" config user.email || true)"
git checkout -b "${BRANCH}"

# Replace public content with staged private content
echo "Staging changes..."
find . -not -path './.git/*' -not -name '.git' -delete 2>/dev/null || true
stage "${TMP_DIR}/public"

if git diff --quiet && git diff --staged --quiet; then
  echo "No changes — public repo is already up to date."
  exit 0
fi

git add -A

echo ""
echo "Changes:"
git diff --staged --stat
echo ""

git commit -m "${COMMIT_MESSAGE}"
git push origin "${BRANCH}"

echo ""
echo "Opening PR..."
PR_URL=$(gh pr create \
  --repo "${PUBLIC_REPO}" \
  --head "${BRANCH}" \
  --base "main" \
  --title "${COMMIT_MESSAGE}" \
  --body "Automated sync from \`nightowlstudiollc/financial-agent\`." \
  --label "sync" 2>/dev/null \
  || gh pr create \
    --repo "${PUBLIC_REPO}" \
    --head "${BRANCH}" \
    --base "main" \
    --title "${COMMIT_MESSAGE}" \
    --body "Automated sync from \`nightowlstudiollc/financial-agent\`.")

echo ""
echo "PR ready: ${PR_URL}"
echo "Review and merge when ready."
