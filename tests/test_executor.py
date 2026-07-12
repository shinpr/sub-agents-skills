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
from _executor import _build_proc_env, build_final_response, execute_agent
from run_subagent import main


class TestBuildProcEnv:
    def test_none_returns_none(self):
        assert _build_proc_env(None) is None
        assert _build_proc_env({}) is None

    def test_sets_and_overrides_keys(self):
        with patch.dict("os.environ", {"EXISTING": "old"}, clear=True):
            env = _build_proc_env({"NEW": "v", "EXISTING": "new"})
        assert env["NEW"] == "v"
        assert env["EXISTING"] == "new"

    def test_none_value_deletes_inherited_key(self):
        # glm relies on this to strip an inherited ANTHROPIC_API_KEY so it is
        # never sent to the Z.ai endpoint.
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-real"}, clear=True):
            env = _build_proc_env({"ANTHROPIC_AUTH_TOKEN": "zai", "ANTHROPIC_API_KEY": None})
        assert "ANTHROPIC_API_KEY" not in env
        assert env["ANTHROPIC_AUTH_TOKEN"] == "zai"


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

    def test_result_marked_partial_stays_partial_even_with_zero_exit(self):
        r = build_final_response("grok", 0, {"result": "progress", "status": "partial"}, [], "")
        assert r["status"] == "partial"
        assert r["exit_code"] == 0

    def test_result_marked_partial_stays_partial_when_terminated_by_us(self):
        r = build_final_response(
            "grok",
            1,
            {"result": "progress", "status": "partial"},
            [],
            "",
            terminated_by_us=True,
        )
        assert r["status"] == "partial"
        assert r["exit_code"] == 1

    def test_terminated_by_us_with_result_is_success_regardless_of_exit_code(self):
        # Windows: terminate() maps to TerminateProcess and yields exit code 1,
        # unlike POSIX SIGTERM (143 / -15). When we asked the CLI to stop after a
        # complete result, the exit code is irrelevant — it must report success.
        r = build_final_response("claude", 1, {"result": "ok"}, [], "", terminated_by_us=True)
        assert r["status"] == "success"
        assert r["exit_code"] == 1

    def test_nonzero_with_result_not_terminated_is_partial(self):
        # Same exit code 1, but we did NOT initiate termination — a genuine
        # abnormal exit with partial output stays "partial", not "success".
        r = build_final_response("claude", 1, {"result": "ok"}, [], "", terminated_by_us=False)
        assert r["status"] == "partial"
        assert r["exit_code"] == 1

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
        flood = ['{"type": "noise", "data": "' + ("x" * 180) + '"}\n'] * 1000 + [""]
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

    def test_stdout_cap_does_not_override_completed_result(self):
        mock_process = MagicMock()
        flood = ['{"type": "noise", "data": "' + ("x" * 180) + '"}\n'] * 20
        mock_process.stdout.readline.side_effect = [
            '{"type": "thread.started"}\n',
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "DONE"}}\n',
            '{"type": "turn.completed"}\n',
            *flood,
            "",
        ]
        mock_process.communicate.return_value = ("", "")
        mock_process.returncode = 1

        with patch("subprocess.Popen", return_value=mock_process):
            with patch("_executor._MAX_STDOUT_CHARS", 1024):
                result = execute_agent(
                    AgentInvocation(cli="codex", prompt="x", cwd="/tmp"),
                    timeout_ms=10000,
                )
        assert result["status"] == "success"
        assert result["result"] == "DONE"
        assert mock_process.terminate.called
        assert not mock_process.kill.called

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

    def test_popen_uses_explicit_utf8_encoding(self):
        """Subprocess output must be decoded as UTF-8 on every platform.

        Without an explicit encoding, text mode decodes using the locale
        codepage (e.g. cp932 on Japanese Windows), garbling the UTF-8 NDJSON
        the CLIs emit. Pin encoding and use a lenient error policy so malformed
        bytes degrade gracefully instead of raising.
        """
        mock_process = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_process.communicate.return_value = ("", "")
        mock_process.returncode = 0

        with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            execute_agent(
                AgentInvocation(cli="codex", prompt="x", cwd="/tmp"),
                timeout_ms=5000,
            )
        popen_kwargs = mock_popen.call_args[1]
        assert popen_kwargs["encoding"] == "utf-8"
        assert popen_kwargs["errors"] == "replace"

    def test_windows_terminate_exit_code_still_reports_success(self):
        """End-to-end Windows simulation: a complete result then exit code 1.

        On Windows the broker calls terminate() after parsing the terminal
        event, and TerminateProcess leaves returncode 1. With a full result in
        hand this must surface as success, matching POSIX behaviour.
        """
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"type": "thread.started"}\n',
            '{"type": "item.completed", "item": {"type": "agent_message", "text": "DONE"}}\n',
            '{"type": "turn.completed"}\n',
            "",
        ]
        mock_process.communicate.return_value = ("", "")
        mock_process.returncode = 1  # Windows TerminateProcess exit code

        with patch("subprocess.Popen", return_value=mock_process):
            result = execute_agent(
                AgentInvocation(cli="codex", prompt="x", cwd="/tmp"),
                timeout_ms=5000,
            )
        assert result["status"] == "success"
        assert result["result"] == "DONE"

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

    def test_grok_pretty_json_output_is_parsed_after_exit(self):
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            "{\n",
            '  "text": "{\\"findings\\":[]}",\n',
            '  "stopReason": "EndTurn",\n',
            '  "sessionId": "s",\n',
            '  "requestId": "r"\n',
            "}\n",
            "",
        ]
        mock_process.communicate.return_value = ("", "")
        mock_process.returncode = 0

        with patch("subprocess.Popen", return_value=mock_process):
            result = execute_agent(
                AgentInvocation(cli="grok", prompt="x", cwd="/tmp"),
                timeout_ms=5000,
            )
        assert result["status"] == "success"
        assert result["result"] == '{"findings":[]}'

    def test_grok_compact_json_output_is_normalized(self):
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"text": "{\\"findings\\":[]}", "stopReason": "EndTurn"}\n',
            "",
        ]
        mock_process.communicate.return_value = ("", "")
        mock_process.returncode = 0

        with patch("subprocess.Popen", return_value=mock_process):
            result = execute_agent(
                AgentInvocation(cli="grok", prompt="x", cwd="/tmp"),
                timeout_ms=5000,
            )
        assert result["status"] == "success"
        assert result["result"] == '{"findings":[]}'

    def test_grok_compact_cancelled_output_stays_partial(self):
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"text": "progress only", "stopReason": "Cancelled"}\n',
            "",
        ]
        mock_process.communicate.return_value = ("", "")
        mock_process.returncode = 1

        with patch("subprocess.Popen", return_value=mock_process):
            result = execute_agent(
                AgentInvocation(cli="grok", prompt="x", cwd="/tmp"),
                timeout_ms=5000,
            )
        assert result["status"] == "partial"
        assert result["result"] == "progress only"

    def test_opencode_ndjson_output_is_parsed(self):
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            '{"type":"step_start","part":{}}\n',
            '{"type":"tool_use","part":{"tool":"read"}}\n',
            '{"type":"step_finish","part":{"reason":"tool-calls"}}\n',
            '{"type":"step_start","part":{}}\n',
            '{"type":"text","part":{"text":"DONE"}}\n',
            '{"type":"step_finish","part":{"reason":"stop"}}\n',
            "",
        ]
        mock_process.communicate.return_value = ("", "")
        mock_process.returncode = 0

        with patch("subprocess.Popen", return_value=mock_process):
            result = execute_agent(
                AgentInvocation(cli="opencode", prompt="x", cwd="/tmp"),
                timeout_ms=5000,
            )
        assert result["status"] == "success"
        assert result["result"] == "DONE"


