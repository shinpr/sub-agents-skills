from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
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


@dataclass(frozen=True)
class ProcessInvocation:
    """Command and environment needed to start a backend process."""

    command: str
    args: list[str]
    env_override: dict[str, str | None] | None = None


@dataclass(frozen=True)
class BackendSpec:
    """Backend capabilities that participate in command construction."""

    builder: Callable[[AgentInvocation], ProcessInvocation]
    permissions: Mapping[str, tuple[str, ...]]
    effort_option: str | None


def build_command(cli: str, prompt: str) -> tuple[str, list[str]]:
    if cli == "codex":
        return "codex", ["exec", "--json", "--skip-git-repo-check", prompt]

    if cli in ("claude", "glm", "kimi"):
        # GLM and Kimi use Claude CLI as their transport.
        return "claude", ["--output-format", "stream-json", "--verbose", "-p", prompt]

    if cli == "gemini":
        # Headless Gemini otherwise prompts for folder trust.
        return "gemini", ["--skip-trust", "--output-format", "stream-json", "-p", prompt]

    if cli == "antigravity":
        return "agy", ["--output-format", "stream-json", "-p", prompt]

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


_CODEX_PERMISSIONS = {
    "read-only": ("-s", "read-only"),
    "safe-edit": ("-s", "workspace-write", "-c", "approval_policy=never"),
    "yolo": ("--dangerously-bypass-approvals-and-sandbox",),
}

_CLAUDE_PERMISSIONS = {
    "read-only": ("--permission-mode", "plan"),
    "safe-edit": ("--permission-mode", "acceptEdits"),
    "yolo": ("--dangerously-skip-permissions",),
}

_GEMINI_PERMISSIONS = {
    "read-only": ("--approval-mode", "plan"),
    "safe-edit": ("--approval-mode", "auto_edit"),
    "yolo": ("-y",),
}

_ANTIGRAVITY_PERMISSIONS = {
    "read-only": ("--mode", "plan", "--sandbox"),
    "safe-edit": ("--mode", "accept-edits", "--sandbox"),
    "yolo": ("--dangerously-skip-permissions",),
}

_CURSOR_PERMISSIONS = {
    # Cursor's execution mode and shell sandbox are independent controls.
    "read-only": ("--mode", "plan", "--sandbox", "enabled"),
    "safe-edit": ("--trust", "--sandbox", "enabled"),
    "yolo": ("-f", "--trust"),
}

# Grok enforces these levels through sandbox profiles.
_GROK_PERMISSIONS = {
    "read-only": ("--permission-mode", "bypassPermissions", "--sandbox", "read-only"),
    "safe-edit": ("--permission-mode", "bypassPermissions", "--sandbox", "workspace"),
    "yolo": ("--permission-mode", "bypassPermissions", "--sandbox", "off"),
}

# OpenCode permissions are supplied through OPENCODE_PERMISSION.
_OPENCODE_PERMISSIONS = {
    "read-only": (),
    "safe-edit": (),
    "yolo": (),
}


def permission_flags(cli: str, permission: str) -> list[str]:
    try:
        return list(_BACKEND_SPECS[cli].permissions[permission])
    except KeyError as e:
        raise ValueError(f"No permission mapping for cli={cli!r}, permission={permission!r}") from e


def effort_flags(cli: str, effort: str | None) -> list[str]:
    if not effort:
        return []

    try:
        effort_option = _BACKEND_SPECS[cli].effort_option
    except KeyError as e:
        raise ValueError(f"Unsupported CLI {cli!r}. Choose one of: {SUPPORTED_CLIS_HELP}.") from e

    if effort_option == "model_reasoning_effort":
        # JSON encoding produces a safe TOML string for the config override.
        encoded_effort = json.dumps(effort, ensure_ascii=False)
        return ["-c", f"model_reasoning_effort={encoded_effort}"]
    if effort_option is not None:
        return [effort_option, effort]

    supported = ", ".join(
        sorted(name for name, spec in _BACKEND_SPECS.items() if spec.effort_option)
    )
    raise ValueError(
        f"Effort is available for: {supported}; selected backend: {cli!r}. "
        "Remove effort or select a listed backend."
    )


def _invocation_flags(inv: AgentInvocation) -> list:
    flags = permission_flags(inv.cli, inv.permission)
    if inv.model:
        flags.extend(["--model", inv.model])
    flags.extend(effort_flags(inv.cli, inv.effort))
    return flags


def _concatenated_args(
    inv: AgentInvocation,
    perm_flags: list[str],
    env: dict[str, str | None] | None,
) -> ProcessInvocation:
    formatted_prompt = format_concatenated_prompt(inv.system_context, inv.prompt)
    command, base_args = build_command(inv.cli, formatted_prompt)
    return ProcessInvocation(command, perm_flags + base_args, env)


def _build_claude_args(inv: AgentInvocation) -> ProcessInvocation:
    perm = _invocation_flags(inv)
    system_prompt = f"cwd: {inv.cwd}\n\n{inv.system_context}"
    command, base_args = build_command(inv.cli, inv.prompt)
    return ProcessInvocation(command, perm + ["--append-system-prompt", system_prompt] + base_args)


def _build_gemini_args(inv: AgentInvocation) -> ProcessInvocation:
    perm = _invocation_flags(inv)
    if inv.agent_file:
        command, base_args = build_command(inv.cli, inv.prompt)
        return ProcessInvocation(command, perm + base_args, {"GEMINI_SYSTEM_MD": inv.agent_file})
    return _concatenated_args(inv, perm, env=None)


def _build_concatenated_args(inv: AgentInvocation) -> ProcessInvocation:
    perm = _invocation_flags(inv)
    return _concatenated_args(inv, perm, env=None)


