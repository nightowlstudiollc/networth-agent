# Plaid MCP Token Refresh Proxy - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a local proxy that handles Plaid OAuth token refresh transparently, eliminating the need to restart Claude Code when tokens expire.

**Architecture:** Python async server using aiohttp. Accepts SSE connections from Claude Code, proxies to Plaid's MCP endpoint, manages token lifecycle with pre-emptive refresh (5 min before expiry) and reactive fallback (on 401).

**Tech Stack:** Python 3.11+, aiohttp, existing plaid_token.py module

---

## Task 1: Add aiohttp Dependency

**Files:**

- Modify: `requirements.txt`

**Step 1: Add aiohttp to requirements**

```txt
coinbase-advanced-py>=1.0.0
flask>=3.0.0
plaid-python>=22.0.0
python-dotenv>=1.0.0
requests>=2.28.0
aiohttp>=3.9.0
```

**Step 2: Install the dependency**

Run: `source .venv/bin/activate && uv pip install -r requirements.txt`
Expected: Successfully installed aiohttp

**Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add aiohttp for MCP proxy"
```

---

## Task 2: Refactor plaid_token.py for Import

**Files:**

- Modify: `plaid_token.py`

The current `plaid_token.py` works but needs minor adjustments to expose token metadata (expiry time) for the proxy's pre-emptive refresh.

**Step 1: Add get_token_with_expiry function**

Add this function after `get_valid_token()`:

```python
def get_token_with_expiry():
    """Get valid token with expiry timestamp for proxy use.

    Returns:
        tuple: (access_token, expires_at) where expires_at is Unix timestamp
    """
    # Try to load cached token
    if TOKEN_FILE.exists():
        try:
            cached = json.loads(TOKEN_FILE.read_text())
            if cached.get("expires_at", 0) > time.time():
                return cached["access_token"], cached["expires_at"]
            # Try refresh
            if cached.get("refresh_token"):
                try:
                    new_token = refresh_token(cached["refresh_token"])
                    write_token_file(new_token)
                    return new_token["access_token"], new_token["expires_at"]
                except Exception as e:
                    print(f"Token refresh failed: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Failed to load cached token: {e}", file=sys.stderr)

    # Fetch new token
    new_token = fetch_new_token()
    write_token_file(new_token)
    return new_token["access_token"], new_token["expires_at"]
