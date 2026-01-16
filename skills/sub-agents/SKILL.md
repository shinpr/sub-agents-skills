---
name: sub-agents
description: Execute external CLI AIs as isolated sub-agents for task delegation, parallel processing, and context separation. Use when delegating complex multi-step tasks, running parallel investigations, needing fresh context without current conversation history, or leveraging specialized agent definitions. Returns structured JSON with agent output, exit code, and execution status.
allowed-tools: Bash Read
---

# Sub-Agents - External CLI AI Task Delegation

Spawns external CLI AIs (claude, cursor-agent, codex, gemini) as isolated sub-agents with dedicated context.

## Resources

- **[run_subagent.py](scripts/run_subagent.py)** - Main execution script
- **[cli-formats.md](references/cli-formats.md)** - CLI output format details

**Execution**: Run scripts using absolute path from this skill's directory with your environment's Python.

## Interpreting User Requests

Extract parameters from user's natural language request:

| Parameter | Source |
|-----------|--------|
| --agent | Agent name from user request (if not specified, use --list to show candidates and ask user) |
| --prompt | Task instruction part (excluding agent specification) |
| --cwd | Current working directory |

**Example**:
"Run code-reviewer on src/"
→ `--agent code-reviewer --prompt "Review src/" --cwd $(pwd)`

## When to Use This Skill

| Scenario | Use Sub-Agent | Reason |
|----------|---------------|--------|
| Complex multi-step task | **Yes** | Isolated context prevents interference |
| Parallel investigation | **Yes** | Multiple agents can run simultaneously |
| Task needs fresh context | **Yes** | Sub-agent starts without conversation history |
| Specialized agent definition exists | **Yes** | Leverage pre-defined expert prompts |
| Simple single-step task | No | Direct execution is faster |
| Task requires current conversation context | No | Sub-agent cannot access parent context |

## Workflow

### Step 1: List Available Agents

**Always list agents first** to discover available definitions:

```bash
python scripts/run_subagent.py --list
```

Output:
```json
{"agents": [{"name": "code-reviewer", "description": "Reviews code..."}], "agents_dir": "/path/.agents"}
```

**If agents list is empty**: Agent definitions must be placed in `{cwd}/.agents/` directory.

### Step 2: Execute Agent

```bash
python scripts/run_subagent.py \
  --agent <name> \
  --prompt "<task>" \
  --cwd <absolute-path>
```

### Step 3: Handle Response

Parse JSON output and check `status` field:

```json
{"result": "...", "exit_code": 0, "status": "success", "cli": "claude"}
```

| status | Meaning | Action |
|--------|---------|--------|
| `success` | Task completed | Use `result` directly |
| `partial` | Timeout but has output | Review partial `result`, may need retry |
| `error` | Execution failed | Check `error` field, fix and retry |

## Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--list` | - | List available agents (no other params needed) |
| `--agent` | Yes* | Agent definition name from --list |
| `--prompt` | Yes* | Task description to delegate |
| `--cwd` | Yes* | Working directory (absolute path) |
| `--timeout` | No | Timeout ms (default: 300000) |
| `--cli` | No | Force CLI: `claude`, `cursor-agent`, `codex`, `gemini` |

*Required when not using --list

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
---

# Agent Name

Brief description of agent's purpose.

## Task
What this agent does.

## Output Format
How results should be structured.
```

**Critical**: The `run-agent` frontmatter determines which CLI executes the agent. See [cli-formats.md](references/cli-formats.md) for CLI-specific behaviors.

## CLI Selection Priority

1. `--cli` argument (explicit override)
2. Agent definition `run-agent` frontmatter
3. Auto-detect caller environment
4. Default: `codex`

## Error Handling

| exit_code | Meaning | Resolution |
|-----------|---------|------------|
| 0 | Success | - |
| 124 | Timeout | Increase `--timeout` or simplify task |
| 127 | CLI not found | Install required CLI (claude, codex, etc.) |
| 1 | General error | Check `error` field in response |

## Anti-patterns

| Anti-pattern | Problem | Correct Approach |
|--------------|---------|------------------|
| Not listing agents first | Unknown agent name causes error | Always run `--list` first |
| Relative path for --cwd | Validation fails | Use absolute path |
| Ignoring status field | Missing errors | Always check status before using result |
| Very long prompts | May exceed CLI limits | Break into smaller tasks |