def _build_grok_args(inv: AgentInvocation) -> ProcessInvocation:
    perm = _invocation_flags(inv)
    formatted_prompt = format_concatenated_prompt(inv.system_context, inv.prompt)
    command, base_args = build_command(inv.cli, formatted_prompt)
    return ProcessInvocation(command, perm + ["--cwd", inv.cwd] + base_args)


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


def _build_opencode_args(inv: AgentInvocation) -> ProcessInvocation:
    perm = _invocation_flags(inv)
    formatted_prompt = format_concatenated_prompt(inv.system_context, inv.prompt)
    command, base_args = build_command(inv.cli, formatted_prompt)
    env_override = {"OPENCODE_PERMISSION": json.dumps(_OPENCODE_PERMISSION_MAPPING[inv.permission])}
    return ProcessInvocation(command, perm + base_args, env_override)


_GLM_BASE_URL = "https://api.z.ai/api/anthropic"
_KIMI_BASE_URL = "https://api.kimi.com/coding/"


def _resolve_provider_api_key(env_name: str) -> str | None:
    """Resolve a non-blank provider-specific API key."""
    api_key = os.environ.get(env_name)
    return api_key if api_key and api_key.strip() else None


# Auto-updates can bypass README, so errors name the replacement credential
# for the calling LLM to relay to existing users.
def _legacy_api_key_is_set() -> bool:
    """Check only whether the removed generic credential is present."""
    return "CLI_API_KEY" in os.environ


def _build_redirected_claude_args(
    inv: AgentInvocation,
    api_key: str,
    base_url: str,
    credential_env: str,
) -> ProcessInvocation:
    perm = _invocation_flags(inv)
    system_prompt = f"cwd: {inv.cwd}\n\n{inv.system_context}"
    command, base_args = build_command(inv.cli, inv.prompt)
    env_override = {
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_API_KEY": None,
        "ANTHROPIC_AUTH_TOKEN": None,
        # Never expose the removed generic credential to a provider process.
        "CLI_API_KEY": None,
    }
    env_override[credential_env] = api_key
    return ProcessInvocation(
        command, perm + ["--system-prompt", system_prompt] + base_args, env_override
    )


def _build_glm_args(inv: AgentInvocation) -> ProcessInvocation:
    """Route a Claude CLI invocation to Z.ai for GLM."""
    api_key = _resolve_provider_api_key("GLM_API_KEY")
    if api_key is None:
        if _legacy_api_key_is_set():
            raise ValueError(
                "GLM configuration error: CLI_API_KEY is set but no longer supported. "
                "Set GLM_API_KEY to a valid Z.ai API token and retry."
            )
        raise ValueError(
            "GLM configuration error: GLM_API_KEY is unset or blank. "
            "A Z.ai API token is required before retrying."
        )
    return _build_redirected_claude_args(inv, api_key, _GLM_BASE_URL, "ANTHROPIC_AUTH_TOKEN")


def _build_kimi_args(inv: AgentInvocation) -> ProcessInvocation:
    """Route a Claude CLI invocation to Kimi Code."""
    api_key = _resolve_provider_api_key("KIMI_API_KEY")
    if api_key is None:
        if _legacy_api_key_is_set():
            raise ValueError(
                "Kimi configuration error: CLI_API_KEY is set but no longer supported. "
                "Set KIMI_API_KEY to a valid Kimi API key and retry."
            )
        raise ValueError(
            "Kimi configuration error: KIMI_API_KEY is unset or blank. "
            "A Kimi API key is required before retrying."
        )
    return _build_redirected_claude_args(inv, api_key, _KIMI_BASE_URL, "ANTHROPIC_API_KEY")


def _build_cursor_args(inv: AgentInvocation) -> ProcessInvocation:
    perm = _invocation_flags(inv)
    # Keep the credential out of argv; logged-in sessions need no override.
    api_key = _resolve_provider_api_key("CURSOR_API_KEY")
    env_override = {}
    if "CURSOR_API_KEY" in os.environ:
        env_override["CURSOR_API_KEY"] = api_key
    if _legacy_api_key_is_set():
        env_override["CLI_API_KEY"] = None
    return _concatenated_args(inv, perm, env=env_override or None)


_BACKEND_SPECS = {
    "codex": BackendSpec(_build_concatenated_args, _CODEX_PERMISSIONS, "model_reasoning_effort"),
    "claude": BackendSpec(_build_claude_args, _CLAUDE_PERMISSIONS, "--effort"),
    "cursor-agent": BackendSpec(_build_cursor_args, _CURSOR_PERMISSIONS, None),
    "glm": BackendSpec(_build_glm_args, _CLAUDE_PERMISSIONS, "--effort"),
    "kimi": BackendSpec(_build_kimi_args, _CLAUDE_PERMISSIONS, "--effort"),
    "grok": BackendSpec(_build_grok_args, _GROK_PERMISSIONS, "--reasoning-effort"),
    "antigravity": BackendSpec(_build_concatenated_args, _ANTIGRAVITY_PERMISSIONS, "--effort"),
    "gemini": BackendSpec(_build_gemini_args, _GEMINI_PERMISSIONS, None),
    "opencode": BackendSpec(_build_opencode_args, _OPENCODE_PERMISSIONS, "--variant"),
}


def build_invocation_args(inv: AgentInvocation) -> ProcessInvocation:
    try:
        builder = _BACKEND_SPECS[inv.cli].builder
    except KeyError as e:
        raise ValueError(
            f"Unsupported CLI {inv.cli!r}. Choose one of: {SUPPORTED_CLIS_HELP}."
        ) from e
    return builder(inv)
