# Syncing Between Private and Public Repos

This project uses a **one-way sync** model:

- `financial-agent` (private): personal instance, all development happens here
- `networth-agent` (public): open-source version, receives synced code via PRs

The public repo name lives in **one place**: `PUBLIC_REPO` at the top of
`sync-to-public.sh`. Change it there (and update the `public` git remote) if
you rename the project.

## Workflow

Changes never push directly to `main` on the public repo. Instead the sync
script creates a branch, commits, pushes, and opens a PR. Pre-push hooks run
normally; you merge when ready.

```
private main ──► sync-to-public.sh ──► public branch ──► PR ──► public main
```

## Setup

Requires the [GitHub CLI](https://cli.github.com) authenticated:

```bash
brew install gh
gh auth login
```

Add the public repo as a git remote (one-time):

```bash
git remote add public git@github.com:nightowlstudiollc/networth-agent.git
```

## Usage

```bash
# Preview what would change (safe, no writes)
./sync-to-public.sh

# Create branch, commit, push, open PR
./sync-to-public.sh --push --message "feat: add launchd scheduling"
```

The script creates a branch named `sync/YYYY-MM-DD-<slug>`, opens a PR against
`main`, and prints the PR URL. Merge it manually after review.

## What's personal vs. public

| File | Private | Public |
|------|---------|--------|
| `accounts.yaml` | ✓ local only (gitignored) | ✗ never |
| `config.yaml` | ✓ local only (gitignored) | ✗ never |
| `.mcp.json` | ✓ local only (gitignored) | ✗ never |
| `.claude/secrets.op` | ✓ local only (gitignored) | ✗ never |
| `accounts.example.yaml` | ✓ | ✓ |
| `config.example.yaml` | ✓ | ✓ |
| `.mcp.example.json` | ✓ | ✓ |
| `.claude/secrets.op.template` | ✓ | ✓ |
| All `*.py` scripts | ✓ | ✓ |
| `CLAUDE.md` | ✓ | ✓ |
| `README.md` | ✓ | ✓ |

## Renaming the project

1. Rename the repo on GitHub (Settings → Repository name)
2. Update `PUBLIC_REPO` in `sync-to-public.sh`
3. Update the `public` remote: `git remote set-url public git@github.com:<new-path>.git`
