"""Subprocess driver: spawn the CLI, consume its stream, shape the response."""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time

from _builder import AgentInvocation, build_invocation_args
from _stream import StreamProcessor

_SUCCESS_EXIT_CODES = (0, 143, -15)  # 0 ok, 143/-15 = SIGTERM (we asked it to stop)


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
) -> dict:
    """Assemble the response dict from process exit state and parsed result.

    ``returncode is None`` means the process has not actually finished — that
    is treated as a failure (the original ``or 0`` masked this).
    """
    exit_code = returncode if returncode is not None else 1

    if exit_code in _SUCCESS_EXIT_CODES and result:
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

# Cap on accumulated stdout codepoints per invocation. Protects the broker
# from OOM if a sub-agent emits high-rate non-terminal output for the full
# wall-clock timeout (default 10 minutes). Counted via len(str) since stdout
# is read in text mode — for ASCII CLI output (the common case) this equals
# bytes; for non-ASCII content the actual memory pressure can be up to ~4×
# this number. 64 M codepoints is a safety net far above realistic transcripts.
_MAX_STDOUT_CHARS = 64 * 1024 * 1024


def _spawn_reader(process: subprocess.Popen) -> queue.Queue:
    """Push each stdout line into a queue from a daemon thread.

    Without this, ``readline()`` blocks indefinitely if the CLI hangs without
    closing stdout — the timeout in :func:`_drive_process` only governs queue
    waits, so the reader thread could otherwise outlive the parent's timeout
    deadline. ``daemon=True`` ensures the thread dies with the interpreter.
    """
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
    return _partial_response(cli, processor.get_result(), 124, f"Timeout after {timeout_ms}ms")


def _drain_to_eof(line_q: queue.Queue, budget_sec: float = 0.5) -> None:
    """Best-effort: consume the reader queue until _EOF or short budget.

    Used after kill() so that ``communicate()`` reads stderr without racing
    the reader thread on stdout. Safe to call when the reader is already
    done — the queue already holds an _EOF sentinel.
    """
    deadline = time.monotonic() + budget_sec
    while time.monotonic() < deadline:
        try:
            kind, _ = line_q.get(timeout=0.05)
        except queue.Empty:
            return
        if kind == _EOF:
            return


def _drive_process(process: subprocess.Popen, cli: str, timeout_ms: int) -> dict:
    """Read process stdout via StreamProcessor, enforce a wall-clock deadline.

    The wall-clock deadline covers the entire subprocess lifetime — including
    cases where the CLI never produces stdout, blocks on stderr, or stops
    emitting lines. A blocking ``readline()`` in the main thread would never
    reach the timeout check, so reads are delegated to a background thread
    and observed via a queue.

    After a terminal event is parsed we keep draining the queue until the
    reader thread reports EOF before calling ``communicate()`` — that way
    only one consumer ever reads ``process.stdout``.
    """
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
            if accumulated_chars > _MAX_STDOUT_CHARS:
                # Defensive cap: a sub-agent emitting unbounded non-terminal
                # output would otherwise grow stdout_lines until the wall-clock
                # deadline (default 10 min). Kill it and report partial.
                process.kill()
                _drain_to_eof(line_q)
                process.communicate()
                return _error_response(
                    cli,
                    1,
                    f"Sub-agent stdout exceeded {_MAX_STDOUT_CHARS} characters; aborted",
                    partial_result=processor.get_result(),
                )
            if not saw_terminal and processor.process_line(line):
                # Processor saw a terminal event; ask the CLI to exit cleanly,
                # but keep looping so the reader thread can drain stdout to EOF.
                process.terminate()
                saw_terminal = True

        # stdout fully drained by reader; communicate() only needs stderr.
        # Floor at 100ms: even if the deadline expired, give the process a
        # brief grace window to exit before we escalate to kill().
        wait_remaining = max(0.1, deadline - time.monotonic())
        try:
            _, stderr = process.communicate(timeout=wait_remaining)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate()
            return _timeout_payload(cli, processor, timeout_ms)

        return build_final_response(
            cli, process.returncode, processor.get_result(), stdout_lines, stderr
        )
    except (OSError, ValueError) as e:
        # OSError covers I/O failures on the pipe; ValueError covers reading
        # from a closed file. Anything else propagates so it's not silently
        # swallowed.
        process.kill()
        return _error_response(
            cli, 1, f"{type(e).__name__}: {e}", partial_result=processor.get_result()
        )


def execute_agent(inv: AgentInvocation, timeout_ms: int = 600000) -> dict:
    """Execute agent CLI for the given invocation. Returns a response dict.

    Response shape: ``{result, exit_code, status, cli, error?}``.
    """
    command, args, env_override = build_invocation_args(inv)
    proc_env = {**os.environ, **env_override} if env_override else None

    try:
        # stdin=DEVNULL: sub-agent CLIs (notably codex) probe stdin for "additional
        # input" and block reading from a TTY inherited from the parent. We never
        # have stdin to give them.
        process = subprocess.Popen(
            [command, *args],
            cwd=inv.cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=proc_env,
        )
    except FileNotFoundError:
        return _error_response(inv.cli, 127, f"CLI not found: {command}")
    except OSError as e:
        return _error_response(inv.cli, 1, f"{type(e).__name__}: {e}")

    return _drive_process(process, inv.cli, timeout_ms)
