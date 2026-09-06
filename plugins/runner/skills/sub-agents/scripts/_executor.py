from __future__ import annotations

import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from typing import TypedDict

from _builder import AgentInvocation, ProcessInvocation, build_invocation_args
from _constants import DEFAULT_TIMEOUT_MS
from _stream import StreamData, StreamProcessor


class _AgentResponseFields(TypedDict):
    # The error path passes the backend's payload through, so result is not str.
    result: object
    exit_code: int
    status: str
    cli: str


# Two classes because NotRequired needs 3.11 and the scripts support 3.9.
class AgentResponse(_AgentResponseFields, total=False):
    """The JSON contract returned to the caller; ``error`` is present on failure."""

    error: str


def _result_value(result: StreamData | None) -> object:
    """Read the result out of a stream payload, preserving whatever it holds."""
    if not result:
        return ""
    return result.get("result", "")


# SIGTERM may be reported as 143 or -15.
_SUCCESS_EXIT_CODES = (0, 143, -15)

_CURSOR_AUTH_ERROR_PHRASES = (
    "authentication required",
    "authentication failed",
    "not authenticated",
    "not logged in",
    "unauthenticated",
    "unauthorized",
    "please log in",
    "please login",
)


def _cursor_legacy_key_guidance(error: str) -> str | None:
    """Give the calling LLM migration guidance when legacy config explains auth failure."""
    if "CLI_API_KEY" not in os.environ:
        return None

    cursor_api_key = os.environ.get("CURSOR_API_KEY")
    if cursor_api_key and cursor_api_key.strip():
        return None

    normalized_error = error.lower()
    is_auth_error = any(phrase in normalized_error for phrase in _CURSOR_AUTH_ERROR_PHRASES) or (
        "api key" in normalized_error
        and any(word in normalized_error for word in ("invalid", "missing", "rejected"))
    )
    if not is_auth_error:
        return None

    return (
        "Cursor authentication error: CLI_API_KEY is set but no longer supported. "
        "Run `cursor-agent login` or set CURSOR_API_KEY, then retry."
    )


def _partial_response(
    cli: str, result: StreamData | None, exit_code: int, error: str
) -> AgentResponse:
    return {
        "result": _result_value(result),
        "exit_code": exit_code,
        "status": "partial" if result else "error",
        "cli": cli,
        "error": error,
    }


def _error_response(
    cli: str, exit_code: int, error: str, partial_result: StreamData | None = None
) -> AgentResponse:
    return {
        "result": _result_value(partial_result),
        "exit_code": exit_code,
        "status": "error",
        "cli": cli,
        "error": error,
    }


def _classify_status(result: StreamData | None, exit_code: int, *, terminated_by_us: bool) -> str:
    """Decide the run's outcome, treating intentional termination as success."""
    if not result:
        return "error"
    if result.get("status") == "error" or result.get("is_error") is True:
        return "error"
    if result.get("status") == "partial":
        return "partial"
    if terminated_by_us or exit_code in _SUCCESS_EXIT_CODES:
        return "success"
    return "partial"


def _error_message(response: AgentResponse, result: StreamData | None, stderr: str) -> str:
    result_error = result.get("error") if result else None
    result_subtype = result.get("subtype") if result else None
    result_text = result.get("result") if result else None
    if isinstance(result_error, str) and result_error.strip():
        msg = result_error.strip()
    elif isinstance(result_subtype, str) and result_subtype.startswith("error_"):
        msg = f"CLI reported {result_subtype.strip()}"
    elif isinstance(result_text, str) and result_text.strip():
        msg = result_text.strip()
    elif result:
        msg = "CLI reported an error"
    else:
        msg = f"CLI exited with code {response['exit_code']}"

    if stderr and stderr.strip():
        msg += f": {stderr.strip()}"

    if response["cli"] == "cursor-agent":
        error_context = msg
        output = response["result"]
        # With no parsed result the response carries the joined stdout.
        if result is None and isinstance(output, str):
            error_context += f"\n{output[:8192]}"
        msg = _cursor_legacy_key_guidance(error_context) or msg
    return msg


# PLR0913: every input decides a case; keyword-only so order is never inferred.
def build_final_response(  # noqa: PLR0913
    *,
    cli: str,
    returncode: int | None,
    result: StreamData | None,
    stdout_lines: list[str],
    stderr: str,
    terminated_by_us: bool = False,
) -> AgentResponse:
    exit_code = returncode if returncode is not None else 1
    status = _classify_status(result, exit_code, terminated_by_us=terminated_by_us)

    response: AgentResponse = {
        "result": _result_value(result) if result else "".join(stdout_lines),
        "exit_code": exit_code,
        "status": status,
        "cli": cli,
    }
    if status == "error":
        response["error"] = _error_message(response, result, stderr)
    return response


# Bound captured output to prevent an unending stream from exhausting memory.
_MAX_STDOUT_CHARS = 64 * 1024 * 1024


def _spawn_reader(process: subprocess.Popen[str]) -> queue.Queue[str | None]:
    """Read stdout in a daemon thread so the main loop can enforce timeouts."""
    line_q: queue.Queue[str | None] = queue.Queue()
    stdout = process.stdout
    if stdout is None:  # pragma: no cover - the process is always spawned with a pipe
        raise ValueError("process was spawned without a stdout pipe")

    def reader() -> None:
        try:
            for line in iter(stdout.readline, ""):
                line_q.put(line)
        finally:
            line_q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    return line_q


