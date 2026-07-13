from __future__ import annotations

import json
import os
from dataclasses import dataclass

from _constants import SUPPORTED_CLIS_HELP, format_concatenated_prompt
from _loader import DEFAULT_PERMISSION


@dataclass(frozen=True)
class AgentInvocation:
    cli: str
    prompt: str
    cwd: str
    system_context: str = ""
    agent_file: str | None = None
    permission: str = DEFAULT_PERMISSION
    model: str | None = None
    effort: str | None = None


def build_command(cli: str, prompt: str) -> tuple[str, list]:
    if cli == "codex":
        return "codex", ["exec", "--json", "--skip-git-repo-check", prompt]

    if cli in ("claude", "glm"):
        # GLM uses Claude CLI as its transport.
        return "claude", ["--output-format", "stream-json", "--verbose", "-p", prompt]

    if cli == "gemini":
        # Headless Gemini otherwise prompts for folder trust.
        return "gemini", ["--skip-trust", "--output-format", "stream-json", "-p", prompt]

    if cli == "grok":
        return "grok", [
            "--output-format",
            "json",
            "--verbatim",
            "-p",
            prompt,
        ]

    if cli == "opencode":
        return "opencode", ["run", "--format", "json", "--auto", prompt]

    if cli == "cursor-agent":
        # Cursor credentials stay out of argv.
        return "cursor-agent", ["--output-format", "json", "-p", prompt]

    raise ValueError(f"Unsupported CLI {cli!r}. Choose one of: {SUPPORTED_CLIS_HELP}.")


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
    # Grok enforces these levels through sandbox profiles.
    "grok": {
        "read-only": ["--permission-mode", "bypassPermissions", "--sandbox", "read-only"],
        "safe-edit": ["--permission-mode", "bypassPermissions", "--sandbox", "workspace"],
        "yolo": ["--permission-mode", "bypassPermissions", "--sandbox", "off"],
    },
    # OpenCode permissions are supplied through OPENCODE_PERMISSION.
    "opencode": {
        "read-only": [],
        "safe-edit": [],
        "yolo": [],
    },
}

_PERMISSION_MAPPING["glm"] = _PERMISSION_MAPPING["claude"]


def permission_flags(cli: str, permission: str) -> list:
    try:
        return list(_PERMISSION_MAPPING[cli][permission])
    except KeyError as e:
        raise ValueError(f"No permission mapping for cli={cli!r}, permission={permission!r}") from e


_EFFORT_SUPPORTED_CLIS = frozenset({"codex", "claude", "glm", "grok", "opencode"})
_EFFORT_UNSUPPORTED_CLIS = frozenset({"cursor-agent", "gemini"})


def effort_flags(cli: str, effort: str | None) -> list:
    if not effort:
        return []

    if cli == "codex":
        # JSON encoding produces a safe TOML string for the config override.
        encoded_effort = json.dumps(effort, ensure_ascii=False)
        return ["-c", f"model_reasoning_effort={encoded_effort}"]
    if cli in ("claude", "glm"):
        return ["--effort", effort]
    if cli == "grok":
        return ["--reasoning-effort", effort]
    if cli == "opencode":
        return ["--variant", effort]
    if cli in _EFFORT_UNSUPPORTED_CLIS:
        supported = ", ".join(sorted(_EFFORT_SUPPORTED_CLIS))
        raise ValueError(
            f"Effort is available for: {supported}; selected backend: {cli!r}. "
            "Remove effort or select a listed backend."
        )
    raise ValueError(f"Unsupported CLI {cli!r}. Choose one of: {SUPPORTED_CLIS_HELP}.")


def _invocation_flags(inv: AgentInvocation) -> list:
    flags = permission_flags(inv.cli, inv.permission)
    if inv.model:
        flags.extend(["--model", inv.model])
    flags.extend(effort_flags(inv.cli, inv.effort))
    return flags


def _concatenated_args(
    inv: AgentInvocation, perm_flags: list, env: dict | None
) -> tuple[str, list, dict | None]:
    formatted_prompt = format_concatenated_prompt(inv.system_context, inv.prompt)
    command, base_args = build_command(inv.cli, formatted_prompt)
    return command, perm_flags + base_args, env


