"""Tests for _builder: per-CLI command construction, permission mapping, TOML escape."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from _builder import (
    AgentInvocation,
    build_command,
    build_invocation_args,
    permission_flags,
)


def _inv(cli, **kw):
    defaults = {
        "cli": cli,
        "system_context": "Agent definition",
        "prompt": "User task",
        "cwd": "/test/cwd",
    }
    defaults.update(kw)
    return AgentInvocation(**defaults)


class TestBuildCommand:
    def test_codex_returns_exec_command_with_json_flag(self):
        cmd, args = build_command("codex", "test prompt")
        assert cmd == "codex"
        assert args == ["exec", "--json", "--skip-git-repo-check", "test prompt"]

    def test_claude_returns_streaming_json_command(self):
        cmd, args = build_command("claude", "test prompt")
        assert cmd == "claude"
        assert args == ["--output-format", "stream-json", "--verbose", "-p", "test prompt"]

    def test_cursor_returns_json_command(self):
        cmd, args = build_command("cursor-agent", "test prompt")
        assert cmd == "cursor-agent"
        assert args == ["--output-format", "json", "-p", "test prompt"]

    def test_cursor_argv_never_carries_api_key(self):
        """Even with CLI_API_KEY set, the secret must not appear in argv.

        Forwarding happens via env (CURSOR_API_KEY), see TestBuildInvocationArgs.
        """
        with patch.dict("os.environ", {"CLI_API_KEY": "test-key"}):
            cmd, args = build_command("cursor-agent", "test prompt")
            assert "--api-key" not in args
            assert "-a" not in args
            assert "test-key" not in args

    def test_gemini_returns_streaming_json_command(self):
        cmd, args = build_command("gemini", "test prompt")
        assert cmd == "gemini"
        assert args == [
            "--skip-trust",
            "--output-format",
            "stream-json",
            "-p",
            "test prompt",
        ]

    def test_grok_returns_json_command_with_turn_budget(self):
        cmd, args = build_command("grok", "test prompt")
        assert cmd == "grok"
        assert args == [
            "--output-format",
            "json",
            "--always-approve",
            "--verbatim",
            "-p",
            "test prompt",
        ]

    def test_glm_reuses_claude_binary_and_argv(self):
        # glm runs the claude binary, so build_command returns the claude shape.
        cmd, args = build_command("glm", "test prompt")
        assert cmd == "claude"
        assert args == ["--output-format", "stream-json", "--verbose", "-p", "test prompt"]

    def test_unknown_cli_raises_error(self):
        with pytest.raises(ValueError, match="Unknown CLI"):
            build_command("unknown-cli", "test prompt")


class TestBuildInvocationArgs:
    """Per-CLI argument assembly via build_invocation_args."""

    def test_claude_uses_append_system_prompt_flag(self):
        cmd, args, env = build_invocation_args(_inv("claude"))
        assert cmd == "claude"
        assert "--append-system-prompt" in args
        sp_idx = args.index("--append-system-prompt")
        system_prompt_value = args[sp_idx + 1]
        assert "cwd: /test/cwd" in system_prompt_value
        assert "Agent definition" in system_prompt_value
        assert "-p" in args
        p_idx = args.index("-p")
        assert args[p_idx + 1] == "User task"
        assert env is None

    def test_gemini_uses_agent_file_for_system_md(self):
        cmd, args, env = build_invocation_args(_inv("gemini", agent_file="/path/to/agent.md"))
        assert cmd == "gemini"
        p_idx = args.index("-p")
        assert args[p_idx + 1] == "User task"
        assert env == {"GEMINI_SYSTEM_MD": "/path/to/agent.md"}

    def test_gemini_without_agent_file_concatenates(self):
        cmd, args, env = build_invocation_args(_inv("gemini"))
        assert cmd == "gemini"
        p_idx = args.index("-p")
        prompt_arg = args[p_idx + 1]
        assert "[System Context]" in prompt_arg
        assert "Agent definition" in prompt_arg
        assert env is None

    def test_codex_concatenates_prompt_even_when_agent_file_given(self):
        cmd, args, env = build_invocation_args(_inv("codex", agent_file="/path/to/agent.md"))
        assert cmd == "codex"
        assert not any("model_instructions_file" in arg for arg in args)
        prompt_arg = args[-1]
        assert "[System Context]" in prompt_arg
        assert "Agent definition" in prompt_arg
        assert "[User Prompt]" in prompt_arg
        assert "User task" in prompt_arg
        assert env is None

    def test_codex_falls_back_to_concatenation_without_agent_file(self):
        cmd, args, env = build_invocation_args(_inv("codex"))
        assert cmd == "codex"
        prompt_arg = args[-1]
        assert "[System Context]" in prompt_arg
        assert "Agent definition" in prompt_arg
        assert "[User Prompt]" in prompt_arg
        assert "User task" in prompt_arg
        assert env is None

    def test_grok_concatenates_prompt_and_sets_cwd(self):
        cmd, args, env = build_invocation_args(_inv("grok"))
        assert cmd == "grok"
        assert "--system-prompt-override" not in args
        assert "--cwd" in args
        cwd_idx = args.index("--cwd")
        assert args[cwd_idx + 1] == "/test/cwd"
        assert "-p" in args
        p_idx = args.index("-p")
        prompt_arg = args[p_idx + 1]
        assert "[System Context]" in prompt_arg
        assert "Agent definition" in prompt_arg
        assert "[User Prompt]" in prompt_arg
        assert "User task" in prompt_arg
        assert env is None

    def test_cursor_concatenates_prompt(self):
        env_no_key = {k: v for k, v in os.environ.items() if k != "CLI_API_KEY"}
        with patch.dict("os.environ", env_no_key, clear=True):
            cmd, args, env = build_invocation_args(_inv("cursor-agent"))
        assert cmd == "cursor-agent"
        p_idx = args.index("-p")
        prompt_arg = args[p_idx + 1]
        assert "[System Context]" in prompt_arg
        assert "Agent definition" in prompt_arg
        assert env is None

    def test_cursor_passes_api_key_via_env_not_argv(self):
        with patch.dict("os.environ", {"CLI_API_KEY": "sk-secret"}):
            cmd, args, env = build_invocation_args(_inv("cursor-agent"))
        assert cmd == "cursor-agent"
        assert "sk-secret" not in args
        assert "--api-key" not in args
        assert "-a" not in args
        assert env == {"CURSOR_API_KEY": "sk-secret"}

    def test_glm_uses_replace_system_prompt_and_injects_zai_env(self):
        with patch.dict("os.environ", {"CLI_API_KEY": "zai-secret"}):
            cmd, args, env = build_invocation_args(_inv("glm"))
        assert cmd == "claude"
        # Full replace, NOT append — GLM runs on the agent def alone.
        assert "--system-prompt" in args
        assert "--append-system-prompt" not in args
        sp_idx = args.index("--system-prompt")
        system_prompt_value = args[sp_idx + 1]
        assert "cwd: /test/cwd" in system_prompt_value
        assert "Agent definition" in system_prompt_value
        p_idx = args.index("-p")
        assert args[p_idx + 1] == "User task"
        # Endpoint + credential routed via env; secret never in argv.
        # ANTHROPIC_API_KEY is mapped to None so _build_proc_env strips any
        # inherited Anthropic key from the child env (see executor tests).
        assert env == {
            "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
            "ANTHROPIC_AUTH_TOKEN": "zai-secret",
            "ANTHROPIC_API_KEY": None,
        }
        assert "zai-secret" not in args

    def test_glm_strips_inherited_anthropic_api_key(self):
        # Even when the parent process has a real ANTHROPIC_API_KEY, the glm
        # override marks it for removal (None) so it never reaches Z.ai.
        with patch.dict("os.environ", {"CLI_API_KEY": "zai-secret", "ANTHROPIC_API_KEY": "sk-ant-real"}):
            _, _, env = build_invocation_args(_inv("glm"))
        assert env["ANTHROPIC_API_KEY"] is None
        assert env["ANTHROPIC_AUTH_TOKEN"] == "zai-secret"

    def test_glm_missing_key_raises_config_error(self):
        env_no_key = {k: v for k, v in os.environ.items() if k != "CLI_API_KEY"}
        with patch.dict("os.environ", env_no_key, clear=True):
            with pytest.raises(ValueError, match="CLI_API_KEY"):
                build_invocation_args(_inv("glm"))

    def test_glm_blank_key_raises_config_error(self):
        # Whitespace-only key must be treated as missing, not forwarded to Z.ai.
        with patch.dict("os.environ", {"CLI_API_KEY": "   "}):
            with pytest.raises(ValueError, match="CLI_API_KEY"):
                build_invocation_args(_inv("glm"))

    def test_unknown_cli_raises(self):
        with pytest.raises(ValueError, match="Unknown CLI"):
            build_invocation_args(_inv("totally-fake-cli"))


class TestPermissionFlags:
    """Each CLI maps the 3 permission levels to its own flags."""

    def test_codex_flags(self):
        assert permission_flags("codex", "read-only") == ["-s", "read-only"]
        assert permission_flags("codex", "safe-edit") == [
            "-s",
            "workspace-write",
            "-c",
            "approval_policy=never",
        ]
        assert permission_flags("codex", "yolo") == ["--dangerously-bypass-approvals-and-sandbox"]

    def test_claude_flags(self):
        assert permission_flags("claude", "read-only") == ["--permission-mode", "plan"]
        assert permission_flags("claude", "safe-edit") == ["--permission-mode", "acceptEdits"]
        assert permission_flags("claude", "yolo") == ["--dangerously-skip-permissions"]

    def test_glm_flags_match_claude(self):
        # glm drives the claude binary, so approval flags are identical.
        assert permission_flags("glm", "read-only") == ["--permission-mode", "plan"]
        assert permission_flags("glm", "safe-edit") == ["--permission-mode", "acceptEdits"]
        assert permission_flags("glm", "yolo") == ["--dangerously-skip-permissions"]

    def test_gemini_flags(self):
        # --skip-trust lives in build_command (headless prerequisite), not in
        # the permission mapping.
        assert permission_flags("gemini", "read-only") == ["--approval-mode", "plan"]
        assert permission_flags("gemini", "safe-edit") == ["--approval-mode", "auto_edit"]
        assert permission_flags("gemini", "yolo") == ["-y"]

    def test_cursor_flags(self):
        assert permission_flags("cursor-agent", "read-only") == ["--mode", "plan"]
        assert permission_flags("cursor-agent", "safe-edit") == ["--trust"]
        assert permission_flags("cursor-agent", "yolo") == ["-f", "--trust"]

    def test_grok_flags(self):
        assert permission_flags("grok", "read-only") == ["--permission-mode", "dontAsk"]
        assert permission_flags("grok", "safe-edit") == ["--permission-mode", "auto"]
        assert permission_flags("grok", "yolo") == ["--permission-mode", "bypassPermissions"]

    def test_unknown_cli_raises(self):
        """Unknown CLI in permission mapping is a programmer error — fail fast."""
        with pytest.raises(ValueError, match="No permission mapping"):
            permission_flags("unknown", "safe-edit")

    def test_unknown_permission_raises(self):
        """Unknown permission level is a programmer error — fail fast."""
        with pytest.raises(ValueError, match="No permission mapping"):
            permission_flags("codex", "weird")


class TestPermissionAppliedToCommand:
    """End-to-end: permission level should produce the right CLI flags in args."""

    def test_codex_safe_edit_flags_in_args(self):
        cmd, args, _ = build_invocation_args(
            AgentInvocation(
                cli="codex",
                prompt="Task",
                cwd="/test/cwd",
                system_context="Agent",
                agent_file="/path/to/agent.md",
                permission="safe-edit",
            )
        )
        assert "-s" in args
        s_idx = args.index("-s")
        assert args[s_idx + 1] == "workspace-write"
        assert "approval_policy=never" in args

    def test_claude_yolo_flag_in_args(self):
        _, args, _ = build_invocation_args(
            AgentInvocation(
                cli="claude",
                prompt="Task",
                cwd="/test/cwd",
                system_context="Agent",
                permission="yolo",
            )
        )
        assert "--dangerously-skip-permissions" in args

    def test_gemini_read_only_flags_in_args(self):
        _, args, _ = build_invocation_args(
            AgentInvocation(
                cli="gemini",
                prompt="Task",
                cwd="/test/cwd",
                system_context="Agent",
                agent_file="/path/to/agent.md",
                permission="read-only",
            )
        )
        assert "--approval-mode" in args
        idx = args.index("--approval-mode")
        assert args[idx + 1] == "plan"

    def test_cursor_safe_edit_in_args(self):
        _, args, _ = build_invocation_args(
            AgentInvocation(
                cli="cursor-agent",
                prompt="Task",
                cwd="/test/cwd",
                system_context="Agent",
                permission="safe-edit",
            )
        )
        assert "--trust" in args

    def test_grok_safe_edit_in_args(self):
        _, args, _ = build_invocation_args(
            AgentInvocation(
                cli="grok",
                prompt="Task",
                cwd="/test/cwd",
                system_context="Agent",
                permission="safe-edit",
            )
        )
        assert "--permission-mode" in args
        idx = args.index("--permission-mode")
        assert args[idx + 1] == "auto"
