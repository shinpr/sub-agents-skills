"""CLI resolution: detect calling environment, fall back to default."""

from __future__ import annotations

import os

_VALID_CLIS = ("claude", "cursor-agent", "codex", "gemini")


def detect_caller_cli() -> str | None:
    """Detect which CLI is calling this script (best-effort).

    Checks well-known env vars first, then falls back to a parent-process
    cmdline probe via /proc (Linux only — macOS lacks /proc and falls
    through silently).
    """
    if os.environ.get("CLAUDE_CODE"):
        return "claude"
    if os.environ.get("CURSOR_AGENT"):
        return "cursor-agent"
    if os.environ.get("CODEX_CLI"):
        return "codex"
    if os.environ.get("GEMINI_CLI"):
        return "gemini"

    try:
        ppid = os.getppid()
        cmdline_path = f"/proc/{ppid}/cmdline"
        if os.path.exists(cmdline_path):
            with open(cmdline_path) as f:
                cmdline = f.read().lower()
                if "claude" in cmdline:
                    return "claude"
                if "cursor" in cmdline:
                    return "cursor-agent"
                if "codex" in cmdline:
                    return "codex"
                if "gemini" in cmdline:
                    return "gemini"
    except (FileNotFoundError, PermissionError, OSError):
        # /proc absent on macOS, may be unreadable under sandbox. Caller
        # detection is best-effort — fall through silently.
        pass

    return None


def resolve_cli(frontmatter_cli: str | None, default: str = "codex") -> str:
    """Resolve which CLI to use.

    Priority: frontmatter > caller detection > default.
    Invalid frontmatter values fall through to caller detection (lenient).
    """
    if frontmatter_cli and frontmatter_cli in _VALID_CLIS:
        return frontmatter_cli

    detected = detect_caller_cli()
    if detected:
        return detected

    return default
