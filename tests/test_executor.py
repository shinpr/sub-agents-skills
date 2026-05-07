"""Tests for _executor and main(): subprocess driving, response shaping, e2e contract."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from _builder import AgentInvocation
from _executor import build_final_response, execute_agent
from run_subagent import main


class TestBuildFinalResponse:
    """Status determination from (returncode, parsed result, stdout, stderr)."""

    def test_success(self):
        r = build_final_response("codex", 0, {"result": "ok"}, [], "")
        assert r == {"result": "ok", "exit_code": 0, "status": "success", "cli": "codex"}

    def test_sigterm_with_result_is_success(self):
        # CLI was terminated after the result event — that's still success
        r = build_final_response("claude", 143, {"result": "ok"}, [], "")
        assert r["status"] == "success"
        assert r["exit_code"] == 143

    def test_returncode_none_treated_as_failure(self):
        # Defensive: process not yet finished should not be reported as success
        r = build_final_response("codex", None, {"result": "ok"}, [], "")
        assert r["exit_code"] == 1
        assert r["status"] == "partial"

    def test_nonzero_with_result_is_partial(self):
        r = build_final_response("codex", 2, {"result": "stuff"}, [], "")
        assert r["status"] == "partial"
        assert r["exit_code"] == 2

    def test_returncode_none_without_result_is_error(self):
        # No exit and no parsed payload — must not slip through as success.
        r = build_final_response("codex", None, None, [], "")
        assert r["status"] == "error"
        assert r["exit_code"] == 1
        # No stderr means just the bare "exited with code" message, no colon suffix.
        assert r["error"] == "CLI exited with code 1"

    def test_nonzero_without_result_is_error_with_stderr(self):
        r = build_final_response("codex", 1, None, ["raw line\n"], "boom")
        assert r["status"] == "error"
        assert r["exit_code"] == 1
        assert r["error"] == "CLI exited with code 1: boom"
        # When parsing failed, raw stdout falls into result for debugging
        assert r["result"] == "raw line\n"


class TestExecuteAgent:
    def test_returns_error_when_cli_executable_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "_executor.build_invocation_args",
                return_value=("nonexistent-cli-12345", ["arg1"], None),
            ):
                result = execute_agent(
                    AgentInvocation(
                        cli="codex",
                        prompt="test prompt",
                        cwd=tmpdir,
                        system_context="test context",
                    ),
                    timeout_ms=5000,
                )
                assert result["status"] == "error"
                assert result["exit_code"] == 127
                assert "not found" in result["error"].lower()

    def test_codex_concatenates_prompt_with_agent_file(self):
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
                    AgentInvocation(
                        cli="codex",
                        prompt="User task",
                        cwd=tmpdir,
                        system_context="Agent definition",
                        agent_file=agent_file,
                    ),
                    timeout_ms=5000,
                )
                call_args = mock_popen.call_args[0][0]
                assert not any("model_instructions_file" in arg for arg in call_args)
                prompt_arg = call_args[-1]
                assert "[System Context]" in prompt_arg
                assert "Agent definition" in prompt_arg
                assert "[User Prompt]" in prompt_arg
                assert "User task" in prompt_arg

    def test_aborts_when_stdout_exceeds_memory_cap(self):
        """A flooding sub-agent must be killed before exhausting broker memory.

        Patches _MAX_STDOUT_CHARS low and feeds non-terminal lines until the
        cap trips; verifies kill is called and an explicit error is returned.
        """
        mock_process = MagicMock()
        # ~200 chars per line; cap will be patched below; aim for >cap quickly.
        flood = ['{"noise": "' + ("x" * 180) + '"}\n'] * 1000 + [""]
        mock_process.stdout.readline.side_effect = flood
        mock_process.communicate.return_value = ("", "")
        mock_process.returncode = -9

        with patch("subprocess.Popen", return_value=mock_process):
            with patch("_executor._MAX_STDOUT_CHARS", 1024):  # 1 K codepoints
                result = execute_agent(
                    AgentInvocation(cli="codex", prompt="x", cwd="/tmp"),
                    timeout_ms=10000,
                )
        assert result["status"] == "error"
        assert result["exit_code"] == 1
        assert "exceeded" in result["error"]
        assert mock_process.kill.called

    def test_timeout_when_cli_blocks_without_output(self):
        """A CLI that produces no output and never exits must be killed by the deadline.

        Regression for the original implementation: ``readline()`` was called in
        the main thread, so a CLI that filled stderr or simply hung silently
        would block forever — the ``communicate(timeout=...)`` line was never
        reached. The deadline must govern the whole subprocess lifetime.
        """
        mock_process = MagicMock()
        kill_event = threading.Event()

        def blocking_readline():
            kill_event.wait(timeout=5)
            return ""

        mock_process.stdout.readline.side_effect = blocking_readline

        def stop_process():
            kill_event.set()
            mock_process.returncode = -9

        mock_process.kill.side_effect = stop_process
        mock_process.terminate.side_effect = stop_process
        mock_process.communicate.return_value = ("", "")
        mock_process.returncode = None

        with patch("subprocess.Popen", return_value=mock_process):
            start = time.monotonic()
            result = execute_agent(
                AgentInvocation(cli="codex", prompt="x", cwd="/tmp"),
                timeout_ms=300,
            )
            elapsed = time.monotonic() - start

        # The whole call must respect the deadline (with reasonable slack).
        assert elapsed < 2.0
        assert result["exit_code"] == 124
        assert "Timeout" in result["error"]

    def test_gemini_passes_env_with_agent_file(self):
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
                    AgentInvocation(
                        cli="gemini",
                        prompt="User task",
                        cwd=tmpdir,
                        system_context="Agent definition",
                        agent_file=agent_file,
                    ),
                    timeout_ms=5000,
                )
                popen_kwargs = mock_popen.call_args[1]
                assert popen_kwargs["env"]["GEMINI_SYSTEM_MD"] == agent_file


class TestMainEndToEnd:
    """Drive main() end-to-end with subprocess mocked. Verifies the JSON contract."""

    def _run(self, argv, popen_factory):
        with patch.object(sys, "argv", argv):
            with patch("subprocess.Popen", side_effect=popen_factory):
                buf = StringIO()
                with patch("sys.stdout", buf):
                    with pytest.raises(SystemExit) as exc_info:
                        main()
                return buf.getvalue(), exc_info.value.code

    def test_main_success_returns_zero_and_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = Path(tmpdir) / ".agents"
            agents_dir.mkdir()
            (agents_dir / "echo.md").write_text(
                "---\nrun-agent: codex\npermission: read-only\n---\n# Echo\n\nReply.\n"
            )

            def popen_factory(*_args, **_kwargs):
                m = MagicMock()
                m.stdout.readline.side_effect = [
                    '{"type": "thread.started"}\n',
                    '{"type": "item.completed", "item": {"type": "agent_message", "text": "PONG"}}\n',
                    '{"type": "turn.completed"}\n',
                    "",
                ]
                m.communicate.return_value = ("", "")
                m.returncode = 0
                return m

            argv = [
                "run_subagent.py",
                "--agent",
                "echo",
                "--prompt",
                "now",
                "--cwd",
                tmpdir,
            ]
            stdout, code = self._run(argv, popen_factory)
            payload = json.loads(stdout.strip())
            assert payload["status"] == "success"
            assert payload["result"] == "PONG"
            assert payload["cli"] == "codex"
            assert code == 0

    def test_main_invalid_permission_returns_one(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = Path(tmpdir) / ".agents"
            agents_dir.mkdir()
            (agents_dir / "bad.md").write_text(
                "---\nrun-agent: codex\npermission: dangerous\n---\n# bad\n"
            )
            argv = [
                "run_subagent.py",
                "--agent",
                "bad",
                "--prompt",
                "x",
                "--cwd",
                tmpdir,
            ]
            with patch.object(sys, "argv", argv):
                buf = StringIO()
                with patch("sys.stdout", buf):
                    with pytest.raises(SystemExit) as exc_info:
                        main()
            payload = json.loads(buf.getvalue().strip())
            assert payload["status"] == "error"
            assert "Invalid permission" in payload["error"]
            assert exc_info.value.code == 1

    def test_main_invalid_cli_override_returns_json_error(self):
        """--cli with an unknown value must produce a JSON error, not a traceback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = Path(tmpdir) / ".agents"
            agents_dir.mkdir()
            (agents_dir / "echo.md").write_text("---\nrun-agent: codex\n---\n# Echo\n")
            argv = [
                "run_subagent.py",
                "--agent",
                "echo",
                "--prompt",
                "x",
                "--cwd",
                tmpdir,
                "--cli",
                "totally-fake-cli",
            ]
            with patch.object(sys, "argv", argv):
                buf = StringIO()
                with patch("sys.stdout", buf):
                    with pytest.raises(SystemExit) as exc_info:
                        main()
            payload = json.loads(buf.getvalue().strip())
            assert payload["status"] == "error"
            assert "Unknown CLI" in payload["error"]
            assert exc_info.value.code == 1

    def test_main_list_returns_agents_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = Path(tmpdir) / ".agents"
            agents_dir.mkdir()
            (agents_dir / "a.md").write_text("# A\n\none.")
            argv = ["run_subagent.py", "--list", "--cwd", tmpdir]
            with patch.object(sys, "argv", argv):
                buf = StringIO()
                with patch("sys.stdout", buf):
                    with pytest.raises(SystemExit) as exc_info:
                        main()
            payload = json.loads(buf.getvalue().strip())
            assert exc_info.value.code == 0
            assert payload["agents"] == [{"name": "a", "description": "one."}]
