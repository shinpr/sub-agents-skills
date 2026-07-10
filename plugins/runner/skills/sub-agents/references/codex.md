# Codex-Specific Notes

Prevents sandbox denial, timeout mismatch, and premature termination of quiet nested runs.

## Permission

Nested CLIs need access to Codex session state and external CLI binaries.
Start `run_subagent.py` with escalated sandbox permissions to avoid a known
fail-then-retry path, including:

- `Operation not permitted`
- failure to initialize Codex app-server or session state
- failure to access external CLI binaries

## Timeout

- Script arg: `--timeout 600000`
- Surrounding tool timeout: at least `600000ms`

Long nested runs are normal. Keep the command attached until one terminal
condition occurs:

- `run_subagent.py` exits and returns its final JSON response.
- The process exits before returning valid JSON.
- The configured timeout expires.

Progress silence is not a terminal condition. Continue waiting on the same
running command.

## Sub-Agent Execution

When running a sub-agent, operate as a broker: carry one run from start to terminal state.

### Allowed actions

1. Validate the requested agent
2. Start `run_subagent.py`
3. Stay attached to that run until a terminal condition occurs
4. Return the sub-agent result, or the failure/timeout outcome

### If the user asks a question mid-run

Answer briefly, then return to waiting on the same run.

## Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `Operation not permitted (os error 1)` | Sandbox restriction | Use escalated permissions |
| Tool call ends before `--timeout` | Surrounding tool timeout is too short | Allow at least the script timeout |
| `permission denied` on session files | Sandbox restriction | Use escalated permissions |
