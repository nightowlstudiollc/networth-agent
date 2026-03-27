# Plaid MCP Token Refresh Proxy - Design Document

**Date:** 2026-01-30
**Status:** Approved

## Problem

The official Plaid Dashboard MCP (`api.dashboard.plaid.com/mcp/sse`) uses OAuth tokens that expire after ~30 minutes. When a token expires mid-session, the MCP connection fails and requires restarting Claude Code to refresh.

## Solution

A local proxy server that:

- Sits between Claude Code and Plaid's MCP endpoint
- Manages token refresh transparently
- Keeps the Claude connection alive while reconnecting to Plaid with fresh tokens

## Architecture

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│   Claude Code   │────▶│   plaid_mcp_proxy    │────▶│  api.dashboard.     │
│                 │ SSE │   (localhost:8787)   │ SSE │  plaid.com/mcp/sse  │
└─────────────────┘     └──────────────────────┘     └─────────────────────┘
                               │
                               ▼
                        ┌──────────────┐
                        │ plaid_token  │
                        │ (reused)     │
                        └──────────────┘
```

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Language | Python | Matches existing codebase, reuses `plaid_token.py` |
| Token refresh | Hybrid (pre-emptive + reactive) | Pre-emptive avoids failures, reactive handles edge cases |
| Lifecycle | Auto-start via pre-launch hook | Zero manual intervention |
| Configuration | Environment variables | Matches existing setup, no new config files |

## Token Refresh Strategy

### Pre-emptive (Primary)

- Track token expiry time
- Refresh 5 minutes before expiration
- Reconnect to Plaid during idle moments
- Keep Claude connection alive throughout

### Reactive (Fallback)

- On 401 from Plaid: immediate refresh and reconnect
- On connection drop: refresh token, reconnect
- Retry with exponential backoff on failures

## Components

### plaid_mcp_proxy.py (New)

Async HTTP server using `aiohttp`:

- SSE server for Claude Code connections
- SSE client for Plaid MCP connections
- Token management via imported `plaid_token` module
- Health endpoint at `/health`
- Graceful shutdown on SIGTERM/SIGINT
- PID file for lifecycle management

### pre-launch.sh (Modified)

- Start proxy in background
- Wait for health check
- Export PID for cleanup
- Kill stale processes on startup

### .mcp.json (Modified)

Point to local proxy instead of Plaid directly:

```json
{
  "plaid-dashboard": {
    "type": "sse",
    "url": "http://localhost:8787/mcp/sse"
  }
}
```

## Error Handling

| Scenario | Action |
|----------|--------|
| Token refresh fails | Retry with backoff (1s, 2s, 4s...) |
| Plaid connection drops | Refresh token, reconnect |
| Plaid returns 401 | Immediate refresh, retry request |
| Plaid is down | Keep Claude connection, return errors gracefully |

## File Changes

### New Files

- `plaid_mcp_proxy.py` - Proxy server (~150-200 lines)
- `docs/PLAID_PROXY.md` - User documentation

### Modified Files

- `.mcp.json` - Point to localhost:8787
- `pre-launch.sh` - Start/manage proxy
- `requirements.txt` - Add `aiohttp`
- `CLAUDE.md` - Document the proxy

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PLAID_CLIENT_ID` | Yes | - | Plaid API client ID |
| `PLAID_SECRET` | Yes | - | Plaid API secret |
| `PLAID_PROXY_PORT` | No | 8787 | Port for proxy server |
| `PLAID_PROXY_LOG` | No | ~/.plaid_mcp_proxy.log | Log file path |
| `PLAID_PROXY_DEBUG` | No | 0 | Enable debug logging |

## Success Criteria

1. Token refreshes transparently without Claude Code restart
2. No dropped requests during token refresh
3. Proper cleanup on exit (no orphaned processes)
4. Clear logging for debugging
5. Documentation sufficient for community use
