"""Tests for _builder: per-CLI command construction and permission mapping."""

from __future__ import annotations

import json
import os
import subprocess
from unittest.mock import patch

import pytest
from _builder import (
    _BACKEND_SPECS,
    AgentInvocation,
    ProcessInvocation,
    _opencode_is_v2,
    build_command,
    build_invocation_args,
    effort_flags,
    permission_flags,
)
from _constants import SUPPORTED_CLIS
from _loader import DEFAULT_PERMISSION, PERMISSION_VALUES


# PLR0913: mirrors AgentInvocation's fields, so the count is the dataclass's.
def _inv(  # noqa: PLR0913
    cli: str,
    *,
    system_context: str = "Agent definition",
    prompt: str = "User task",
    cwd: str = "/test/cwd",
    agent_file: str | None = None,
    permission: str = DEFAULT_PERMISSION,
    model: str | None = None,
    effort: str | None = None,
) -> AgentInvocation:
    return AgentInvocation(
        cli=cli,
        prompt=prompt,
        cwd=cwd,
        system_context=system_context,
        agent_file=agent_file,
        permission=permission,
        model=model,
        effort=effort,
    )


def _env(process: ProcessInvocation) -> dict[str, str | None]:
    assert process.env_override is not None
    return process.env_override


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
            ("mcode", "provider/model"),
            ("pi", "provider/model"),
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
            ("mcode", "high", ("--effort", "high")),
            ("pi", "high", ("--thinking", "high")),
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

    def test_mcode_places_options_after_exec_and_prompt_after_separator(self) -> None:
        process = build_invocation_args(
            _inv("mcode", prompt="- list files", permission="read-only")
        )
        assert process.command == "mcode"
        assert process.args == [
            "exec",
            "--permission",
            "smart",
            "--append-system-prompt",
            "cwd: /test/cwd\n\nAgent definition",
            "--output-format",
            "json",
            "--",
            "- list files",
        ]

    def test_pi_read_only_limits_tools_and_places_prompt_after_separator(self) -> None:
        process = build_invocation_args(_inv("pi", prompt="- list files", permission="read-only"))
        assert process.command == "pi"
        assert process.args == [
            "--tools",
            "read,grep,find,ls",
            "--exclude-tools",
            "mcp__*",
            "--append-system-prompt",
            "cwd: /test/cwd\n\nAgent definition",
            "--no-session",
            "--mode",
            "json",
            "--print",
            "--",
            "- list files",
        ]

    def test_pi_prompt_starting_with_at_is_not_read_as_a_file(self) -> None:
        process = build_invocation_args(_inv("pi", prompt="@src/app.ts review"))
        assert process.args[-1] == " @src/app.ts review"

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

    def test_glm_prefers_provider_specific_api_key(self) -> None:
        with patch.dict(
            "os.environ",
            {"GLM_API_KEY": "zai-primary", "CLI_API_KEY": "legacy-secret"},
            clear=True,
        ):
            process = build_invocation_args(_inv("glm"))
        assert _env(process)["ANTHROPIC_AUTH_TOKEN"] == "zai-primary"
        assert _env(process)["CLI_API_KEY"] is None
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
        assert _env(process)["ANTHROPIC_API_KEY"] == "kimi-primary"
        assert _env(process)["ANTHROPIC_AUTH_TOKEN"] is None
        assert _env(process)["CLI_API_KEY"] is None
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
        permission_env = _env(process)["OPENCODE_PERMISSION"]
        assert permission_env is not None
        assert json.loads(permission_env) == expected

    def test_opencode_v1_places_model_and_variant_after_run(self) -> None:
        process = build_invocation_args(_inv("opencode", model="p/m", effort="high"))
        assert process.args[:5] == ["run", "--model", "p/m", "--variant", "high"]

    def test_opencode_working_directory_is_passed_through_pwd(self) -> None:
        process = build_invocation_args(_inv("opencode", cwd="/target/dir"))
        assert _env(process)["PWD"] == "/target/dir"

    def test_opencode_v2_uses_private_server_model_variant_and_config_permission(self) -> None:
        with patch("_builder._opencode_is_v2", return_value=True):
            process = build_invocation_args(
                _inv("opencode", model="p/m", effort="high", permission="read-only")
            )
        assert process.args[:-1] == [
            "run",
            "--standalone",
            "--model",
            "p/m#high",
            "--format",
            "json",
            "--auto",
        ]
        env = _env(process)
        assert "OPENCODE_PERMISSION" not in env
        config = env["OPENCODE_CONFIG_CONTENT"]
        assert config is not None
        assert json.loads(config) == {
            "permission": {
                "edit": "deny",
                "task": "deny",
                "external_directory": "deny",
                "question": "deny",
            }
        }

    def test_opencode_v2_effort_without_model_is_rejected(self) -> None:
        with patch("_builder._opencode_is_v2", return_value=True):
            with pytest.raises(ValueError, match="effort requires model"):
                build_invocation_args(_inv("opencode", effort="high"))

    def test_unknown_cli_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported CLI"):
            build_invocation_args(_inv("totally-fake-cli"))


class TestOpencodeVersionProbe:
    def test_v2_version_output_selects_v2(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "opencode v2.0.29\n", "")
        with patch("subprocess.run", return_value=completed):
            assert _opencode_is_v2() is True

    @pytest.mark.parametrize("stdout", ["1.18.20\n", "unexpected\n"])
    def test_v1_or_unreadable_version_output_counts_as_v1(self, stdout: str) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout, "")
        with patch("subprocess.run", return_value=completed):
            assert _opencode_is_v2() is False

    def test_missing_binary_counts_as_v1(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            assert _opencode_is_v2() is False


class TestPermissionFlags:
    def test_unknown_cli_raises(self) -> None:
        """Unknown CLI in permission mapping is a programmer error — fail fast."""
        with pytest.raises(ValueError, match="No permission mapping"):
            permission_flags("unknown", "safe-edit")

    def test_unknown_permission_raises(self) -> None:
        """Unknown permission level is a programmer error — fail fast."""
        with pytest.raises(ValueError, match="No permission mapping"):
            permission_flags("codex", "weird")

    def test_permission_flags_reach_the_command(self) -> None:
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
