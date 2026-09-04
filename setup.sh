#!/usr/bin/env bash
# Setup script: restore config files from backup or create initial backup.
# Run after cloning or after a destructive git operation.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_BACKUP_DIR="${HOME}/.config/financial-agent"

CONFIG_FILES=(
  ".mcp.json"
  "accounts.yaml"
  "config.yaml"
)
SECRET_FILES=(
  ".claude/secrets.op:secrets.op"
)

restore_from_backup() {
  local restored=0
  for file in "${CONFIG_FILES[@]}"; do
    src="${CONFIG_BACKUP_DIR}/${file}"
    dst="${REPO_DIR}/${file}"
    if [[ ! -f "${dst}" ]] && [[ -f "${src}" ]]; then
      cp "${src}" "${dst}"
      echo "  Restored ${file}"
      restored=$((restored + 1))
    elif [[ -f "${dst}" ]]; then
      echo "  ${file} already exists"
    else
      echo "  ${file} missing (no backup available)"
    fi
  done
  for entry in "${SECRET_FILES[@]}"; do
    repo_path="${entry%%:*}"
    backup_name="${entry##*:}"
    src="${CONFIG_BACKUP_DIR}/${backup_name}"
    dst="${REPO_DIR}/${repo_path}"
    if [[ ! -f "${dst}" ]] && [[ -f "${src}" ]]; then
      cp "${src}" "${dst}"
      chmod 400 "${dst}"
      echo "  Restored ${repo_path}"
      restored=$((restored + 1))
    elif [[ -f "${dst}" ]]; then
      echo "  ${repo_path} already exists"
    else
      echo "  ${repo_path} missing (no backup available)"
    fi
  done
  echo ""
  if [[ "${restored}" -gt 0 ]]; then
    echo "Restored ${restored} file(s) from backup."
  else
    echo "All config files already in place."
  fi
}

create_backup() {
  mkdir -p "${CONFIG_BACKUP_DIR}"
  local backed=0
  for file in "${CONFIG_FILES[@]}"; do
    src="${REPO_DIR}/${file}"
    dst="${CONFIG_BACKUP_DIR}/${file}"
    if [[ -f "${src}" ]]; then
      cp "${src}" "${dst}"
      echo "  Backed up ${file}"
      backed=$((backed + 1))
    else
      echo "  ${file} not found in repo (skipped)"
    fi
  done
  for entry in "${SECRET_FILES[@]}"; do
    repo_path="${entry%%:*}"
    backup_name="${entry##*:}"
    src="${REPO_DIR}/${repo_path}"
    dst="${CONFIG_BACKUP_DIR}/${backup_name}"
    if [[ -f "${src}" ]]; then
      cp "${src}" "${dst}"
      chmod 400 "${dst}"
      echo "  Backed up ${repo_path}"
      backed=$((backed + 1))
    else
      echo "  ${repo_path} not found in repo (skipped)"
    fi
  done
  echo ""
  echo "Backed up ${backed} file(s) to ${CONFIG_BACKUP_DIR}"
}

echo "Financial Agent Config Setup"
echo "============================"
echo ""

if [[ -d "${CONFIG_BACKUP_DIR}" ]]; then
  echo "Backup directory found: ${CONFIG_BACKUP_DIR}"
  echo "Restoring config files..."
  echo ""
  restore_from_backup
else
  echo "No backup directory found."
  echo ""
  # Check if config files exist in repo to create initial backup
  has_config=false
  for file in "${CONFIG_FILES[@]}"; do
    [[ -f "${REPO_DIR}/${file}" ]] && has_config=true
  done

  if ${has_config}; then
    echo "Creating initial backup from existing config files..."
    echo ""
    create_backup
  else
    echo "No config files found. Copy from templates first:"
    echo "  cp .mcp.example.json .mcp.json"
    echo "  cp accounts.example.yaml accounts.yaml"
    echo "  cp config.example.yaml config.yaml"
    echo ""
    echo "Then edit each file with your real values and re-run this script."
  fi
fi
