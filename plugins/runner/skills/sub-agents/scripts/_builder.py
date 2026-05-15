"""Per-CLI command argument construction.

Holds the static knowledge of how each backend CLI is invoked: base flags,
permission-level mapping, system-prompt injection mechanism. The dispatcher
:func:`build_invocation_args` returns ``(command, args, env_override)``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from _loader import DEFAULT_PERMISSION


@dataclass(frozen=True)
class AgentInvocation:
    """A single sub-agent invocation request."""

    cli: str
    prompt: str
    cwd: str
    system_context: str = ""
    agent_file: str | None = None
    permission: str = DEFAULT_PERMISSION


def build_command(cli: str, prompt: str) -> tuple[str, list]:
    """Build the base command + base args (no permission/system-prompt yet)."""
    if cli == "codex":
        return "codex", ["exec", "--json", "--skip-git-repo-check", prompt]

    if cli == "claude":
        return "claude", ["--output-format", "stream-json", "--verbose", "-p", prompt]

    if cli == "gemini":
        # --skip-trust is required for headless runs in untrusted folders;
        # passing --cwd is itself a trust statement, and Gemini otherwise
        # downgrades the approval mode to "default" (interactive prompts)
        # which deadlocks here.
        return "gemini", ["--skip-trust", "--output-format", "stream-json", "-p", prompt]

    if cli == "cursor-agent":
        # API key is forwarded via CURSOR_API_KEY env (in build_invocation_args),
        # never via argv — argv would expose the secret in `ps` output.
        return "cursor-agent", ["--output-format", "json", "-p", prompt]

    raise ValueError(f"Unknown CLI: {cli}")


_PERMISSION_MAPPING = {
    "codex": {
        "read-only": ["-s", "read-only"],
        "safe-edit": ["-s", "workspace-write", "-c", "approval_policy=never"],
        "yolo": ["--dangerously-bypass-approvals-and-sandbox"],
    },
    "claude": {
        "read-only": ["--permission-mode", "plan"],
        "safe-edit": ["--permission-mode", "acceptEdits"],
        "yolo": ["--dangerously-skip-permissions"],
    },
    "gemini": {
        "read-only": ["--approval-mode", "plan"],
        "safe-edit": ["--approval-mode", "auto_edit"],
        "yolo": ["-y"],
    },
    "cursor-agent": {
        "read-only": ["--mode", "plan"],
        "safe-edit": ["--trust"],
        "yolo": ["-f", "--trust"],
    },
}


def permission_flags(cli: str, permission: str) -> list:
    """Map permission level to CLI-specific flags. Fails fast on unknown values."""
    try:
        return list(_PERMISSION_MAPPING[cli][permission])
    except KeyError as e:
        raise ValueError(f"No permission mapping for cli={cli!r}, permission={permission!r}") from e


def _concatenated_args(
    inv: AgentInvocation, perm_flags: list, env: dict | None
) -> tuple[str, list, dict | None]:
    """Fallback: concatenate system context into the user prompt argument.

    Used when a CLI lacks a native system-prompt mechanism we can target.
    """
    formatted_prompt = f"[System Context]\n{inv.system_context}\n\n[User Prompt]\n{inv.prompt}"
    command, base_args = build_command(inv.cli, formatted_prompt)
    return command, perm_flags + base_args, env


def _build_claude_args(inv: AgentInvocation) -> tuple[str, list, dict | None]:
    perm = permission_flags(inv.cli, inv.permission)
    system_prompt = f"cwd: {inv.cwd}\n\n{inv.system_context}"
    command, base_args = build_command(inv.cli, inv.prompt)
    return command, perm + ["--append-system-prompt", system_prompt] + base_args, None


def _build_gemini_args(inv: AgentInvocation) -> tuple[str, list, dict | None]:
    perm = permission_flags(inv.cli, inv.permission)
    if inv.agent_file:
        command, base_args = build_command(inv.cli, inv.prompt)
        return command, perm + base_args, {"GEMINI_SYSTEM_MD": inv.agent_file}
    return _concatenated_args(inv, perm, env=None)


def _build_codex_args(inv: AgentInvocation) -> tuple[str, list, dict | None]:
    perm = permission_flags(inv.cli, inv.permission)
    return _concatenated_args(inv, perm, env=None)


def _build_cursor_args(inv: AgentInvocation) -> tuple[str, list, dict | None]:
    perm = permission_flags(inv.cli, inv.permission)
    # Forward CLI_API_KEY (skill contract) as CURSOR_API_KEY (cursor's native
    # env). Passing via env keeps the secret out of `ps` output.
    api_key = os.environ.get("CLI_API_KEY")
    env_override = {"CURSOR_API_KEY": api_key} if api_key else None
    return _concatenated_args(inv, perm, env=env_override)


_BUILDERS = {
    "claude": _build_claude_args,
    "gemini": _build_gemini_args,
    "codex": _build_codex_args,
    "cursor-agent": _build_cursor_args,
}


def build_invocation_args(inv: AgentInvocation) -> tuple[str, list, dict | None]:
    """Dispatch to the per-CLI argument builder.

    Returns ``(command, args, env_override_or_None)``.
    """
    try:
        builder = _BUILDERS[inv.cli]
    except KeyError as e:
        raise ValueError(f"Unknown CLI: {inv.cli}") from e
    return builder(inv)