def _build_claude_args(inv: AgentInvocation) -> tuple[str, list, dict | None]:
    perm = _invocation_flags(inv)
    system_prompt = f"cwd: {inv.cwd}\n\n{inv.system_context}"
    command, base_args = build_command(inv.cli, inv.prompt)
    return command, perm + ["--append-system-prompt", system_prompt] + base_args, None


def _build_gemini_args(inv: AgentInvocation) -> tuple[str, list, dict | None]:
    perm = _invocation_flags(inv)
    if inv.agent_file:
        command, base_args = build_command(inv.cli, inv.prompt)
        return command, perm + base_args, {"GEMINI_SYSTEM_MD": inv.agent_file}
    return _concatenated_args(inv, perm, env=None)


def _build_codex_args(inv: AgentInvocation) -> tuple[str, list, dict | None]:
    perm = _invocation_flags(inv)
    return _concatenated_args(inv, perm, env=None)


def _build_grok_args(inv: AgentInvocation) -> tuple[str, list, dict | None]:
    perm = _invocation_flags(inv)
    formatted_prompt = format_concatenated_prompt(inv.system_context, inv.prompt)
    command, base_args = build_command(inv.cli, formatted_prompt)
    return command, perm + ["--cwd", inv.cwd] + base_args, None


_OPENCODE_PERMISSION_MAPPING = {
    "read-only": {
        "edit": "deny",
        "bash": "deny",
        "task": "deny",
        "external_directory": "deny",
        "question": "deny",
    },
    "safe-edit": {
        "edit": "allow",
        "bash": "allow",
        "task": "deny",
        "external_directory": "deny",
        "question": "deny",
    },
    "yolo": "allow",
}


def _build_opencode_args(inv: AgentInvocation) -> tuple[str, list, dict | None]:
    perm = _invocation_flags(inv)
    formatted_prompt = format_concatenated_prompt(inv.system_context, inv.prompt)
    command, base_args = build_command(inv.cli, formatted_prompt)
    env_override = {"OPENCODE_PERMISSION": json.dumps(_OPENCODE_PERMISSION_MAPPING[inv.permission])}
    return command, perm + base_args, env_override


_GLM_BASE_URL = "https://api.z.ai/api/anthropic"


def _build_glm_args(inv: AgentInvocation) -> tuple[str, list, dict | None]:
    """Route a Claude CLI invocation to Z.ai for GLM."""
    api_key = os.environ.get("CLI_API_KEY")
    if not api_key or not api_key.strip():
        raise ValueError(
            "GLM configuration error: CLI_API_KEY is unset or blank. "
            "A Z.ai API token is required before retrying."
        )
    perm = _invocation_flags(inv)
    system_prompt = f"cwd: {inv.cwd}\n\n{inv.system_context}"
    command, base_args = build_command(inv.cli, inv.prompt)
    env_override = {
        "ANTHROPIC_BASE_URL": _GLM_BASE_URL,
        "ANTHROPIC_AUTH_TOKEN": api_key,
        # Prevent inherited Anthropic credentials from reaching Z.ai.
        "ANTHROPIC_API_KEY": None,
    }
    return command, perm + ["--system-prompt", system_prompt] + base_args, env_override


def _build_cursor_args(inv: AgentInvocation) -> tuple[str, list, dict | None]:
    perm = _invocation_flags(inv)
    # Keep the credential out of argv; logged-in sessions need no override.
    api_key = os.environ.get("CLI_API_KEY")
    env_override = {"CURSOR_API_KEY": api_key} if api_key else None
    return _concatenated_args(inv, perm, env=env_override)


_BUILDERS = {
    "claude": _build_claude_args,
    "gemini": _build_gemini_args,
    "codex": _build_codex_args,
    "cursor-agent": _build_cursor_args,
    "glm": _build_glm_args,
    "grok": _build_grok_args,
    "opencode": _build_opencode_args,
}


def build_invocation_args(inv: AgentInvocation) -> tuple[str, list, dict | None]:
    try:
        builder = _BUILDERS[inv.cli]
    except KeyError as e:
        raise ValueError(
            f"Unsupported CLI {inv.cli!r}. Choose one of: {SUPPORTED_CLIS_HELP}."
        ) from e
    return builder(inv)
