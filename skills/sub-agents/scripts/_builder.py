"""Per-CLI command argument construction.

Holds the static knowledge of how each backend CLI is invoked: base flags,
permission-level mapping, system-prompt injection mechanism. The dispatcher
:func:`build_invocation_args` returns ``(command, args, env_override)``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from _constants import format_concatenated_prompt
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
    model: str | None = None


def build_command(cli: str, prompt: str) -> tuple[str, list]:
    """Build the base command + base args (no permission/system-prompt yet)."""
    if cli == "codex":
        return "codex", ["exec", "--json", "--skip-git-repo-check", prompt]

    if cli in ("claude", "glm"):
        # glm runs the *claude* binary pointed at a GLM (Z.ai) Anthropic-compatible
        # endpoint via env (see _build_glm_args), so it shares claude's argv shape
        # and stream-json output — no separate parser needed.
        return "claude", ["--output-format", "stream-json", "--verbose", "-p", prompt]

    if cli == "gemini":
        # --skip-trust is required for headless runs in untrusted folders;
        # passing --cwd is itself a trust statement, and Gemini otherwise
        # downgrades the approval mode to "default" (interactive prompts)
        # which deadlocks here.
        return "gemini", ["--skip-trust", "--output-format", "stream-json", "-p", prompt]

    if cli == "grok":
        return "grok", [
            "--output-format",
            "json",
            "--always-approve",
            "--verbatim",
            "-p",
            prompt,
        ]

    if cli == "opencode":
        return "opencode", ["run", "--format", "json", "--auto", prompt]

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
    "grok": {
        "read-only": ["--permission-mode", "dontAsk"],
        "safe-edit": ["--permission-mode", "auto"],
        "yolo": ["--permission-mode", "bypassPermissions"],
    },
    # OpenCode permissions are supplied through OPENCODE_PERMISSION in
    # _build_opencode_args; empty argv mappings keep the shared validation and
    # configuration-sync checks intact.
    "opencode": {
        "read-only": [],
        "safe-edit": [],
        "yolo": [],
    },
}

# glm drives the claude binary, so its approval flags are identical to claude's.
_PERMISSION_MAPPING["glm"] = _PERMISSION_MAPPING["claude"]


def permission_flags(cli: str, permission: str) -> list:
    """Map permission level to CLI-specific flags. Fails fast on unknown values."""
    try:
        return list(_PERMISSION_MAPPING[cli][permission])
    except KeyError as e:
        raise ValueError(f"No permission mapping for cli={cli!r}, permission={permission!r}") from e


def _invocation_flags(inv: AgentInvocation) -> list:
    """Build flags shared by every backend invocation."""
    flags = permission_flags(inv.cli, inv.permission)
    if inv.model:
        flags.extend(["--model", inv.model])
    return flags


def _concatenated_args(
    inv: AgentInvocation, perm_flags: list, env: dict | None
) -> tuple[str, list, dict | None]:
    """Fallback: concatenate system context into the user prompt argument.

    Used when a CLI lacks a native system-prompt mechanism we can target.
    """
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
    """Use OpenCode with optional model override and non-interactive permissions."""
    perm = _invocation_flags(inv)
    formatted_prompt = format_concatenated_prompt(inv.system_context, inv.prompt)
    command, base_args = build_command(inv.cli, formatted_prompt)
    env_override = {"OPENCODE_PERMISSION": json.dumps(_OPENCODE_PERMISSION_MAPPING[inv.permission])}
    return command, perm + base_args, env_override


# GLM's Anthropic-compatible endpoint (Z.ai). Constant, not user-facing.
_GLM_BASE_URL = "https://api.z.ai/api/anthropic"


def _build_glm_args(inv: AgentInvocation) -> tuple[str, list, dict | None]:
    """Drive the claude binary against a GLM (Z.ai) endpoint.

    Two deliberate differences from _build_claude_args:

    - ``--system-prompt`` (full replace), not ``--append-system-prompt``: GLM
      should run on the agent definition alone, not layered on top of Claude
      Code's harness system prompt (which is tuned for Claude, not GLM).
    - Env injection routes the claude binary to Z.ai. The secret is delivered
      via env (never argv, so it stays out of ``ps`` output). An inherited
      ``ANTHROPIC_API_KEY`` is removed from the child env (mapped to ``None``):
      if both ``ANTHROPIC_API_KEY`` (x-api-key) and our ``ANTHROPIC_AUTH_TOKEN``
      (Bearer) reach the claude binary, auth conflicts and the Anthropic key can
      be sent to Z.ai, producing a 401 — the opaque failure this backend exists
      to avoid.

    Raises ValueError (surfaced to the orchestrating LLM as a config error) when
    the Z.ai token is absent or blank — proceeding would only produce an opaque
    401 from Z.ai buried in the stream, which the caller cannot act on. The
    message is phrased in positive-imperative form so the orchestrating LLM has
    a single clear next action (BP-001).
    """
    api_key = os.environ.get("CLI_API_KEY")
    if not api_key or not api_key.strip():
        raise ValueError(
            "GLM backend needs a Z.ai API token in the CLI_API_KEY environment "
            "variable, which is currently unset. Ask the user to set CLI_API_KEY "
            "to their Z.ai token and re-run, then wait for them. The same call "
            "keeps failing until CLI_API_KEY is set, so treat this run as finished."
        )
    perm = _invocation_flags(inv)
    system_prompt = f"cwd: {inv.cwd}\n\n{inv.system_context}"
    command, base_args = build_command(inv.cli, inv.prompt)
    env_override = {
        "ANTHROPIC_BASE_URL": _GLM_BASE_URL,
        "ANTHROPIC_AUTH_TOKEN": api_key,
        # None = delete from child env (see _build_proc_env). Strips any
        # inherited Anthropic key so it cannot be sent to the Z.ai endpoint.
        "ANTHROPIC_API_KEY": None,
    }
    return command, perm + ["--system-prompt", system_prompt] + base_args, env_override


def _build_cursor_args(inv: AgentInvocation) -> tuple[str, list, dict | None]:
    perm = _invocation_flags(inv)
    # Forward CLI_API_KEY (skill contract) as CURSOR_API_KEY (cursor's native
    # env). Passing via env keeps the secret out of `ps` output. Cursor can
    # also run from its own logged-in state, so a missing key is not an error.
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
    """Dispatch to the per-CLI argument builder.

    Returns ``(command, args, env_override_or_None)``.
    """
    try:
        builder = _BUILDERS[inv.cli]
    except KeyError as e:
        raise ValueError(f"Unknown CLI: {inv.cli}") from e
    return builder(inv)
