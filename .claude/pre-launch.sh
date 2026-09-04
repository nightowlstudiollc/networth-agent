#!/usr/bin/env bash
# Project pre-launch hook for financial-agent
# Called by claude-with-identity wrapper after secrets are loaded
#
# Expected environment from parent:
#   GIT_ROOT - Project root directory (set by claude-with-identity)
#   PLAID_CLIENT_ID, PLAID_SECRET - Plaid credentials (from 1Password)
#   debug_log(), log_warn() - Logging functions (from claude-with-identity)

# Require GIT_ROOT from parent script (exit early if not sourced correctly)
: "${GIT_ROOT:?pre-launch.sh must be sourced by claude-with-identity}"

# --- Config file backup & restore ---
CONFIG_BACKUP_DIR="${HOME}/.config/financial-agent"
CONFIG_FILES=(
  ".mcp.json"
  "accounts.yaml"
  "config.yaml"
)
SECRET_FILES=(
  ".claude/secrets.op:secrets.op"
)

if [[ -d "${CONFIG_BACKUP_DIR}" ]]; then
  # Restore any missing config files from backup
  for file in "${CONFIG_FILES[@]}"; do
    src="${CONFIG_BACKUP_DIR}/${file}"
    dst="${GIT_ROOT}/${file}"
    if [[ ! -f "${dst}" ]] && [[ -f "${src}" ]]; then
      cp "${src}" "${dst}"
      log_warn "Restored missing ${file} from backup"
    fi
  done
  for entry in "${SECRET_FILES[@]}"; do
    repo_path="${entry%%:*}"
    backup_name="${entry##*:}"
    src="${CONFIG_BACKUP_DIR}/${backup_name}"
    dst="${GIT_ROOT}/${repo_path}"
    if [[ ! -f "${dst}" ]] && [[ -f "${src}" ]]; then
      cp "${src}" "${dst}"
      chmod 400 "${dst}"
      log_warn "Restored missing ${repo_path} from backup"
    fi
  done

  # Sync current config files to backup (keep backup fresh)
  for file in "${CONFIG_FILES[@]}"; do
    src="${GIT_ROOT}/${file}"
    dst="${CONFIG_BACKUP_DIR}/${file}"
    if [[ -f "${src}" ]]; then
      if ! cmp -s "${src}" "${dst}" 2>/dev/null; then
        cp "${src}" "${dst}"
        debug_log "Backed up ${file}"
      fi
    fi
  done
  for entry in "${SECRET_FILES[@]}"; do
    repo_path="${entry%%:*}"
    backup_name="${entry##*:}"
    src="${GIT_ROOT}/${repo_path}"
    dst="${CONFIG_BACKUP_DIR}/${backup_name}"
    if [[ -f "${src}" ]]; then
      if ! cmp -s "${src}" "${dst}" 2>/dev/null; then
        cp "${src}" "${dst}"
        chmod 400 "${dst}"
        debug_log "Backed up ${repo_path}"
      fi
    fi
  done
else
  log_warn "Config backup dir missing: ${CONFIG_BACKUP_DIR}"
  log_warn "Run setup.sh to initialize backups"
fi

# Plaid MCP proxy retired 2026-07-01. The plaid-dashboard MCP server was removed
# (nothing in the automation used it, and Plaid's upstream OAuth began returning
# 404). plaid_mcp_proxy.py remains on disk for manual use if the integration is
# ever revived. To re-enable, restore this block and re-add plaid-dashboard to
# .mcp.json / .mcp.example.json.
