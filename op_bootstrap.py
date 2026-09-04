"""Bootstrap: ensure the project venv is active and credentials are loaded.

Import this module before any third-party packages and call bootstrap().
Uses only stdlib so it is importable by any Python interpreter.

Re-exec chain (at most 3 execs):
  system python3 → venv python3 → op run → venv python3 (converged)
"""

import os
import sys

# Guard var injected into the environment before op run so a broken
# secrets.op file can't cause an infinite re-exec loop.
_FA_OP_LAUNCHED = "_FA_OP_LAUNCHED"


def bootstrap(sentinel: str) -> None:
    """Ensure the project venv is active and `sentinel` env var is set.

    If either condition is not met, re-execs the current script via the
    appropriate mechanism (venv python or `op run`) and does not return.
    When both conditions are met, returns immediately.

    Args:
        sentinel: Name of an env var that proves credentials are loaded
                  (e.g. "PLAID_CLIENT_ID", "MERCURY_API_TOKEN").
    """
    project_dir = os.path.dirname(os.path.abspath(__file__))
    venv_python = os.path.join(project_dir, ".venv", "bin", "python3")
    in_venv = os.path.abspath(sys.prefix) == os.path.abspath(
        os.path.join(project_dir, ".venv")
    )

    # Step 1: Re-exec through the project venv if we're not already in it.
    # Environment (including any credentials from op run) is inherited.
    if not in_venv and os.path.exists(venv_python):
        os.execv(venv_python, [venv_python] + sys.argv)

    # Step 2: If credentials are present, nothing more to do.
    if os.getenv(sentinel):
        return

    # Guard against a broken secrets file causing an infinite re-exec loop.
    if os.getenv(_FA_OP_LAUNCHED):
        sys.exit(
            f"Error: {sentinel} was not set after `op run`.\n"
            "Check that .claude/secrets.op contains the correct 1Password reference."
        )

    secrets = os.path.join(project_dir, ".claude", "secrets.op")
    if not os.path.exists(secrets):
        sys.exit(
            f"Error: {sentinel} is not set and secrets file not found:\n"
            f"  {secrets}\n"
            "Copy .claude/secrets.op.example to .claude/secrets.op "
            "and fill in your vault references."
        )

    # Re-exec via op run to inject secrets, then the chain continues above.
    os.environ[_FA_OP_LAUNCHED] = "1"
    try:
        os.execvp("op", ["op", "run", f"--env-file={secrets}", "--"] + sys.argv)
    except OSError as e:
        sys.exit(
            f"Error: {sentinel} is not set and could not run 1Password CLI (op).\n"
            f"Details: {e}\n"
            "Install op: https://developer.1password.com/docs/cli/get-started/"
        )
