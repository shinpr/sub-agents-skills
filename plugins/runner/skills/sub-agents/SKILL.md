---
name: sub-agents
description: Run agent definitions as sub-agents. Use when the user names an agent or sub-agent to run, references an agent definition, or delegates a task to an agent.
allowed-tools: Bash Read
---

# Sub-Agents - External CLI AI Task Delegation

Spawns external CLI AIs (codex, claude, cursor-agent, glm, grok, gemini, opencode) as isolated sub-agents with dedicated context.

Workflow: discover available definitions, select one from the user request, execute it, and handle the JSON response.

## Resources

- **[run_subagent.py](scripts/run_subagent.py)** - Main execution script
- **[codex.md](references/codex.md)** - Read before first execution from Codex; covers permissions and timeout

**Script Path**: Use absolute path `{SKILL_DIR}/scripts/run_subagent.py` where `{SKILL_DIR}` is the directory containing this SKILL.md file.

## Interpreting User Requests

Extract parameters from user's natural language request:

| Parameter | Source |
|-----------|--------|
| `--agent` | Agent name from the user request or workflow selection |
| `--prompt` | Task instruction part (excluding agent specification) |
| `--cwd` | Current working directory (absolute path) |
| `--cli` | Backend override explicitly requested by the user; otherwise omit |
| `--timeout` | Timeout explicitly requested by the user, converted to milliseconds; otherwise omit (default: 600000) |

**Example**:
"Run code-reviewer on src/"
→ `--agent code-reviewer --prompt "Review src/" --cwd $(pwd)`

## Important: Permission and Timeout

This script executes external CLIs that require elevated permissions.

**Before first execution:**
1. Request elevated permissions via your CLI's tool parameters
2. Set tool timeout to match `--timeout` argument (default: 600000ms)

**For Codex CLI** (most common permission issues): See [references/codex.md](references/codex.md) for exact JSON parameter format.

## Workflow

### Step 1: List Available Agents

**Always list agents first** to discover available definitions:

```bash
{SKILL_DIR}/scripts/run_subagent.py --list
```

Output:
```json
{"agents": [{"name": "code-reviewer", "description": "Reviews code..."}], "agents_dir": "/path/.agents"}
```

When the user provides an agent name, select it if it appears in the result. If
it is absent, report the available definitions and wait for a selection. When
the result is empty, provide the Agent Definition Format below and wait for the
user to add a definition.

When the user leaves the agent selection open:

| Available agents | Action |
|------------------|--------|
| 0 | Report that no definitions are available, provide the Agent Definition Format below, and wait |
| 1 | Select it |
| 2+ | Show names and descriptions, then ask the user to select one |

### Step 2: Execute Agent

```bash
{SKILL_DIR}/scripts/run_subagent.py \
  --agent <name> \
  --prompt "<task>" \
  --cwd <absolute-path>
```

Append `--cli <backend>` when the user specifies a backend. Append
`--timeout <milliseconds>` when the user specifies a timeout.

### Step 3: Handle Response

Parse JSON output and check `status` field:

```json
{"result": "...", "exit_code": 0, "status": "success", "cli": "claude"}
```

**By status:**

| status | Meaning | Action |
|--------|---------|--------|
| `success` | Task completed | Use `result` directly |
| `partial` | Timeout but has output | Review partial `result`, may need retry |
| `error` | Execution failed | Check `error` and `exit_code`; retry after satisfying the reported requirement |

For configuration or credential errors, retry after the required external
configuration has changed.

**By exit_code** (when status is `error`):

| exit_code | Meaning | Resolution |
|-----------|---------|------------|
| 0 | Success | - |
| 124 | Timeout | Increase `--timeout` or simplify task |
| 127 | CLI not found | Install required CLI (claude, codex, etc.) |
| 1 | General error | Check `error` field in response |

## Agent Definition Location

| Priority | Source | Path |
|----------|--------|------|
| 1 | Environment variable | `$SUB_AGENTS_DIR` |
| 2 | Default | `{cwd}/.agents/` |

To customize: `export SUB_AGENTS_DIR=/custom/path`

## Agent Definition Format

Place `.md` files in `.agents/` directory:

```markdown
---
run-agent: claude
model: sonnet
permission: safe-edit
---

# Agent Name

Brief description of agent's purpose.

## Task
What this agent does.

## Output Format
How results should be structured.
```

`run-agent` supplies the backend; an explicit `--cli` argument overrides it.

**Frontmatter fields:**

| Field | Values | Description |
|-------|--------|-------------|
| `run-agent` | `codex`, `claude`, `cursor-agent`, `glm`, `grok`, `gemini`, `opencode` | Which CLI executes this agent |
| `model` | Backend-specific model name (optional) | Model passed to the selected CLI; omit to use its configured default |
| `effort` | Backend/model-specific reasoning level or OpenCode variant (optional) | Advanced: forwarded as an opaque value. Confirm support for the selected model before setting; omit to use its default. Unsupported on `cursor-agent` and `gemini` |
| `permission` | `read-only`, `safe-edit` (default), `yolo` | `read-only` for investigation, `safe-edit` for workspace edits, or `yolo` to bypass approvals and sandboxing |