def _timeout_payload(cli: str, processor: StreamProcessor, timeout_ms: int) -> AgentResponse:
    error = (
        f"Sub-agent timed out after {timeout_ms} ms. "
        "Increase --timeout or simplify the task before retrying."
    )
    return _partial_response(cli, processor.get_result(), 124, error)


def _drain_to_eof(line_q: queue.Queue[str | None], budget_sec: float = 0.5) -> None:
    """Drain stdout to prevent concurrent reads during ``communicate()``."""
    deadline = time.monotonic() + budget_sec
    while time.monotonic() < deadline:
        try:
            line = line_q.get(timeout=0.05)
        except queue.Empty:
            return
        if line is None:
            return


def _drive_process(process: subprocess.Popen[str], cli: str, timeout_ms: int) -> AgentResponse:
    deadline = time.monotonic() + timeout_ms / 1000
    processor = StreamProcessor(cli)
    stdout_lines: list[str] = []
    accumulated_chars = 0
    line_q = _spawn_reader(process)
    saw_terminal = False

    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                _drain_to_eof(line_q)
                process.communicate()
                return _timeout_payload(cli, processor, timeout_ms)

            try:
                line = line_q.get(timeout=remaining)
            except queue.Empty:
                process.kill()
                _drain_to_eof(line_q)
                process.communicate()
                return _timeout_payload(cli, processor, timeout_ms)

            if line is None:
                break
            stdout_lines.append(line)
            accumulated_chars += len(line)
            if not saw_terminal and accumulated_chars > _MAX_STDOUT_CHARS:
                process.kill()
                _drain_to_eof(line_q)
                process.communicate()
                return _error_response(
                    cli,
                    1,
                    f"Sub-agent output exceeded {_MAX_STDOUT_CHARS} characters. "
                    "Retry with a narrower task.",
                    partial_result=processor.get_result(),
                )
            if not saw_terminal and processor.process_line(line):
                process.terminate()
                saw_terminal = True

        # Allow a short graceful-exit window before killing the process.
        wait_remaining = max(0.1, deadline - time.monotonic())
        try:
            _, stderr = process.communicate(timeout=wait_remaining)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate()
            return _timeout_payload(cli, processor, timeout_ms)

        result = processor.get_result()
        if result is None:
            processor.process_complete_output("".join(stdout_lines))
            result = processor.get_result()

        return build_final_response(
            cli=cli,
            returncode=process.returncode,
            result=result,
            stdout_lines=stdout_lines,
            stderr=stderr,
            terminated_by_us=saw_terminal,
        )
    except (OSError, ValueError) as e:
        process.kill()
        # Reap before callers clean up per-run resources.
        process.wait()
        return _error_response(
            cli, 1, f"{type(e).__name__}: {e}", partial_result=processor.get_result()
        )


def _build_proc_env(env_override: dict[str, str | None] | None) -> dict[str, str] | None:
    """Apply child environment overrides; ``None`` removes a variable."""
    if not env_override:
        return None
    proc_env = {**os.environ}
    for key, value in env_override.items():
        if value is None:
            proc_env.pop(key, None)
        else:
            proc_env[key] = value
    return proc_env


def _spawn_and_drive(
    process_invocation: ProcessInvocation,
    inv: AgentInvocation,
    proc_env: dict[str, str] | None,
    timeout_ms: int,
) -> AgentResponse:
    command, args = process_invocation.command, process_invocation.args
    cli, cwd = inv.cli, inv.cwd
    try:
        # Prevent CLIs from waiting for interactive input.
        # S603: command is a literal from build_command()'s closed set, and args
        # go through argv with shell=False, so no prompt text reaches a shell.
        process = subprocess.Popen(  # noqa: S603
            [command, *args],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # CLI streams are UTF-8 regardless of host locale.
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=proc_env,
        )
    except FileNotFoundError:
        return _error_response(
            cli,
            127,
            f"CLI unavailable: {command!r} was not found on PATH. "
            "Install it or select another backend.",
        )
    except OSError as e:
        return _error_response(cli, 1, f"{type(e).__name__}: {e}")

    return _drive_process(process, cli, timeout_ms)


def _isolated_opencode_env(
    env_override: dict[str, str | None] | None, temp_dir: str
) -> dict[str, str | None]:
    """Isolate OpenCode state to prevent concurrent SQLite session locks."""
    data_home = os.path.join(temp_dir, "data")
    state_home = os.path.join(temp_dir, "state")
    os.makedirs(os.path.join(data_home, "opencode"))
    os.makedirs(state_home)

    default_data_home = os.environ.get(
        "XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share")
    )
    auth_file = os.path.join(default_data_home, "opencode", "auth.json")
    try:
        if os.path.isfile(auth_file):
            shutil.copy2(auth_file, os.path.join(data_home, "opencode", "auth.json"))
    except OSError:
        # OpenCode reports authentication failures when this copy was required.
        pass

    return {**(env_override or {}), "XDG_DATA_HOME": data_home, "XDG_STATE_HOME": state_home}


def execute_agent(inv: AgentInvocation, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> AgentResponse:
    process_invocation = build_invocation_args(inv)

    if inv.cli == "opencode":
        temp_dir = tempfile.mkdtemp(prefix="subagent-opencode-")
        try:
            proc_env = _build_proc_env(
                _isolated_opencode_env(process_invocation.env_override, temp_dir)
            )
            return _spawn_and_drive(process_invocation, inv, proc_env, timeout_ms)
        finally:
            # _spawn_and_drive reaps the process before returning.
            shutil.rmtree(temp_dir, ignore_errors=True)

    proc_env = _build_proc_env(process_invocation.env_override)
    return _spawn_and_drive(process_invocation, inv, proc_env, timeout_ms)
