"""Shared constants and formatting helpers for the sub-agent runner."""

from __future__ import annotations

DEFAULT_TIMEOUT_MS = 600000
SUPPORTED_CLIS = (
    "codex",
    "claude",
    "cursor-agent",
    "glm",
    "grok",
    "gemini",
    "opencode",
)
SUPPORTED_CLIS_HELP = ", ".join(SUPPORTED_CLIS)


def format_concatenated_prompt(system_context: str, prompt: str) -> str:
    """Format fallback prompts for CLIs without a native system prompt hook."""
    return f"[System Context]\n{system_context}\n\n[User Prompt]\n{prompt}"
