# Syncing Between Private and Public Repos

This project uses a **one-way sync** model:

- `financial-agent` (private): personal instance with `accounts.yaml`, `config.yaml`,
  and `.claude/secrets.op` containing real data — never committed
- Public repo (default: `networth-agent`): open-source version, code only

The public repo name and URL are configured in **one place**: the `PUBLIC_REPO_URL`
variable at the top of `sync-to-public.sh`. Change it there if you rename the project.

## First-time setup

Add the public repo as a git remote so the sync script can use it directly:

```bash
git remote add public https://github.com/nightowlstudiollc/networth-agent.git
# Replace the URL above if you rename the repo
```

## Syncing changes

```bash
# Preview what will be copied (dry run, safe to run any time)
./sync-to-public.sh

# Apply and push
./sync-to-public.sh --push --message "feat: add launchd scheduling"
```

The script uses `rsync --delete`, so files removed from the private repo are
also removed from the public repo on the next sync.

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
2. Update `PUBLIC_REPO_URL` in `sync-to-public.sh`
3. Update the `public` remote: `git remote set-url public <new-url>`
4. Update the URL in `README.md`
