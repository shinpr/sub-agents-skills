#!/usr/bin/env python3
"""Tests for run_subagent.py"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "sub-agents" / "scripts"))

from run_subagent import (
    StreamProcessor,
    _build_system_prompt_args,
    build_command,
    detect_caller_cli,
    execute_agent,
    extract_description,
    get_agents_dir,
    list_agents,
    load_agent,
    parse_frontmatter,
    resolve_cli,
)


class TestParseFrontmatter:
    def test_with_frontmatter(self):
        content = """---
run-agent: claude
---

# Agent Name

Body content here.
"""
        frontmatter, body = parse_frontmatter(content)
        assert frontmatter == {"run-agent": "claude"}
        assert "# Agent Name" in body
        assert "---" not in body

    def test_without_frontmatter(self):
        content = """# Agent Name

No frontmatter here.
"""
        frontmatter, body = parse_frontmatter(content)
        assert frontmatter == {}
        assert body == content


class TestLoadAgentRejectsInvalidNames:
    """load_agent should reject names that could escape the agents directory."""

    def _make_agents_dir(self, tmpdir):
        agents_dir = Path(tmpdir) / "agents"
        agents_dir.mkdir()
        (agents_dir / "valid-agent.md").write_text("# Valid\n\nA valid agent.")
        return str(agents_dir)

    # --- Valid names load successfully ---

    def test_loads_simple_hyphenated_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            _, context, _, file_path = load_agent(agents_dir, "valid-agent")
            assert "Valid" in context
            assert file_path.endswith("valid-agent.md")

    # --- Path traversal attempts are rejected ---

    def test_rejects_parent_directory_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, "../etc/passwd")

    def test_rejects_nested_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, "../../secret")

    def test_rejects_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, "/etc/passwd")

    def test_rejects_forward_slash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, "sub/agent")

    def test_rejects_backslash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, "sub\\agent")

    def test_rejects_empty_string(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, "")

    def test_rejects_shell_metacharacters(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, "agent;rm -rf")

    def test_rejects_leading_dot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, ".hidden")


class TestLoadAgent:
    def test_load_agent_md(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_file = Path(tmpdir) / "test-agent.md"
            agent_file.write_text("""---
run-agent: codex
---

# Test Agent

This is a test agent.

