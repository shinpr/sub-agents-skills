"""Tests for _builder: per-CLI command construction and permission mapping."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from _builder import (
    _BACKEND_SPECS,
    AgentInvocation,
    build_command,
    build_invocation_args,
    effort_flags,
    permission_flags,
)
from _constants import SUPPORTED_CLIS
from _loader import PERMISSION_VALUES


def _inv(cli: str, **kw: object) -> AgentInvocation:
    defaults = {
        "cli": cli,
        "system_context": "Agent definition",
        "prompt": "User task",
        "cwd": "/test/cwd",
    }
    defaults.update(kw)
    return AgentInvocation(**defaults)


def _credential_env(cli: str) -> dict[str, str]:
    if cli == "glm":
        return {"GLM_API_KEY": "test-key"}
    if cli == "kimi":
        return {"KIMI_API_KEY": "test-key"}
    return {}


class TestBuildCommand:
    def test_supported_cli_configuration_is_in_sync(self) -> None:
        assert set(_BACKEND_SPECS) == set(SUPPORTED_CLIS)
        assert all(
            set(spec.permissions) == set(PERMISSION_VALUES) for spec in _BACKEND_SPECS.values()
        )

    def test_codex_returns_exec_command_with_json_flag(self) -> None:
        cmd, args = build_command("codex", "test prompt")
        assert cmd == "codex"
        assert args == ["exec", "--json", "--skip-git-repo-check", "test prompt"]

    def test_claude_returns_streaming_json_command(self) -> None:
        cmd, args = build_command("claude", "test prompt")
        assert cmd == "claude"
        assert args == ["--output-format", "stream-json", "--verbose", "-p", "test prompt"]

    def test_cursor_returns_json_command(self) -> None:
        cmd, args = build_command("cursor-agent", "test prompt")
        assert cmd == "cursor-agent"
        assert args == ["--output-format", "json", "-p", "test prompt"]

    def test_cursor_argv_never_carries_api_key(self) -> None:
        """Even with CLI_API_KEY set, the secret must not appear in argv."""
        with patch.dict("os.environ", {"CLI_API_KEY": "test-key"}):
            cmd, args = build_command("cursor-agent", "test prompt")
            assert "--api-key" not in args
            assert "-a" not in args
            assert "test-key" not in args

    def test_gemini_returns_streaming_json_command(self) -> None:
        cmd, args = build_command("gemini", "test prompt")
        assert cmd == "gemini"
        assert args == [
            "--skip-trust",
            "--output-format",
            "stream-json",
            "-p",
            "test prompt",
        ]

    def test_antigravity_returns_streaming_json_command(self) -> None:
        cmd, args = build_command("antigravity", "test prompt")
        assert cmd == "agy"
        assert args == ["--output-format", "stream-json", "-p", "test prompt"]

    def test_grok_returns_json_command_with_turn_budget(self) -> None:
        cmd, args = build_command("grok", "test prompt")
        assert cmd == "grok"
        assert args == [
            "--output-format",
            "json",
            "--verbatim",
            "-p",
            "test prompt",
        ]

    def test_glm_reuses_claude_binary_and_argv(self) -> None:
        # glm runs the claude binary, so build_command returns the claude shape.
        cmd, args = build_command("glm", "test prompt")
        assert cmd == "claude"
        assert args == ["--output-format", "stream-json", "--verbose", "-p", "test prompt"]

    def test_kimi_reuses_claude_binary_and_argv(self) -> None:
        cmd, args = build_command("kimi", "test prompt")
        assert cmd == "claude"
        assert args == ["--output-format", "stream-json", "--verbose", "-p", "test prompt"]

    def test_opencode_returns_json_run_command(self) -> None:
        cmd, args = build_command("opencode", "test prompt")
        assert cmd == "opencode"
        assert args == ["run", "--format", "json", "--auto", "test prompt"]

    def test_command_code_returns_headless_json_command(self) -> None:
        cmd, args = build_command("command-code", "test prompt")
        assert cmd == "command-code"
        assert args == [
            "--output-format",
            "json",
            "--trust",
            "--no-session",
            "--skip-onboarding",
            "-p",
            "test prompt",
        ]

    def test_unknown_cli_raises_error(self) -> None:
        with pytest.raises(ValueError, match="Unsupported CLI"):
            build_command("unknown-cli", "test prompt")


class TestBuildInvocationArgs:
    """Per-CLI argument assembly via build_invocation_args."""

    @pytest.mark.parametrize(
        ("cli", "model"),
        [
            ("codex", "gpt-5.4-mini"),
            ("claude", "sonnet"),
            ("cursor-agent", "gpt-5"),
            ("glm", "glm-4.7"),
            ("kimi", "kimi-for-coding"),
            ("grok", "grok-code-fast-1"),
            ("gemini", "gemini-3-flash-preview"),
            ("antigravity", "gemini-3.7-flash-high"),
            ("opencode", "test-provider/test-model"),
            ("command-code", "claude-sonnet-4-6"),
        ],
    )
    def test_model_is_forwarded_to_every_backend(self, cli: str, model: str) -> None:
        env = _credential_env(cli)
        with patch.dict(os.environ, env, clear=bool(env)):
            process = build_invocation_args(_inv(cli, model=model))
        model_idx = process.args.index("--model")
        assert process.args[model_idx + 1] == model
        assert model_idx < len(process.args) - 1

    @pytest.mark.parametrize("cli", SUPPORTED_CLIS)
    def test_model_is_omitted_when_unspecified(self, cli: str) -> None:
        env = _credential_env(cli)
        with patch.dict(os.environ, env, clear=bool(env)):
            process = build_invocation_args(_inv(cli))
        assert "--model" not in process.args

    @pytest.mark.parametrize(
        ("cli", "effort", "expected_pair"),
        [
            ("codex", "xhigh", ("-c", 'model_reasoning_effort="xhigh"')),
            ("claude", "high", ("--effort", "high")),
            ("glm", "max", ("--effort", "max")),
            ("kimi", "high", ("--effort", "high")),
            ("grok", "high", ("--reasoning-effort", "high")),
            ("antigravity", "high", ("--effort", "high")),
            ("opencode", "vendor-level", ("--variant", "vendor-level")),
            ("command-code", "high", ("--effort", "high")),
        ],
    )
    def test_effort_is_forwarded_without_value_validation(
        self, cli: str, effort: str, expected_pair: tuple[str, str]
    ) -> None:
        env = _credential_env(cli)
        with patch.dict(os.environ, env, clear=bool(env)):
            process = build_invocation_args(_inv(cli, effort=effort))
        assert expected_pair in zip(process.args, process.args[1:])

    @pytest.mark.parametrize("cli", SUPPORTED_CLIS)
    def test_effort_is_omitted_when_unspecified(self, cli: str) -> None:
        env = _credential_env(cli)
        with patch.dict(os.environ, env, clear=bool(env)):
            process = build_invocation_args(_inv(cli))
        assert "--effort" not in process.args
        assert "--reasoning-effort" not in process.args
        assert "--variant" not in process.args
        assert not any(arg.startswith("model_reasoning_effort=") for arg in process.args)

    @pytest.mark.parametrize("cli", ["cursor-agent", "gemini"])
    def test_unsupported_effort_fails_instead_of_being_ignored(self, cli: str) -> None:
        with pytest.raises(ValueError, match=rf"selected backend: '{cli}'"):
            build_invocation_args(_inv(cli, effort="high"))

    def test_codex_effort_is_safely_encoded_as_toml_string(self) -> None:
        assert effort_flags("codex", 'high" -c unsafe=true') == [
            "-c",
            'model_reasoning_effort="high\\" -c unsafe=true"',
        ]

    def test_claude_uses_append_system_prompt_flag(self) -> None:
        process = build_invocation_args(_inv("claude"))
        assert process.command == "claude"
        assert "--append-system-prompt" in process.args
        sp_idx = process.args.index("--append-system-prompt")
        system_prompt_value = process.args[sp_idx + 1]
        assert "cwd: /test/cwd" in system_prompt_value
        assert "Agent definition" in system_prompt_value
        assert "-p" in process.args
        p_idx = process.args.index("-p")
        assert process.args[p_idx + 1] == "User task"
        assert process.env_override is None

    def test_gemini_uses_agent_file_for_system_md(self) -> None:
        process = build_invocation_args(_inv("gemini", agent_file="/path/to/agent.md"))
        assert process.command == "gemini"
        p_idx = process.args.index("-p")
        assert process.args[p_idx + 1] == "User task"
        assert process.env_override == {"GEMINI_SYSTEM_MD": "/path/to/agent.md"}

    def test_gemini_without_agent_file_concatenates(self) -> None:
        process = build_invocation_args(_inv("gemini"))
        assert process.command == "gemini"
        p_idx = process.args.index("-p")
        prompt_arg = process.args[p_idx + 1]
        assert "[System Context]" in prompt_arg
        assert "Agent definition" in prompt_arg
        assert process.env_override is None

    def test_antigravity_concatenates_agent_definition(self) -> None:
        process = build_invocation_args(_inv("antigravity"))
        assert process.command == "agy"
        p_idx = process.args.index("-p")
        prompt_arg = process.args[p_idx + 1]
        assert "[System Context]" in prompt_arg
        assert "Agent definition" in prompt_arg
        assert "[User Prompt]" in prompt_arg
        assert "User task" in prompt_arg

    def test_codex_concatenates_prompt_even_when_agent_file_given(self) -> None:
        process = build_invocation_args(_inv("codex", agent_file="/path/to/agent.md"))
        assert process.command == "codex"
        assert not any("model_instructions_file" in arg for arg in process.args)
        prompt_arg = process.args[-1]
        assert "[System Context]" in prompt_arg
        assert "Agent definition" in prompt_arg
        assert "[User Prompt]" in prompt_arg
        assert "User task" in prompt_arg
        assert process.env_override is None

    def test_codex_falls_back_to_concatenation_without_agent_file(self) -> None:
        process = build_invocation_args(_inv("codex"))
        assert process.command == "codex"
        prompt_arg = process.args[-1]
        assert "[System Context]" in prompt_arg
        assert "Agent definition" in prompt_arg
        assert "[User Prompt]" in prompt_arg
        assert "User task" in prompt_arg
        assert process.env_override is None

    def test_grok_concatenates_prompt_and_sets_cwd(self) -> None:
        process = build_invocation_args(_inv("grok"))
        assert process.command == "grok"
        assert "--system-prompt-override" not in process.args
        assert "--cwd" in process.args
        cwd_idx = process.args.index("--cwd")
        assert process.args[cwd_idx + 1] == "/test/cwd"
        assert "-p" in process.args
        p_idx = process.args.index("-p")
        prompt_arg = process.args[p_idx + 1]
        assert "[System Context]" in prompt_arg
        assert "Agent definition" in prompt_arg
        assert "[User Prompt]" in prompt_arg
        assert "User task" in prompt_arg
        assert process.env_override is None

    def test_cursor_concatenates_prompt(self) -> None:
        env_no_key = {
            k: v for k, v in os.environ.items() if k not in {"CURSOR_API_KEY", "CLI_API_KEY"}
        }
        with patch.dict("os.environ", env_no_key, clear=True):
            process = build_invocation_args(_inv("cursor-agent"))
        assert process.command == "cursor-agent"
        p_idx = process.args.index("-p")
        prompt_arg = process.args[p_idx + 1]
        assert "[System Context]" in prompt_arg
        assert "Agent definition" in prompt_arg
        assert process.env_override is None

    def test_cursor_removes_legacy_api_key_instead_of_forwarding_it(self) -> None:
        with patch.dict("os.environ", {"CLI_API_KEY": "sk-secret"}, clear=True):
            process = build_invocation_args(_inv("cursor-agent"))
        assert process.command == "cursor-agent"
        assert "sk-secret" not in process.args
        assert "--api-key" not in process.args
        assert "-a" not in process.args
        assert process.env_override == {"CLI_API_KEY": None}

    def test_cursor_prefers_provider_specific_api_key(self) -> None:
        with patch.dict(
            "os.environ",
            {"CURSOR_API_KEY": "cursor-secret", "CLI_API_KEY": "legacy-secret"},
            clear=True,
        ):
            process = build_invocation_args(_inv("cursor-agent"))
        assert process.env_override == {
            "CURSOR_API_KEY": "cursor-secret",
            "CLI_API_KEY": None,
        }
        assert "cursor-secret" not in process.args
        assert "legacy-secret" not in process.args

    def test_glm_uses_replace_system_prompt_and_injects_zai_env(self) -> None:
        with patch.dict("os.environ", {"GLM_API_KEY": "zai-secret"}, clear=True):
            process = build_invocation_args(_inv("glm"))
        assert process.command == "claude"
        # Full replace, NOT append — GLM runs on the agent def alone.
        assert "--system-prompt" in process.args
        assert "--append-system-prompt" not in process.args
        sp_idx = process.args.index("--system-prompt")
        system_prompt_value = process.args[sp_idx + 1]
        assert "cwd: /test/cwd" in system_prompt_value
        assert "Agent definition" in system_prompt_value
        p_idx = process.args.index("-p")
        assert process.args[p_idx + 1] == "User task"
        # Endpoint + credential routed via env; secret never in argv.
        # ANTHROPIC_API_KEY is mapped to None so _build_proc_env strips any
        # inherited Anthropic key from the child env (see executor tests).
        assert process.env_override == {
            "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
            "ANTHROPIC_AUTH_TOKEN": "zai-secret",
            "ANTHROPIC_API_KEY": None,
            "CLI_API_KEY": None,
        }
        assert "zai-secret" not in process.args

    def test_glm_strips_inherited_anthropic_api_key(self) -> None:
        # Even when the parent process has a real ANTHROPIC_API_KEY, the glm
        # override marks it for removal (None) so it never reaches Z.ai.
        with patch.dict(
            "os.environ",
            {"GLM_API_KEY": "zai-secret", "ANTHROPIC_API_KEY": "sk-ant-real"},
            clear=True,
        ):
            process = build_invocation_args(_inv("glm"))
        assert process.env_override["ANTHROPIC_API_KEY"] is None
        assert process.env_override["ANTHROPIC_AUTH_TOKEN"] == "zai-secret"

    def test_glm_prefers_provider_specific_api_key(self) -> None:
        with patch.dict(
            "os.environ",
            {"GLM_API_KEY": "zai-primary", "CLI_API_KEY": "legacy-secret"},
            clear=True,
        ):
            process = build_invocation_args(_inv("glm"))
        assert process.env_override["ANTHROPIC_AUTH_TOKEN"] == "zai-primary"
        assert process.env_override["CLI_API_KEY"] is None
        assert "zai-primary" not in process.args
        assert "legacy-secret" not in process.args

    @pytest.mark.parametrize("legacy_value", ["legacy-secret", ""])
    def test_glm_legacy_api_key_raises_migration_guidance_without_exposing_value(
        self, legacy_value: str
    ) -> None:
        with patch.dict("os.environ", {"CLI_API_KEY": legacy_value}, clear=True):
            # PT011: the full message is asserted with == below.
            with pytest.raises(ValueError) as exc_info:  # noqa: PT011
                build_invocation_args(_inv("glm"))
        assert str(exc_info.value) == (
            "GLM configuration error: CLI_API_KEY is set but no longer supported. "
            "Set GLM_API_KEY to a valid Z.ai API token and retry."
        )
        if legacy_value:
            assert legacy_value not in str(exc_info.value)

    @pytest.mark.parametrize("env", [{}, {"GLM_API_KEY": "   "}])
    def test_glm_missing_key_raises_actionable_config_error(self, env: dict[str, str]) -> None:
        with patch.dict("os.environ", env, clear=True):
            # PT011: the full message is asserted with == below.
            with pytest.raises(ValueError) as exc_info:  # noqa: PT011
                build_invocation_args(_inv("glm"))
        assert str(exc_info.value) == (
            "GLM configuration error: GLM_API_KEY is unset or blank. "
            "A Z.ai API token is required before retrying."
        )

    def test_kimi_uses_replace_system_prompt_and_injects_provider_env(self) -> None:
        with patch.dict("os.environ", {"KIMI_API_KEY": "kimi-secret"}, clear=True):
            process = build_invocation_args(_inv("kimi"))
        assert process.command == "claude"
        assert "--system-prompt" in process.args
        assert "--append-system-prompt" not in process.args
        sp_idx = process.args.index("--system-prompt")
        assert "cwd: /test/cwd" in process.args[sp_idx + 1]
        assert "Agent definition" in process.args[sp_idx + 1]
        p_idx = process.args.index("-p")
        assert process.args[p_idx + 1] == "User task"
        assert process.env_override == {
            "ANTHROPIC_BASE_URL": "https://api.kimi.com/coding/",
            "ANTHROPIC_API_KEY": "kimi-secret",
            "ANTHROPIC_AUTH_TOKEN": None,
            "CLI_API_KEY": None,
        }
        assert "kimi-secret" not in process.args

    def test_kimi_prefers_provider_specific_api_key_and_strips_auth_token(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "KIMI_API_KEY": "kimi-primary",
                "CLI_API_KEY": "legacy-secret",
                "ANTHROPIC_AUTH_TOKEN": "anthropic-secret",
            },
            clear=True,
        ):
            process = build_invocation_args(_inv("kimi"))
        assert process.env_override["ANTHROPIC_API_KEY"] == "kimi-primary"
        assert process.env_override["ANTHROPIC_AUTH_TOKEN"] is None
        assert process.env_override["CLI_API_KEY"] is None
        assert "kimi-primary" not in process.args
        assert "legacy-secret" not in process.args

    @pytest.mark.parametrize("legacy_value", ["legacy-secret", ""])
    def test_kimi_legacy_api_key_raises_migration_guidance_without_exposing_value(
        self, legacy_value: str
    ) -> None:
        with patch.dict(
            "os.environ",
            {"KIMI_API_KEY": "   ", "CLI_API_KEY": legacy_value},
            clear=True,
        ):
            # PT011: the full message is asserted with == below.
            with pytest.raises(ValueError) as exc_info:  # noqa: PT011
                build_invocation_args(_inv("kimi"))
        assert str(exc_info.value) == (
            "Kimi configuration error: CLI_API_KEY is set but no longer supported. "
            "Set KIMI_API_KEY to a valid Kimi API key and retry."
        )
        if legacy_value:
            assert legacy_value not in str(exc_info.value)

    @pytest.mark.parametrize("env", [{}, {"KIMI_API_KEY": "   "}])
    def test_kimi_missing_key_raises_actionable_config_error(self, env: dict[str, str]) -> None:
        with patch.dict("os.environ", env, clear=True):
            # PT011: the full message is asserted with == below.
            with pytest.raises(ValueError) as exc_info:  # noqa: PT011
                build_invocation_args(_inv("kimi"))
        assert str(exc_info.value) == (
            "Kimi configuration error: KIMI_API_KEY is unset or blank. "
            "A Kimi API key is required before retrying."
        )

    @pytest.mark.parametrize(
        ("permission", "expected"),
        [
            (
                "read-only",
                {
                    "edit": "deny",
                    "task": "deny",
                    "external_directory": "deny",
                    "question": "deny",
                },
            ),
            (
                "safe-edit",
                {
                    "edit": "allow",
                    "bash": "allow",
                    "task": "deny",
                    "external_directory": "deny",
                    "question": "deny",
                },
            ),
            ("yolo", "allow"),
        ],
    )
    def test_opencode_uses_configured_model_and_permission_env(
        self, permission: str, expected: str
    ) -> None:
        process = build_invocation_args(_inv("opencode", permission=permission))
        assert process.command == "opencode"
        assert process.args[:4] == ["run", "--format", "json", "--auto"]
        assert "--model" not in process.args
        assert "[System Context]" in process.args[-1]
        assert "Agent definition" in process.args[-1]
        assert json.loads(process.env_override["OPENCODE_PERMISSION"]) == expected

    def test_command_code_read_only_uses_plan_mode(self) -> None:
        process = build_invocation_args(_inv("command-code", permission="read-only"))
        assert process.command == "command-code"
        assert process.args[:2] == [
            "--permission-mode",
            "plan",
        ]
        assert process.args[-2:] == ["-p", process.args[-1]]
        assert "[System Context]" in process.args[-1]
        assert "Agent definition" in process.args[-1]
        assert process.env_override is None

    def test_command_code_safe_edit_enables_headless_tools_in_auto_accept_mode(self) -> None:
        process = build_invocation_args(_inv("command-code", permission="safe-edit"))
        assert process.command == "command-code"
        assert process.args[:3] == [
            "--yolo",
            "--permission-mode",
            "auto-accept",
        ]
        assert process.args[-2:] == ["-p", process.args[-1]]
        assert "[System Context]" in process.args[-1]
        assert "Agent definition" in process.args[-1]
        assert process.env_override is None

    def test_command_code_yolo_uses_native_yolo_mode(self) -> None:
        process = build_invocation_args(_inv("command-code", permission="yolo"))
        assert process.command == "command-code"
        assert process.args[0] == "--yolo"
        assert "--permission-mode" not in process.args
        assert process.args[-2:] == ["-p", process.args[-1]]

    def test_unknown_cli_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported CLI"):
            build_invocation_args(_inv("totally-fake-cli"))


class TestPermissionFlags:
    """Each CLI maps the 3 permission levels to its own flags."""

    def test_codex_flags(self) -> None:
        assert permission_flags("codex", "read-only") == ["-s", "read-only"]
        assert permission_flags("codex", "safe-edit") == [
            "-s",
            "workspace-write",
            "-c",
            "approval_policy=never",
        ]
        assert permission_flags("codex", "yolo") == ["--dangerously-bypass-approvals-and-sandbox"]

    def test_claude_flags(self) -> None:
        assert permission_flags("claude", "read-only") == ["--permission-mode", "plan"]
        assert permission_flags("claude", "safe-edit") == ["--permission-mode", "acceptEdits"]
        assert permission_flags("claude", "yolo") == ["--dangerously-skip-permissions"]

    def test_glm_flags_match_claude(self) -> None:
        # glm drives the claude binary, so approval flags are identical.
        assert permission_flags("glm", "read-only") == ["--permission-mode", "plan"]
        assert permission_flags("glm", "safe-edit") == ["--permission-mode", "acceptEdits"]
        assert permission_flags("glm", "yolo") == ["--dangerously-skip-permissions"]

    def test_kimi_flags_match_claude(self) -> None:
        assert permission_flags("kimi", "read-only") == ["--permission-mode", "plan"]
        assert permission_flags("kimi", "safe-edit") == ["--permission-mode", "acceptEdits"]
        assert permission_flags("kimi", "yolo") == ["--dangerously-skip-permissions"]

    def test_gemini_flags(self) -> None:
        # --skip-trust lives in build_command (headless prerequisite), not in
        # the permission mapping.
        assert permission_flags("gemini", "read-only") == ["--approval-mode", "plan"]
        assert permission_flags("gemini", "safe-edit") == ["--approval-mode", "auto_edit"]
        assert permission_flags("gemini", "yolo") == ["-y"]

    def test_antigravity_flags(self) -> None:
        assert permission_flags("antigravity", "read-only") == ["--mode", "plan", "--sandbox"]
        assert permission_flags("antigravity", "safe-edit") == [
            "--mode",
            "accept-edits",
            "--sandbox",
        ]
        assert permission_flags("antigravity", "yolo") == ["--dangerously-skip-permissions"]

    def test_cursor_flags(self) -> None:
        assert permission_flags("cursor-agent", "read-only") == [
            "--mode",
            "plan",
            "--sandbox",
            "enabled",
        ]
        assert permission_flags("cursor-agent", "safe-edit") == [
            "--trust",
            "--sandbox",
            "enabled",
        ]
        assert permission_flags("cursor-agent", "yolo") == ["-f", "--trust"]

    def test_grok_flags(self) -> None:
        assert permission_flags("grok", "read-only") == [
            "--permission-mode",
            "bypassPermissions",
            "--sandbox",
            "read-only",
        ]
        assert permission_flags("grok", "safe-edit") == [
            "--permission-mode",
            "bypassPermissions",
            "--sandbox",
            "workspace",
        ]
        assert permission_flags("grok", "yolo") == [
            "--permission-mode",
            "bypassPermissions",
            "--sandbox",
            "off",
        ]

    def test_opencode_permissions_are_environment_only(self) -> None:
        assert permission_flags("opencode", "read-only") == []
        assert permission_flags("opencode", "safe-edit") == []
        assert permission_flags("opencode", "yolo") == []

    def test_command_code_read_only_permission_uses_plan(self) -> None:
        assert permission_flags("command-code", "read-only") == [
            "--permission-mode",
            "plan",
        ]

    def test_command_code_safe_edit_permission_uses_yolo_with_auto_accept(self) -> None:
        assert permission_flags("command-code", "safe-edit") == [
            "--yolo",
            "--permission-mode",
            "auto-accept",
        ]

    def test_command_code_yolo_permission_uses_native_yolo(self) -> None:
        assert permission_flags("command-code", "yolo") == ["--yolo"]

    def test_unknown_cli_raises(self) -> None:
        """Unknown CLI in permission mapping is a programmer error — fail fast."""
        with pytest.raises(ValueError, match="No permission mapping"):
            permission_flags("unknown", "safe-edit")

    def test_unknown_permission_raises(self) -> None:
        """Unknown permission level is a programmer error — fail fast."""
        with pytest.raises(ValueError, match="No permission mapping"):
            permission_flags("codex", "weird")


class TestPermissionAppliedToCommand:
    """End-to-end: permission level should produce the right CLI flags in args."""

    def test_codex_safe_edit_flags_in_args(self) -> None:
        process = build_invocation_args(
            AgentInvocation(
                cli="codex",
                prompt="Task",
                cwd="/test/cwd",
                system_context="Agent",
                agent_file="/path/to/agent.md",
                permission="safe-edit",
            )
        )
        assert "-s" in process.args
        s_idx = process.args.index("-s")
        assert process.args[s_idx + 1] == "workspace-write"
        assert "approval_policy=never" in process.args

    def test_claude_yolo_flag_in_args(self) -> None:
        process = build_invocation_args(
            AgentInvocation(
                cli="claude",
                prompt="Task",
                cwd="/test/cwd",
                system_context="Agent",
                permission="yolo",
            )
        )
        assert "--dangerously-skip-permissions" in process.args

    def test_gemini_read_only_flags_in_args(self) -> None:
        process = build_invocation_args(
            AgentInvocation(
                cli="gemini",
                prompt="Task",
                cwd="/test/cwd",
                system_context="Agent",
                agent_file="/path/to/agent.md",
                permission="read-only",
            )
        )
        assert "--approval-mode" in process.args
        idx = process.args.index("--approval-mode")
        assert process.args[idx + 1] == "plan"

    @pytest.mark.parametrize("permission", ["read-only", "safe-edit"])
    def test_cursor_non_yolo_modes_enable_sandbox(self, permission: str) -> None:
        process = build_invocation_args(
            AgentInvocation(
                cli="cursor-agent",
                prompt="Task",
                cwd="/test/cwd",
                system_context="Agent",
                permission=permission,
            )
        )
        sandbox_idx = process.args.index("--sandbox")
        assert process.args[sandbox_idx + 1] == "enabled"

    def test_grok_safe_edit_in_args(self) -> None:
        process = build_invocation_args(
            AgentInvocation(
                cli="grok",
                prompt="Task",
                cwd="/test/cwd",
                system_context="Agent",
                permission="safe-edit",
            )
        )
        assert "--sandbox" in process.args
        idx = process.args.index("--sandbox")
        assert process.args[idx + 1] == "workspace"
