#!/usr/bin/env python3
"""Tests for run_subagent.py"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "sub-agents" / "scripts"))

from run_subagent import (
    StreamProcessor,
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
            run_agent, system_context, desc = load_agent(tmpdir, "test-agent")
            assert run_agent == "codex"
            assert "# Test Agent" in system_context
            assert "This is a test agent." in desc

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


class TestExecuteAgent:
    def test_returns_error_when_cli_executable_not_found(self):
        """Should return error status when CLI executable is not in PATH"""
        # Mock build_command to return a non-existent executable
        with (
            patch(
                "run_subagent.build_command",
                return_value=("nonexistent-cli-12345", ["arg1"]),
            ),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            result = execute_agent(
                cli="codex",  # Valid CLI name, but build_command is mocked
                system_context="test context",
                prompt="test prompt",
                cwd=tmpdir,
                timeout=5000,
            )
            assert result["status"] == "error"
            assert result["exit_code"] == 127
            assert "not found" in result["error"].lower()

    def test_formats_prompt_with_system_context(self):
        """Should combine system context and user prompt"""
        mock_process = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_process.communicate.return_value = ("", "")
        mock_process.returncode = 0

        with (
            patch("subprocess.Popen", return_value=mock_process) as mock_popen,
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            execute_agent(
                cli="codex",
                system_context="System instructions",
                prompt="User task",
                cwd=tmpdir,
                timeout=5000,
            )
            # Verify the prompt was formatted correctly
            call_args = mock_popen.call_args[0][0]
            prompt_arg = call_args[-1]  # Last argument is the prompt
            assert "[System Context]" in prompt_arg
            assert "System instructions" in prompt_arg
            assert "[User Prompt]" in prompt_arg
            assert "User task" in prompt_arg