class TestOpencodeDataDirIsolation:
    """Each OpenCode invocation gets a private XDG data/state home.

    OpenCode keeps all sessions in one shared SQLite DB; concurrent runs race
    on it and fail with "database is locked". The executor isolates the data
    dir per invocation and cleans it up once the process finishes.
    """

    def _mock_process(self):
        m = MagicMock()
        m.stdout.readline.side_effect = [
            '{"type":"text","part":{"text":"DONE"}}\n',
            '{"type":"step_finish","part":{"reason":"stop"}}\n',
            "",
        ]
        m.communicate.return_value = ("", "")
        m.returncode = 0
        return m

    def _run_capturing_env(self, popen_side_effect):
        captured = {}

        def popen_factory(cmd, **kwargs):
            captured["env"] = kwargs["env"]
            return popen_side_effect()

        with patch("subprocess.Popen", side_effect=popen_factory):
            result = execute_agent(
                AgentInvocation(cli="opencode", prompt="x", cwd="/tmp"),
                timeout_ms=5000,
            )
        return result, captured["env"]

    def test_private_xdg_dirs_and_permission_env_survive_the_merge(self):
        result, env = self._run_capturing_env(self._mock_process)

        assert result["status"] == "success"
        assert "OPENCODE_PERMISSION" in env  # builder's env_override is preserved
        data_home, state_home = env["XDG_DATA_HOME"], env["XDG_STATE_HOME"]
        assert data_home != state_home
        # Both live inside one per-invocation temp dir, not the user's homes.
        temp_dir = os.path.dirname(data_home)
        assert temp_dir == os.path.dirname(state_home)
        assert os.path.basename(temp_dir).startswith("subagent-opencode-")

    def test_temp_dir_is_removed_after_the_run(self):
        _, env = self._run_capturing_env(self._mock_process)
        assert not os.path.exists(os.path.dirname(env["XDG_DATA_HOME"]))

    def test_temp_dir_is_removed_when_spawn_fails(self):
        def raise_not_found():
            raise FileNotFoundError()

        result, env = self._run_capturing_env(raise_not_found)
        assert result["status"] == "error"
        assert not os.path.exists(os.path.dirname(env["XDG_DATA_HOME"]))

    def test_auth_json_is_copied_into_isolated_data_home(self):
        # auth.json lives in the data home, so `opencode auth login` credentials
        # must follow the invocation into its private dir. Checked inside the
        # Popen factory because the temp dir is gone after execute_agent returns.
        captured = {}
        with tempfile.TemporaryDirectory() as fake_default:
            opencode_dir = Path(fake_default) / "opencode"
            opencode_dir.mkdir(parents=True)
            (opencode_dir / "auth.json").write_text('{"provider":"key"}')

            def popen_factory(cmd, **kwargs):
                copied = Path(kwargs["env"]["XDG_DATA_HOME"]) / "opencode" / "auth.json"
                captured["content"] = copied.read_text() if copied.is_file() else None
                return self._mock_process()

            with patch.dict("os.environ", {"XDG_DATA_HOME": fake_default}):
                with patch("subprocess.Popen", side_effect=popen_factory):
                    execute_agent(
                        AgentInvocation(cli="opencode", prompt="x", cwd="/tmp"),
                        timeout_ms=5000,
                    )
        assert captured["content"] == '{"provider":"key"}'

    def test_auth_json_copy_failure_does_not_crash_the_run(self):
        # The copy is best-effort: env-based provider auth needs no auth.json,
        # so an unreadable file must not turn into an executor crash.
        with tempfile.TemporaryDirectory() as fake_default:
            opencode_dir = Path(fake_default) / "opencode"
            opencode_dir.mkdir(parents=True)
            (opencode_dir / "auth.json").write_text('{"provider":"key"}')

            with patch.dict("os.environ", {"XDG_DATA_HOME": fake_default}):
                with patch("shutil.copy2", side_effect=OSError("permission denied")):
                    with patch("subprocess.Popen", return_value=self._mock_process()):
                        result = execute_agent(
                            AgentInvocation(cli="opencode", prompt="x", cwd="/tmp"),
                            timeout_ms=5000,
                        )
        assert result["status"] == "success"
        assert result["result"] == "DONE"

    def test_other_clis_are_not_isolated(self):
        captured = {}

        def popen_factory(cmd, **kwargs):
            captured["env"] = kwargs["env"]
            m = MagicMock()
            m.stdout.readline.side_effect = [
                '{"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}\n',
                '{"type": "turn.completed"}\n',
                "",
            ]
            m.communicate.return_value = ("", "")
            m.returncode = 0
            return m

        with patch("subprocess.Popen", side_effect=popen_factory):
            execute_agent(
                AgentInvocation(cli="codex", prompt="x", cwd="/tmp"),
                timeout_ms=5000,
            )
        assert captured["env"] is None


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
                "---\nrun-agent: codex\nmodel: gpt-5.4-mini\npermission: read-only\n---\n"
                "# Echo\n\nReply.\n"
            )

            def popen_factory(args, **_kwargs):
                model_idx = args.index("--model")
                assert args[model_idx + 1] == "gpt-5.4-mini"
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