## Instructions
Do something.
""")
            run_agent, system_context, desc, file_path = load_agent(tmpdir, "test-agent")
            assert run_agent == "codex"
            assert "# Test Agent" in system_context
            assert "This is a test agent." in desc
            assert file_path.endswith("test-agent.md")

    def test_agent_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir, pytest.raises(FileNotFoundError):
            load_agent(tmpdir, "nonexistent")


class TestListAgents:
    def test_list_multiple_agents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "agent-a.md").write_text("# Agent A\n\nFirst agent.")
            (Path(tmpdir) / "agent-b.md").write_text("# Agent B\n\nSecond agent.")
            agents = list_agents(tmpdir)
            assert len(agents) == 2
            names = [a["name"] for a in agents]
            assert "agent-a" in names
            assert "agent-b" in names

    def test_list_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents = list_agents(tmpdir)
            assert agents == []

    def test_list_nonexistent_directory(self):
        agents = list_agents("/nonexistent/path")
        assert agents == []


class TestGetAgentsDir:
    def test_args_priority(self):
        result = get_agents_dir("/custom/path", "/some/cwd")
        assert result == "/custom/path"

    def test_env_priority(self):
        with patch.dict("os.environ", {"SUB_AGENTS_DIR": "/env/path"}):
            result = get_agents_dir(None, "/some/cwd")
            assert result == "/env/path"

    def test_cwd_fallback(self):
        result = get_agents_dir(None, "/some/cwd")
        assert result == "/some/cwd/.agents"


class TestResolveCli:
    def test_frontmatter_priority(self):
        assert resolve_cli("claude") == "claude"
        assert resolve_cli("codex") == "codex"

    def test_default_fallback(self):
        assert resolve_cli(None) == "codex"

    def test_invalid_frontmatter_uses_default(self):
        assert resolve_cli("invalid-cli") == "codex"


class TestStreamProcessor:
    def test_claude_result(self):
        processor = StreamProcessor()
        assert processor.process_line('{"type": "result", "result": "hello"}')
        result = processor.get_result()
        assert result["result"] == "hello"

    def test_gemini_stream(self):
        processor = StreamProcessor()
        assert not processor.process_line('{"type": "init"}')
        assert not processor.process_line(
            '{"type": "message", "role": "assistant", "content": "part1"}'
        )
        assert not processor.process_line(
            '{"type": "message", "role": "assistant", "content": "part2"}'
        )
        assert processor.process_line('{"type": "result", "status": "success"}')
        result = processor.get_result()
        assert result["result"] == "part1part2"

    def test_codex_stream(self):
        processor = StreamProcessor()
        assert not processor.process_line('{"type": "thread.started"}')
        assert not processor.process_line(
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "msg1"}}'
        )
        assert not processor.process_line(
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "msg2"}}'
        )
        assert processor.process_line('{"type": "turn.completed"}')
        result = processor.get_result()
        assert result["result"] == "msg1\nmsg2"


class TestBuildCommand:
    def test_codex_returns_exec_command_with_json_flag(self):
        """codex CLI should use 'exec --json' subcommand"""
        cmd, args = build_command("codex", "test prompt")
        assert cmd == "codex"
        assert args == ["exec", "--json", "test prompt"]

    def test_claude_returns_streaming_json_command(self):
        """claude CLI should output streaming JSON format"""
        cmd, args = build_command("claude", "test prompt")
        assert cmd == "claude"
        # Verify the command would produce streaming JSON output
        assert args == ["--output-format", "stream-json", "--verbose", "-p", "test prompt"]

    def test_cursor_returns_json_command(self):
        """cursor-agent CLI should output JSON format"""
        cmd, args = build_command("cursor-agent", "test prompt")
        assert cmd == "cursor-agent"
        assert args == ["--output-format", "json", "-p", "test prompt"]

    def test_cursor_with_api_key(self):
        """cursor-agent should include API key when env var is set"""
        with patch.dict("os.environ", {"CLI_API_KEY": "test-key"}):
            cmd, args = build_command("cursor-agent", "test prompt")
            assert "-a" in args
            assert "test-key" in args

    def test_gemini_returns_streaming_json_command(self):
        """gemini CLI should output streaming JSON format"""
        cmd, args = build_command("gemini", "test prompt")
        assert cmd == "gemini"
        assert args == ["--output-format", "stream-json", "-p", "test prompt"]

    def test_unknown_cli_raises_error(self):
        """Unknown CLI should raise ValueError"""
        with pytest.raises(ValueError, match="Unknown CLI"):
            build_command("unknown-cli", "test prompt")


class TestDetectCallerCli:
    def test_detects_claude_from_env(self):
        """Should detect claude when CLAUDE_CODE env var is set"""
        with patch.dict("os.environ", {"CLAUDE_CODE": "1"}, clear=False):
            assert detect_caller_cli() == "claude"

    def test_detects_codex_from_env(self):
        """Should detect codex when CODEX_CLI env var is set"""
        with patch.dict("os.environ", {"CODEX_CLI": "1"}, clear=False):
            assert detect_caller_cli() == "codex"

    def test_returns_none_when_no_indicator(self):
        """Should return None when no environment indicator is present"""
        with patch.dict("os.environ", {}, clear=True):
            result = detect_caller_cli()
            # May return None or detect from parent process
            assert result is None or result in {"claude", "cursor-agent", "codex", "gemini"}


class TestExtractDescription:
    def test_extracts_first_non_heading_line(self):
        """Should return first line that's not a heading"""
        body = "# Title\n\nThis is description.\n\nMore content."
        assert extract_description(body) == "This is description."

    def test_returns_empty_for_headings_only(self):
        """Should return empty string if only headings"""
        body = "# Title\n## Subtitle"
        assert extract_description(body) == ""

    def test_truncates_long_descriptions(self):
        """Should truncate descriptions over 100 chars"""
        long_line = "a" * 150
        body = f"# Title\n\n{long_line}"
        result = extract_description(body)
        assert len(result) == 100


