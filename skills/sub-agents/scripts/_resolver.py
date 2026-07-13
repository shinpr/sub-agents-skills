from __future__ import annotations

import os

from _constants import SUPPORTED_CLIS


def detect_caller_cli() -> str | None:
    """Detect the caller from environment variables or Linux ``/proc``."""
    if os.environ.get("CLAUDE_CODE"):
        return "claude"
    if os.environ.get("CURSOR_AGENT"):
        return "cursor-agent"
    if os.environ.get("CODEX_CLI"):
        return "codex"
    if os.environ.get("GEMINI_CLI"):
        return "gemini"
    if os.environ.get("GROK_CLI"):
        return "grok"

    try:
        ppid = os.getppid()
        cmdline_path = f"/proc/{ppid}/cmdline"
        if os.path.exists(cmdline_path):
            with open(cmdline_path, encoding="utf-8", errors="replace") as f:
                cmdline = f.read().lower()
                if "claude" in cmdline:
                    return "claude"
                if "cursor" in cmdline:
                    return "cursor-agent"
                if "codex" in cmdline:
                    return "codex"
                if "gemini" in cmdline:
                    return "gemini"
                if "grok" in cmdline:
                    return "grok"
                if "opencode" in cmdline:
                    return "opencode"
    except (FileNotFoundError, PermissionError, OSError):
        # Caller detection is optional.
        pass

    return None


def resolve_cli(frontmatter_cli: str | None, default: str = "codex") -> str:
    """Resolve a backend from frontmatter, caller detection, then default."""
    if frontmatter_cli and frontmatter_cli in SUPPORTED_CLIS:
        return frontmatter_cli

    detected = detect_caller_cli()
    if detected:
        return detected

    return default
