#!/usr/bin/env python3
"""Plaid MCP Token Refresh Proxy.

A local proxy server that handles OAuth token refresh transparently,
eliminating the need to restart Claude Code when tokens expire.
"""

import asyncio
import logging
import os
import signal
import time
from pathlib import Path

from aiohttp import web, ClientSession, ClientTimeout

from plaid_token import get_token_with_expiry

# Configuration
HOST = os.getenv("PLAID_PROXY_HOST", "127.0.0.1")
PORT = int(os.getenv("PLAID_PROXY_PORT", "8788"))
PLAID_MCP_SSE_URL = "https://api.dashboard.plaid.com/mcp/sse"
PLAID_MCP_MESSAGE_URL = "https://api.dashboard.plaid.com/mcp/message"
DEBUG = os.getenv("PLAID_PROXY_DEBUG", "").lower() in ("1", "true", "yes")

# Pre-emptive refresh: refresh token 5 minutes before expiry
REFRESH_BUFFER_SECONDS = 300

PID_FILE = Path(__file__).parent / ".plaid_proxy.pid"
LOG_FILE = Path(__file__).parent / ".plaid_mcp_proxy.log"

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


class TokenManager:
    """Manage OAuth token lifecycle with pre-emptive refresh."""

    def __init__(self):
        self.access_token: str | None = None
        self.expires_at: float = 0
        self._refresh_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        """Get current valid token, refreshing if needed."""
        async with self._lock:
            # Check if token is still valid (with buffer)
            if self.access_token and self.expires_at > time.time() + 60:
                return self.access_token

            # Need to refresh
            logger.info("Token expired or missing, fetching new token")
            await self._refresh()
            return self.access_token

    async def _refresh(self):
        """Refresh the token (call within lock)."""
        try:
            # Run sync function in executor to not block event loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, get_token_with_expiry)
            token, expires_at = result
            self.access_token = token
            self.expires_at = expires_at
            remaining = int(expires_at - time.time())
            logger.info(f"Token refreshed, expires in {remaining}s")
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
            raise

    async def force_refresh(self):
        """Force immediate token refresh (e.g., on 401)."""
        async with self._lock:
            self.access_token = None
            self.expires_at = 0
            await self._refresh()

    async def start_refresh_loop(self):
        """Start background task for pre-emptive refresh."""
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        logger.info("Started pre-emptive refresh loop")

    async def _refresh_loop(self):
        """Background loop to refresh token before expiry."""
        while True:
            try:
                # Calculate sleep time
                if self.expires_at > 0:
                    sleep_time = max(
                        10,  # Minimum 10 seconds between checks
                        self.expires_at - time.time() - REFRESH_BUFFER_SECONDS,
                    )
                else:
                    sleep_time = 60  # No token yet, check in 60s

                logger.debug(f"Refresh loop sleeping for {int(sleep_time)}s")
                await asyncio.sleep(sleep_time)

                # Pre-emptively refresh if within buffer
                if self.expires_at - time.time() < REFRESH_BUFFER_SECONDS:
                    logger.info("Pre-emptive token refresh starting")
                    async with self._lock:
                        await self._refresh()

            except asyncio.CancelledError:
                logger.info("Refresh loop cancelled")
                break
            except Exception as e:
                logger.error(f"Background refresh failed: {e}")
                await asyncio.sleep(60)  # Wait before retry

    async def stop(self):
        """Stop the refresh loop."""
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None
            logger.info("Refresh loop stopped")


# Global token manager
token_manager = TokenManager()


async def health_handler(request: web.Request) -> web.Response:
    """Health check endpoint with token status."""
    if token_manager.expires_at:
        expires_in = int(token_manager.expires_at - time.time())
    else:
        expires_in = 0
    return web.json_response(
        {
            "status": "ok",
            "token_expires_in": max(0, expires_in),
            "has_token": token_manager.access_token is not None,
        }
    )


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

    async def connect_to_plaid():
        """Establish connection to Plaid with current token."""
        token = await token_manager.get_token()
        timeout = ClientTimeout(total=None, sock_read=None)
        session = ClientSession(timeout=timeout)

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "text/event-stream",
        }

        resp = await session.get(PLAID_MCP_SSE_URL, headers=headers)
        if resp.status == 401:
            await session.close()
            # Force token refresh and retry
            logger.warning("Got 401 on SSE, forcing token refresh")
            await token_manager.force_refresh()
            token = await token_manager.get_token()
            session = ClientSession(timeout=timeout)
            headers["Authorization"] = f"Bearer {token}"
            resp = await session.get(PLAID_MCP_SSE_URL, headers=headers)

        if resp.status != 200:
            text = await resp.text()
            await session.close()
            raise Exception(f"Plaid returned {resp.status}: {text}")

        return session, resp

    session = None
    try:
        session, plaid_resp = await connect_to_plaid()
        logger.info("Connected to Plaid MCP SSE stream")

        # Stream events from Plaid to client
        async for chunk in plaid_resp.content.iter_any():
            await response.write(chunk)

    except asyncio.CancelledError:
        logger.info("SSE client disconnected")
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


async def mcp_post_handler(request: web.Request) -> web.Response:
    """Proxy POST requests to Plaid MCP message endpoint."""
    # Build upstream URL with query params (e.g., sessionId)
    query_string = request.query_string
    upstream_url = PLAID_MCP_MESSAGE_URL
    if query_string:
        upstream_url = f"{upstream_url}?{query_string}"

    logger.debug(f"Proxying POST request to {upstream_url}")

    token = await token_manager.get_token()
    body = await request.read()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": request.content_type or "application/json",
    }

    async with ClientSession() as session:
        url = upstream_url
        async with session.post(url, headers=headers, data=body) as resp:
            if resp.status == 401:
                # Force refresh and retry once
                logger.warning("Got 401 on POST, forcing token refresh")
                await token_manager.force_refresh()
                token = await token_manager.get_token()
                headers["Authorization"] = f"Bearer {token}"
                async with session.post(
                    upstream_url, headers=headers, data=body
                ) as retry_resp:
                    response_body = await retry_resp.read()
                    return web.Response(
                        status=retry_resp.status,
                        body=response_body,
                        content_type=retry_resp.content_type,
                    )

            response_body = await resp.read()
            return web.Response(
                status=resp.status,
                body=response_body,
                content_type=resp.content_type,
            )


def create_app() -> web.Application:
    """Create the aiohttp application."""
    app = web.Application()
    app.router.add_get("/health", health_handler)
    app.router.add_get("/mcp/sse", sse_proxy_handler)
    app.router.add_post("/mcp/message", mcp_post_handler)
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


def kill_stale_process():
    """Kill any stale proxy process from previous run."""
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            os.kill(old_pid, signal.SIGTERM)
            logger.info(f"Killed stale process {old_pid}")
            time.sleep(0.5)  # Give it time to exit
        except (ProcessLookupError, ValueError):
            pass  # Process already gone or invalid PID
        finally:
            cleanup_pid_file()


async def run_server():
    """Run the proxy server."""
    kill_stale_process()
    write_pid_file()

    # Get initial token
    logger.info("Fetching initial token...")
    await token_manager.get_token()
    await token_manager.start_refresh_loop()

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, HOST, PORT)
    await site.start()
    logger.info(f"Proxy server started on http://{HOST}:{PORT}")

    # Setup signal handlers
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def signal_handler():
        logger.info("Received shutdown signal")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    # Wait for shutdown signal
    await stop_event.wait()

    # Cleanup
    await token_manager.stop()
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
