# Codex-Specific Notes

Prevents two common Codex failures: sandbox denial and timeout mismatch.

## Permission

This script accesses `~/.codex/sessions` and external CLI binaries.
Use escalated sandbox permissions from the first run for this skill.

## Timeout

- Script arg: `--timeout 600000`
- Tool param: `timeout_ms: 600000`
- Rule: both values must match

## Sub-Agent Execution

When running a sub-agent, operate as a broker: carry one run from start to terminal state.

### Allowed actions

1. Validate the requested agent
2. Start `run_subagent.py`
3. Stay attached to that run until it finishes, times out, or fails
4. Return the sub-agent result, or the failure/timeout outcome

### If the user asks a question mid-run

Answer briefly, then return to waiting on the same run.

## Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `Operation not permitted (os error 1)` | Sandbox restriction | Use escalated permissions |
| No output after a few seconds | Tool timeout too short | Align tool timeout with `--timeout` |
| `permission denied` on session files | Sandbox restriction | Use escalated permissions |