class TestBuildSystemPromptArgs:
    """Tests for CLI-specific system prompt separation."""

    def test_claude_uses_append_system_prompt_flag(self):
        """Claude should use --append-system-prompt with cwd prefix"""
        cmd, args, env = _build_system_prompt_args(
            "claude", "Agent definition", "User task", "/test/cwd"
        )
        assert cmd == "claude"
        assert "--append-system-prompt" in args
        # System prompt should include cwd and agent definition
        sp_idx = args.index("--append-system-prompt")
        system_prompt_value = args[sp_idx + 1]
        assert "cwd: /test/cwd" in system_prompt_value
        assert "Agent definition" in system_prompt_value
        # User prompt should be separate
        assert "-p" in args
        p_idx = args.index("-p")
        assert args[p_idx + 1] == "User task"
        # No env override
        assert env is None

    def test_gemini_uses_agent_file_for_system_md(self):
        """Gemini should set GEMINI_SYSTEM_MD to the agent file path"""
        cmd, args, env = _build_system_prompt_args(
            "gemini",
            "Agent definition",
            "User task",
            "/test/cwd",
            agent_file="/path/to/agent.md",
        )
        assert cmd == "gemini"
        # User prompt should be in args
        assert "-p" in args
        p_idx = args.index("-p")
        assert args[p_idx + 1] == "User task"
        # Env should have GEMINI_SYSTEM_MD pointing to agent file
        assert env is not None
        assert env["GEMINI_SYSTEM_MD"] == "/path/to/agent.md"

    def test_gemini_without_agent_file_concatenates(self):
        """Gemini without agent_file should fall back to concatenation"""
        cmd, args, env = _build_system_prompt_args(
            "gemini",
            "Agent definition",
            "User task",
            "/test/cwd",
        )
        assert cmd == "gemini"
        p_idx = args.index("-p")
        prompt_arg = args[p_idx + 1]
        assert "[System Context]" in prompt_arg
        assert "Agent definition" in prompt_arg
        assert env is None

    def test_codex_concatenates_prompt(self):
        """Codex should concatenate system context and user prompt"""
        cmd, args, env = _build_system_prompt_args(
            "codex", "Agent definition", "User task", "/test/cwd"
        )
        assert cmd == "codex"
        prompt_arg = args[-1]
        assert "[System Context]" in prompt_arg
        assert "Agent definition" in prompt_arg
        assert "[User Prompt]" in prompt_arg
        assert "User task" in prompt_arg
        assert env is None

    def test_cursor_concatenates_prompt(self):
        """Cursor should concatenate system context and user prompt"""
        cmd, args, env = _build_system_prompt_args(
            "cursor-agent", "Agent definition", "User task", "/test/cwd"
        )
        assert cmd == "cursor-agent"
        p_idx = args.index("-p")
        prompt_arg = args[p_idx + 1]
        assert "[System Context]" in prompt_arg
        assert "Agent definition" in prompt_arg
        assert env is None


class TestExecuteAgent:
    def test_returns_error_when_cli_executable_not_found(self):
        """Should return error status when CLI executable is not in PATH"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "run_subagent.build_command",
                return_value=("nonexistent-cli-12345", ["arg1"]),
            ):
                result = execute_agent(
                    cli="codex",
                    system_context="test context",
                    prompt="test prompt",
                    cwd=tmpdir,
                    timeout=5000,
                )
                assert result["status"] == "error"
                assert result["exit_code"] == 127
                assert "not found" in result["error"].lower()

    def test_codex_formats_prompt_with_system_context(self):
        """Codex should combine system context and user prompt in single arg"""
        mock_process = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_process.communicate.return_value = ("", "")
        mock_process.returncode = 0

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
                execute_agent(
                    cli="codex",
                    system_context="System instructions",
                    prompt="User task",
                    cwd=tmpdir,
                    timeout=5000,
                )
                call_args = mock_popen.call_args[0][0]
                prompt_arg = call_args[-1]
                assert "[System Context]" in prompt_arg
                assert "System instructions" in prompt_arg
                assert "[User Prompt]" in prompt_arg
                assert "User task" in prompt_arg

    def test_gemini_passes_env_with_agent_file(self):
        """Gemini should set GEMINI_SYSTEM_MD env var to agent file path"""
        mock_process = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_process.communicate.return_value = ("", "")
        mock_process.returncode = 0

        with tempfile.TemporaryDirectory() as tmpdir:
            agent_file = os.path.join(tmpdir, "test-agent.md")
            with open(agent_file, "w") as f:
                f.write("# Agent\n\nDefinition.")
            with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
                execute_agent(
                    cli="gemini",
                    system_context="Agent definition",
                    prompt="User task",
                    cwd=tmpdir,
                    timeout=5000,
                    agent_file=agent_file,
                )
                popen_kwargs = mock_popen.call_args[1]
                assert popen_kwargs["env"]["GEMINI_SYSTEM_MD"] == agent_file