```

**Step 2: Verify existing tests still pass (if any)**

Run: `python -c "from plaid_token import get_token_with_expiry; print('Import OK')"`
Expected: Import OK

**Step 3: Commit**

```bash
git add plaid_token.py
git commit -m "feat(plaid_token): add get_token_with_expiry for proxy"
```

---

## Task 3: Create Proxy Core - Basic HTTP Server

**Files:**

- Create: `plaid_mcp_proxy.py`

**Step 1: Create the basic server skeleton**

```python
#!/usr/bin/env python3
"""Plaid MCP Token Refresh Proxy.

A local proxy that sits between Claude Code and Plaid's MCP endpoint,
handling OAuth token refresh transparently.
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from aiohttp import web

# Configuration
PORT = int(os.getenv("PLAID_PROXY_PORT", "8787"))
LOG_FILE = os.getenv("PLAID_PROXY_LOG", str(Path.home() / ".plaid_mcp_proxy.log"))
DEBUG = os.getenv("PLAID_PROXY_DEBUG", "0") == "1"
PID_FILE = Path(__file__).parent / ".plaid_proxy.pid"

# Plaid MCP endpoint
PLAID_MCP_URL = "https://api.dashboard.plaid.com/mcp/sse"

# Setup logging
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler() if DEBUG else logging.NullHandler(),
    ],
)
logger = logging.getLogger(__name__)


async def health_handler(request: web.Request) -> web.Response:
    """Health check endpoint."""
    return web.json_response({"status": "ok"})


def create_app() -> web.Application:
    """Create the aiohttp application."""
    app = web.Application()
    app.router.add_get("/health", health_handler)
    return app


def write_pid_file():
    """Write current PID to file."""
    PID_FILE.write_text(str(os.getpid()))
    logger.info(f"PID {os.getpid()} written to {PID_FILE}")


def cleanup_pid_file():
    """Remove PID file on shutdown."""
    if PID_FILE.exists():
        PID_FILE.unlink()
        logger.info("PID file removed")


async def run_server():
    """Run the proxy server."""
    write_pid_file()

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "localhost", PORT)
    await site.start()

    logger.info(f"Plaid MCP Proxy running on http://localhost:{PORT}")

    # Wait forever (until signal)
    stop_event = asyncio.Event()

    def signal_handler():
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    await stop_event.wait()

    # Cleanup
    await runner.cleanup()
    cleanup_pid_file()
    logger.info("Proxy shut down cleanly")


def main():
    """Entry point."""
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass
    finally:
        cleanup_pid_file()


if __name__ == "__main__":
    main()
```

**Step 2: Test the basic server**

Run: `timeout 3 python plaid_mcp_proxy.py &; sleep 1; curl -s http://localhost:8787/health; kill %1 2>/dev/null`
Expected: `{"status": "ok"}`

**Step 3: Commit**

```bash
git add plaid_mcp_proxy.py
git commit -m "feat(proxy): add basic HTTP server skeleton"
```

---

## Task 4: Add Token Management to Proxy

**Files:**

- Modify: `plaid_mcp_proxy.py`

**Step 1: Import plaid_token and add token state**

Add after the logging setup:

```python
# Import token management
from plaid_token import get_token_with_expiry

# Token state
class TokenManager:
    """Manage OAuth token lifecycle."""

    def __init__(self):
        self.access_token: str | None = None
        self.expires_at: float = 0
        self._refresh_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        """Get current valid token, refreshing if needed."""
        async with self._lock:
            # Check if token needs refresh (5 min buffer)
            if self.access_token and self.expires_at > (asyncio.get_event_loop().time() + 300):
                return self.access_token

            # Refresh token
            logger.info("Refreshing Plaid token...")
            try:
                # Run sync function in executor
                loop = asyncio.get_event_loop()
                self.access_token, self.expires_at = await loop.run_in_executor(
                    None, get_token_with_expiry
                )
                logger.info(f"Token refreshed, expires in {int(self.expires_at - asyncio.get_event_loop().time())}s")
                return self.access_token
            except Exception as e:
                logger.error(f"Token refresh failed: {e}")
                raise

    async def start_refresh_loop(self):
        """Start background token refresh loop."""
        async def refresh_loop():
            while True:
                try:
                    # Sleep until 5 minutes before expiry
                    now = asyncio.get_event_loop().time()
                    sleep_time = max(60, self.expires_at - now - 300)
                    logger.debug(f"Next token refresh in {int(sleep_time)}s")
                    await asyncio.sleep(sleep_time)
                    await self.get_token()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Background refresh failed: {e}")
                    await asyncio.sleep(60)  # Retry in 1 min

        self._refresh_task = asyncio.create_task(refresh_loop())

    async def stop(self):
        """Stop background refresh."""
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass


# Global token manager
token_manager = TokenManager()
```

**Step 2: Update health endpoint to show token status**

Replace health_handler:

```python
async def health_handler(request: web.Request) -> web.Response:
    """Health check endpoint."""
    import time
    expires_in = int(token_manager.expires_at - time.time()) if token_manager.expires_at else 0
    return web.json_response({
        "status": "ok",
        "token_expires_in": max(0, expires_in),
        "has_token": token_manager.access_token is not None,
    })
```

**Step 3: Initialize token on startup**

Update run_server() to initialize token:

```python
async def run_server():
    """Run the proxy server."""
    write_pid_file()

    # Get initial token
    logger.info("Fetching initial token...")
    await token_manager.get_token()
    await token_manager.start_refresh_loop()

    app = create_app()
    # ... rest of function
```

And update cleanup section:

```python
    # Cleanup
    await token_manager.stop()
    await runner.cleanup()
    cleanup_pid_file()
```

**Step 4: Test token management**

Run: `timeout 5 python plaid_mcp_proxy.py &; sleep 2; curl -s http://localhost:8787/health; kill %1 2>/dev/null`
Expected: `{"status": "ok", "token_expires_in": <number>, "has_token": true}`

**Step 5: Commit**

```bash
git add plaid_mcp_proxy.py
git commit -m "feat(proxy): add token management with pre-emptive refresh"
```

---

## Task 5: Add SSE Proxy Handler

**Files:**

- Modify: `plaid_mcp_proxy.py`

**Step 1: Add aiohttp ClientSession import**

Add to imports:

```python
import aiohttp
from aiohttp import web, ClientSession, ClientTimeout
```

**Step 2: Add SSE proxy handler**

Add after TokenManager class:

```python
async def sse_proxy_handler(request: web.Request) -> web.StreamResponse:
    """Proxy SSE connection to Plaid MCP."""
    logger.info("New SSE connection from client")

    # Prepare streaming response to client
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)

    async def connect_to_plaid() -> aiohttp.ClientResponse:
        """Establish connection to Plaid with current token."""
        token = await token_manager.get_token()
        timeout = ClientTimeout(total=None, sock_read=None)
        session = ClientSession(timeout=timeout)

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "text/event-stream",
        }

        resp = await session.get(PLAID_MCP_URL, headers=headers)
        if resp.status == 401:
            await session.close()
            # Force token refresh and retry
            logger.warning("Got 401, forcing token refresh")
            token_manager.access_token = None
            token_manager.expires_at = 0
            token = await token_manager.get_token()
            session = ClientSession(timeout=timeout)
            headers["Authorization"] = f"Bearer {token}"
            resp = await session.get(PLAID_MCP_URL, headers=headers)

        if resp.status != 200:
            await session.close()
            raise Exception(f"Plaid returned {resp.status}: {await resp.text()}")

        return session, resp

    session = None
    try:
        session, plaid_resp = await connect_to_plaid()
        logger.info("Connected to Plaid MCP")

        # Stream events from Plaid to client
        async for chunk in plaid_resp.content.iter_any():
            await response.write(chunk)

    except asyncio.CancelledError:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"SSE proxy error: {e}")
        # Try to send error to client
        try:
            error_event = f"event: error\ndata: {str(e)}\n\n"
            await response.write(error_event.encode())
        except Exception:
            pass
    finally:
        if session:
            await session.close()
        logger.info("SSE connection closed")

    return response
```

**Step 3: Add POST handler for MCP requests**

MCP uses POST for client-to-server messages. Add:

```python
async def mcp_post_handler(request: web.Request) -> web.Response:
    """Proxy POST requests to Plaid MCP."""
    logger.debug("Proxying POST request to Plaid")

    token = await token_manager.get_token()
    body = await request.read()

    async with ClientSession() as session:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": request.content_type or "application/json",
        }

        # Plaid MCP POST endpoint (same base, no /sse)
        post_url = PLAID_MCP_URL.replace("/sse", "")

        async with session.post(post_url, headers=headers, data=body) as resp:
            if resp.status == 401:
                # Force refresh and retry
                logger.warning("Got 401 on POST, forcing token refresh")
                token_manager.access_token = None
                token_manager.expires_at = 0
                token = await token_manager.get_token()
                headers["Authorization"] = f"Bearer {token}"
                async with session.post(post_url, headers=headers, data=body) as retry_resp:
                    return web.Response(
                        status=retry_resp.status,
                        body=await retry_resp.read(),
                        content_type=retry_resp.content_type,
                    )

            return web.Response(
                status=resp.status,
                body=await resp.read(),
                content_type=resp.content_type,
            )
```

**Step 4: Register routes**

Update create_app():

```python
def create_app() -> web.Application:
    """Create the aiohttp application."""
    app = web.Application()
    app.router.add_get("/health", health_handler)
    app.router.add_get("/mcp/sse", sse_proxy_handler)
    app.router.add_post("/mcp/sse", mcp_post_handler)
    return app
```

**Step 5: Commit**

```bash
git add plaid_mcp_proxy.py
git commit -m "feat(proxy): add SSE and POST proxy handlers"
```

---

## Task 6: Update pre-launch.sh to Start Proxy

**Files:**

- Modify: `.claude/pre-launch.sh`

**Step 1: Replace token fetching with proxy startup**

Replace the entire file with:

```bash
#!/usr/bin/env bash
# Project pre-launch hook for financial-agent
# Called by claude-with-identity wrapper after secrets are loaded
#
# Expected environment from parent:
#   GIT_ROOT - Project root directory (set by claude-with-identity)
#   PLAID_CLIENT_ID, PLAID_SECRET - Plaid credentials (from 1Password)
#   debug_log(), warn_log() - Logging functions (from claude-with-identity)

# Require GIT_ROOT from parent script (exit early if not sourced correctly)
: "${GIT_ROOT:?pre-launch.sh must be sourced by claude-with-identity}"

# Start Plaid MCP Proxy if credentials are available
if [[ -n "${PLAID_CLIENT_ID:-}" ]] && [[ -n "${PLAID_SECRET:-}" ]]; then
  PYTHON="${GIT_ROOT}/.venv/bin/python"
  PROXY_SCRIPT="${GIT_ROOT}/plaid_mcp_proxy.py"
  PID_FILE="${GIT_ROOT}/.plaid_proxy.pid"

  if [[ -x "${PYTHON}" ]] && [[ -f "${PROXY_SCRIPT}" ]]; then
    # Kill any existing proxy (stale from previous session)
    if [[ -f "${PID_FILE}" ]]; then
      OLD_PID=$(cat "${PID_FILE}")
      if kill -0 "${OLD_PID}" 2>/dev/null; then
        debug_log "Killing stale proxy (PID ${OLD_PID})"
        kill "${OLD_PID}" 2>/dev/null || true
        sleep 1
      fi
      rm -f "${PID_FILE}"
    fi

    # Start proxy in background
    "${PYTHON}" "${PROXY_SCRIPT}" &
    PLAID_PROXY_PID=$!
    export PLAID_PROXY_PID

    # Wait for proxy to be ready (up to 10 seconds)
    for i in {1..20}; do
      if curl -s http://localhost:8787/health >/dev/null 2>&1; then
        debug_log "Plaid MCP Proxy started (PID ${PLAID_PROXY_PID})"
        break
      fi
      sleep 0.5
    done

    if ! curl -s http://localhost:8787/health >/dev/null 2>&1; then
      warn_log "Plaid MCP Proxy failed to start"
      kill "${PLAID_PROXY_PID}" 2>/dev/null || true
      unset PLAID_PROXY_PID
    fi
  fi
fi
```

**Step 2: Commit**

```bash
git add .claude/pre-launch.sh
git commit -m "feat(pre-launch): start proxy instead of fetching token"
```

---

## Task 7: Update .mcp.json to Use Proxy

**Files:**

- Modify: `.mcp.json`

**Step 1: Point to local proxy**

```json
{
  "mcpServers": {
    "google-sheets": {
      "command": "/opt/homebrew/bin/uvx",
      "args": ["mcp-google-sheets@latest"],
      "env": {
        "SERVICE_ACCOUNT_PATH": "/path/to/your/service-account.json",
        "DRIVE_FOLDER_ID": "YOUR_DRIVE_FOLDER_ID"
      }
    },
    "plaid-dashboard": {
      "type": "sse",
      "url": "http://localhost:8787/mcp/sse"
    }
  }
}
```

**Step 2: Commit**

```bash
git add .mcp.json
git commit -m "feat(mcp): point plaid-dashboard to local proxy"
```

---

## Task 8: Create User Documentation

**Files:**

- Create: `docs/PLAID_PROXY.md`

**Step 1: Write documentation**

```markdown
# Plaid MCP Token Refresh Proxy

## The Problem

The official Plaid Dashboard MCP (`api.dashboard.plaid.com/mcp/sse`) uses OAuth tokens that expire after approximately 30 minutes. When a token expires mid-session, the MCP connection fails, requiring a restart of Claude Code to get a fresh token.

## The Solution

A lightweight local proxy server that:
- Sits between Claude Code and Plaid's MCP endpoint
- Manages token refresh transparently
- Pre-emptively refreshes tokens before expiry
- Automatically reconnects on auth failures

## Quick Start

### 1. Set Environment Variables

```bash
export PLAID_CLIENT_ID="your_client_id"
export PLAID_SECRET="your_secret"
```

### 2. Start the Proxy

```bash
python plaid_mcp_proxy.py
```

Or let `pre-launch.sh` start it automatically when you launch Claude Code.

### 3. Configure MCP

Update your `.mcp.json` to point to the local proxy:

```json
{
  "mcpServers": {
    "plaid-dashboard": {
      "type": "sse",
      "url": "http://localhost:8787/mcp/sse"
    }
  }
}
```

## How It Works

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│   Claude Code   │────▶│   plaid_mcp_proxy    │────▶│  api.dashboard.     │
│                 │ SSE │   (localhost:8787)   │ SSE │  plaid.com/mcp/sse  │
└─────────────────┘     └──────────────────────┘     └─────────────────────┘
```

### Token Refresh Strategy

1. **Pre-emptive Refresh**: The proxy tracks token expiry and refreshes 5 minutes before expiration, during idle moments.

2. **Reactive Fallback**: If Plaid returns a 401, the proxy immediately refreshes the token and retries the request.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PLAID_CLIENT_ID` | (required) | Plaid API client ID |
| `PLAID_SECRET` | (required) | Plaid API secret |
| `PLAID_PROXY_PORT` | `8787` | Port for proxy server |
| `PLAID_PROXY_LOG` | `~/.plaid_mcp_proxy.log` | Log file path |
| `PLAID_PROXY_DEBUG` | `0` | Set to `1` for debug logging |

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check, returns token expiry info |
| `/mcp/sse` | GET | SSE connection (proxied to Plaid) |
| `/mcp/sse` | POST | MCP requests (proxied to Plaid) |

## Troubleshooting

### Proxy won't start

Check logs at `~/.plaid_mcp_proxy.log` or run with `PLAID_PROXY_DEBUG=1`.

### Token refresh failing

Verify your `PLAID_CLIENT_ID` and `PLAID_SECRET` are correct and have MCP dashboard scope.

### Connection drops

The proxy will automatically reconnect. Check logs for details.

## Files

- `plaid_mcp_proxy.py` - The proxy server
- `plaid_token.py` - Token management (reused by proxy)
- `.plaid_proxy.pid` - PID file for lifecycle management
- `.plaid_token.json` - Cached token (auto-managed)

```

**Step 2: Commit**

```bash
git add docs/PLAID_PROXY.md
git commit -m "docs: add Plaid proxy documentation"
```

---

## Task 9: Update CLAUDE.md

**Files:**

- Modify: `CLAUDE.md`

**Step 1: Add proxy to Working Integrations table**

Update the table to include proxy:

```markdown
| Plaid Dashboard | MCP via local proxy | `plaid_mcp_proxy.py` |
```

**Step 2: Add proxy to Project Structure**

```markdown
plaid_mcp_proxy.py     # Plaid MCP token refresh proxy
```

**Step 3: Add Proxy section**

Add after "Plaid Account Mapping" section:

```markdown
## Plaid MCP Proxy

The `plaid_mcp_proxy.py` handles automatic token refresh for the Plaid Dashboard MCP. It starts automatically via `pre-launch.sh` and runs on `localhost:8787`.

See `docs/PLAID_PROXY.md` for details.
```

**Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with proxy info"
```

---

## Task 10: Integration Test

**Step 1: Restart Claude Code**

Exit and restart Claude Code to trigger the proxy startup.

**Step 2: Verify proxy is running**

```bash
curl -s http://localhost:8787/health | jq
```

Expected:

```json
{
  "status": "ok",
  "token_expires_in": <number>,
  "has_token": true
}
```

**Step 3: Test Plaid MCP through proxy**

Use the Plaid MCP tool to list teams - it should work transparently through the proxy.

**Step 4: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix: integration test fixes"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Add aiohttp dependency | `requirements.txt` |
| 2 | Add get_token_with_expiry | `plaid_token.py` |
| 3 | Create basic HTTP server | `plaid_mcp_proxy.py` |
| 4 | Add token management | `plaid_mcp_proxy.py` |
| 5 | Add SSE proxy handlers | `plaid_mcp_proxy.py` |
| 6 | Update pre-launch.sh | `.claude/pre-launch.sh` |
| 7 | Update .mcp.json | `.mcp.json` |
| 8 | Create documentation | `docs/PLAID_PROXY.md` |
| 9 | Update CLAUDE.md | `CLAUDE.md` |
| 10 | Integration test | - |
