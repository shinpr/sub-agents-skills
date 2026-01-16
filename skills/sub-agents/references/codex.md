# Codex-Specific Notes

Read this reference when executing sub-agents from Codex CLI.

## Where to Set Parameters

Codex CLI uses **Bash tool call parameters** for permissions and timeout. These are NOT command-line arguments—they are tool invocation settings.

```json
{
  "tool": "Bash",
  "parameters": {
    "command": "/path/to/scripts/run_subagent.py --agent <name> --prompt \"<task>\" --cwd /absolute/path --timeout 600000",
    "timeout_ms": 600000,
    "sandbox_permissions": "require_escalated"
  }
}
```

## Permission: Request Escalated BEFORE First Execution

Sub-agent CLIs require access to session files outside the sandbox.

**Setting:** `"sandbox_permissions": "require_escalated"` in Bash tool parameters.

Do not attempt a sandboxed run first—it will fail with:
```
Error: Fatal error: Codex cannot access session files at ~/.codex/sessions (permission denied)
```

## Timeout: Match timeout_ms with --timeout

Codex's Bash tool has its own timeout (`timeout_ms`), separate from the script's `--timeout` argument.

**Setting:** `"timeout_ms": 600000` in Bash tool parameters (must match `--timeout` value).

| --timeout | timeout_ms | Result |
|-----------|------------|--------|
| Not set | Not set | Script: 600000ms, Tool: ~10000ms → Tool kills script early |
| 600000 | Not set | Same problem |
| 600000 | 600000 | Correct: both aligned |

## Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `Operation not permitted (os error 1)` | Sandbox restriction | Use `sandbox_permissions:"require_escalated"` |
| `(no output)` after few seconds | Tool timeout too short | Set `timeout_ms` to match `--timeout` |
| `permission denied` on session files | Need elevated permissions | Request escalated permissions first |
