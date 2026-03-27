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

# Start Plaid MCP proxy if credentials are available
if [[ -n "${PLAID_CLIENT_ID:-}" ]] && [[ -n "${PLAID_SECRET:-}" ]]; then
  PYTHON="${GIT_ROOT}/.venv/bin/python"
  PROXY_SCRIPT="${GIT_ROOT}/plaid_mcp_proxy.py"
  PID_FILE="${GIT_ROOT}/.plaid_proxy.pid"

  if [[ -x "${PYTHON}" ]] && [[ -f "${PROXY_SCRIPT}" ]]; then
    # Check if proxy is already running
    proxy_running=false
    if [[ -f "${PID_FILE}" ]]; then
      old_pid=$(cat "${PID_FILE}" 2>/dev/null)
      if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" 2>/dev/null; then
        proxy_running=true
        debug_log "Plaid MCP proxy already running (PID ${old_pid})"
      fi
    fi

    # Start proxy if not running
    if [[ "${proxy_running}" == "false" ]]; then
      debug_log "Starting Plaid MCP proxy..."
      nohup "${PYTHON}" "${PROXY_SCRIPT}" >/dev/null 2>&1 &
      # Wait briefly for proxy to start
      sleep 1
      if [[ -f "${PID_FILE}" ]]; then
        new_pid=$(cat "${PID_FILE}" 2>/dev/null)
        debug_log "Plaid MCP proxy started (PID ${new_pid})"
      else
        log_warn "Plaid MCP proxy may have failed to start"
      fi
    fi
  fi
fi
