#!/usr/bin/env python3
"""Watch Plaid MCP token expiration and output remaining lifetime."""

import json
import sys
import time
from pathlib import Path

TOKEN_FILE = Path(__file__).parent / ".plaid_token.json"

# ANSI colors
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
RESET = "\033[0m"
CLEAR_LINE = "\033[K"


def get_token_status() -> tuple[float, bool]:
    """Return (seconds_remaining, has_refresh_token)."""
    if not TOKEN_FILE.exists():
        return -1, False

    try:
        data = json.loads(TOKEN_FILE.read_text())
        expires_at = data.get("expires_at", 0)
        has_refresh = bool(data.get("refresh_token"))
        return expires_at - time.time(), has_refresh
    except (json.JSONDecodeError, KeyError):
        return -1, False


def format_time(seconds: float) -> str:
    """Format seconds as MM:SS."""
    if seconds < 0:
        return "EXPIRED"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


def get_color(seconds: float) -> str:
    """Return ANSI color based on remaining time."""
    if seconds < 0:
        return RED
    elif seconds < 120:  # < 2 minutes
        return RED
    elif seconds < 300:  # < 5 minutes
        return YELLOW
    return GREEN


def watch(interval: int = 30, inline: bool = False) -> None:
    """Watch token and output remaining time.

    Args:
        interval: Seconds between updates
        inline: If True, update in place (single line)
    """
    try:
        while True:
            remaining, has_refresh = get_token_status()
            color = get_color(remaining)
            time_str = format_time(remaining)

            if inline:
                print(
                    f"\r{CLEAR_LINE}{color}Plaid token: {time_str}{RESET}",
                    end="",
                    flush=True,
                )
            else:
                refresh_note = " (has refresh)" if has_refresh else ""
                print(f"{color}Plaid token: {time_str}{refresh_note}{RESET}")

            if remaining < 0:
                if not inline:
                    print("Token expired. Restart Claude Code.")
                break

            time.sleep(interval)
    except KeyboardInterrupt:
        if inline:
            print()  # Newline after inline output


def status() -> None:
    """Print one-time status."""
    remaining, has_refresh = get_token_status()
    color = get_color(remaining)
    time_str = format_time(remaining)

    if remaining < 0:
        print(f"{RED}Plaid token: EXPIRED{RESET}")
        print("Restart Claude Code for fresh token.")
        sys.exit(1)
    else:
        refresh_note = " (has refresh)" if has_refresh else ""
        print(f"{color}Plaid token: {time_str} remaining{refresh_note}{RESET}")


def main() -> None:
    """Entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Watch Plaid token")
    parser.add_argument(
        "--watch",
        "-w",
        action="store_true",
        help="Continuously watch token (default: one-time status)",
    )
    parser.add_argument(
        "--interval",
        "-i",
        type=int,
        default=30,
        help="Update interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--inline",
        action="store_true",
        help="Update in place (single line, for tmux/status bars)",
    )
    args = parser.parse_args()

    if args.watch:
        watch(interval=args.interval, inline=args.inline)
    else:
        status()


if __name__ == "__main__":
    main()
