from __future__ import annotations

import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time

from _builder import AgentInvocation, build_invocation_args
from _constants import DEFAULT_TIMEOUT_MS
from _stream import StreamProcessor

# SIGTERM may be reported as 143 or -15.
_SUCCESS_EXIT_CODES = (0, 143, -15)


def _partial_response(cli: str, result: dict | None, exit_code: int, error: str) -> dict:
    return {
        "result": result.get("result", "") if result else "",
        "exit_code": exit_code,
        "status": "partial" if result else "error",
        "cli": cli,
        "error": error,
    }


def _error_response(
    cli: str, exit_code: int, error: str, partial_result: dict | None = None
) -> dict:
    return {
        "result": partial_result.get("result", "") if partial_result else "",
        "exit_code": exit_code,
        "status": "error",
        "cli": cli,
        "error": error,
    }


def build_final_response(
    cli: str,
    returncode: int | None,
    result: dict | None,
    stdout_lines: list,
    stderr: str,
    terminated_by_us: bool = False,
) -> dict:
    """Build a response, treating intentional termination as success."""
    exit_code = returncode if returncode is not None else 1

    if result and result.get("status") == "partial":
        status = "partial"
    elif result and (terminated_by_us or exit_code in _SUCCESS_EXIT_CODES):
        status = "success"
    elif result:
        status = "partial"
    else:
        status = "error"

    response = {
        "result": result.get("result", "") if result else "".join(stdout_lines),
        "exit_code": exit_code,
        "status": status,
        "cli": cli,
    }
    if status == "error":
        msg = f"CLI exited with code {exit_code}"
        if stderr and stderr.strip():
            msg += f": {stderr.strip()}"
        response["error"] = msg
    return response


_LINE = "line"
_EOF = "eof"

# Bound captured output to prevent an unending stream from exhausting memory.
_MAX_STDOUT_CHARS = 64 * 1024 * 1024


def _spawn_reader(process: subprocess.Popen) -> queue.Queue:
    """Read stdout in a daemon thread so the main loop can enforce timeouts."""
    line_q: queue.Queue = queue.Queue()

    def reader() -> None:
        try:
            for line in iter(process.stdout.readline, ""):
                line_q.put((_LINE, line))
        finally:
            line_q.put((_EOF, None))

    threading.Thread(target=reader, daemon=True).start()
    return line_q


def _timeout_payload(cli: str, processor: StreamProcessor, timeout_ms: int) -> dict:
    error = (
        f"Sub-agent timed out after {timeout_ms} ms. "
        "Increase --timeout or simplify the task before retrying."
    )
    return _partial_response(cli, processor.get_result(), 124, error)


def _drain_to_eof(line_q: queue.Queue, budget_sec: float = 0.5) -> None:
    """Drain stdout to prevent concurrent reads during ``communicate()``."""
    deadline = time.monotonic() + budget_sec
    while time.monotonic() < deadline:
        try:
            kind, _ = line_q.get(timeout=0.05)
        except queue.Empty:
            return
        if kind == _EOF:
            return


def _drive_process(process: subprocess.Popen, cli: str, timeout_ms: int) -> dict:
    deadline = time.monotonic() + timeout_ms / 1000
    processor = StreamProcessor()
    stdout_lines: list = []
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
                kind, line = line_q.get(timeout=remaining)
            except queue.Empty:
                process.kill()
                _drain_to_eof(line_q)
                process.communicate()
                return _timeout_payload(cli, processor, timeout_ms)

            if kind == _EOF:
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
            cli,
            process.returncode,
            result,
            stdout_lines,
            stderr,
            terminated_by_us=saw_terminal,
        )
    except (OSError, ValueError) as e:
        process.kill()
        # Reap before callers clean up per-run resources.
        process.wait()
        return _error_response(
            cli, 1, f"{type(e).__name__}: {e}", partial_result=processor.get_result()
        )


def _build_proc_env(env_override: dict | None) -> dict | None:
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
    command: str,
    args: list,
    proc_env: dict | None,
    cwd: str,
    cli: str,
    timeout_ms: int,
) -> dict:
    try:
        # Prevent CLIs from waiting for interactive input.
        process = subprocess.Popen(
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


def _isolated_opencode_env(env_override: dict | None, temp_dir: str) -> dict:
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


def execute_agent(inv: AgentInvocation, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict:
    command, args, env_override = build_invocation_args(inv)

    if inv.cli == "opencode":
        temp_dir = tempfile.mkdtemp(prefix="subagent-opencode-")
        try:
            proc_env = _build_proc_env(_isolated_opencode_env(env_override, temp_dir))
            return _spawn_and_drive(command, args, proc_env, inv.cwd, inv.cli, timeout_ms)
        finally:
            # _spawn_and_drive reaps the process before returning.
            shutil.rmtree(temp_dir, ignore_errors=True)

    proc_env = _build_proc_env(env_override)
    return _spawn_and_drive(command, args, proc_env, inv.cwd, inv.cli, timeout_ms)
