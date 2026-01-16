# Sub-Agents Skills

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Bring Claude Code–style sub-agents to any Agent Skills-compatible tool.

This skill lets you define task-specific AI agents (like "test-writer" or "code-reviewer") in markdown files, and execute them via Codex, Cursor CLI, Gemini CLI, or Claude Code backends.

## Which Version Should I Use?

This repository provides the **Agent Skills** version. There's also an [MCP version](https://github.com/shinpr/sub-agents-mcp).

| | Skills (this repo) | MCP |
|---|---|---|
| **Best for** | Codex | Cursor, Claude Desktop, Windsurf |
| **Setup** | Copy files | Configure mcp.json |
| **Session management** | No | Yes |
| **Runtime dependency** | Python | Node.js |

**Quick decision:**
- Using **Codex**? → Use this Skills version
- Using **Cursor, Claude Desktop, or Windsurf**? → Use [MCP version](https://github.com/shinpr/sub-agents-mcp)
- Using **Claude Code**? → It has built-in sub-agents. Use this only if you want portable agent definitions.

## Why?

Claude Code offers powerful sub-agent workflows—but they're limited to its own environment. This skill makes that workflow portable through the Agent Skills standard, so any compatible tool can use the same agents.

- Define agents once, reuse across tools
- Team members can share agents regardless of IDE
- No MCP server—just a Python script

## Table of Contents

- [Which Version Should I Use?](#which-version-should-i-use)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Usage Examples](#usage-examples)
- [Writing Effective Agents](#writing-effective-agents)
- [Agent Examples](#agent-examples)
- [Configuration Reference](#configuration-reference)
- [Troubleshooting](#troubleshooting)
- [Design Philosophy](#design-philosophy)
- [How It Works](#how-it-works)

## Prerequisites

- Python 3.8 or higher
- One of these execution engines (they actually run the sub-agents):
  - `codex` CLI (from Codex)
  - `cursor-agent` CLI (from Cursor)
  - `gemini` CLI (from Gemini CLI)
  - `claude` CLI (from Claude Code)
- An Agent Skills-compatible tool (Codex, Claude Code, etc.)

## Quick Start

### 1. Install the Skill

**Via curl:**
```bash
# For Codex
curl -fsSL https://raw.githubusercontent.com/shinpr/sub-agents-skills/main/install.sh | bash -s -- --target ~/.codex/skills

# For Claude Code (already has built-in sub-agents, but you can use this for custom definitions)
curl -fsSL https://raw.githubusercontent.com/shinpr/sub-agents-skills/main/install.sh | bash -s -- --target ~/.claude/skills
```

**Manually:**
```bash
git clone https://github.com/shinpr/sub-agents-skills.git
cd sub-agents-skills
./install.sh --target ~/.codex/skills
```

### 2. Create Your First Agent

Create a `.agents/` folder in your project and add `code-reviewer.md`:

```markdown
---
run-agent: codex
---

# Code Reviewer

Review code for quality and maintainability issues.

## Task
- Find bugs and potential issues
- Suggest improvements
- Check code style consistency

## Done When
- All target files reviewed
- Issues listed with explanations
```

The `run-agent` frontmatter specifies which CLI executes this agent (`codex`, `claude`, `cursor-agent`, or `gemini`). See [Writing Effective Agents](#writing-effective-agents) for more on agent design.

### 3. Install Your Execution Engine

Pick one based on which CLI you want sub-agents to use:

**For Codex users:**
```bash
npm install -g @openai/codex
```

**For Cursor users:**
```bash
# Install Cursor CLI (includes cursor-agent)
curl https://cursor.com/install -fsS | bash

# Authenticate (required before first use)
cursor-agent login
```

**For Gemini CLI users:**
```bash
npm install -g @google/gemini-cli

# Authenticate via browser (required before first use)
gemini
```

**For Claude Code users:**
```bash
# Option 1: Native install (recommended)
curl -fsSL https://claude.ai/install.sh | bash

# Option 2: NPM (requires Node.js 18+)
npm install -g @anthropic-ai/claude-code
```

### 4. Fix "Permission Denied" Errors When Running Shell Commands

Sub-agents may fail to execute shell commands with permission errors. This happens because sub-agents can't respond to interactive permission prompts.

**Recommended approach:**

1. Run your CLI tool directly with the task you want sub-agents to handle:
   ```bash
   codex           # For Codex users
   cursor-agent    # For Cursor users
   gemini          # For Gemini CLI users
   claude          # For Claude Code users
   ```

2. When prompted to allow commands (e.g., "Add Shell(cd), Shell(make) to allowlist?"), approve them

3. This automatically updates your configuration file, and those commands will now work when invoked via sub-agents

## Usage Examples

Just tell your AI to use an agent:

```
"Use the code-reviewer agent to check my UserService class"
```

```
"Use the test-writer agent to create unit tests for the auth module"
```

```
"Use the doc-writer agent to add JSDoc comments to all public methods"
```

Your AI automatically invokes the specialized agent and returns results.

**Tip:** Always include *what you want done* in your request—not just which agent to use. For example:

- ✅ "Use the code-reviewer agent **to check my UserService class**"
- ❌ "Use the code-reviewer agent" (too vague—the agent won't know what to review)

The more specific your task, the better the results.

## Writing Effective Agents

### The Single Responsibility Principle

Each agent should do **one thing well**. Avoid "swiss army knife" agents.

| ✅ Good | ❌ Bad |
|---------|--------|
| Reviews code for security issues | Reviews code, writes tests, and refactors |
| Writes unit tests for a module | Writes tests and fixes bugs it finds |

### Essential Structure

```markdown
---
run-agent: codex
---

# Agent Name

One-sentence purpose.

## Task
- Action 1
- Action 2

## Done When
- Criterion 1
- Criterion 2
```

### Frontmatter Options

| Field | Values | Description |
|-------|--------|-------------|
| `run-agent` | `claude`, `cursor-agent`, `codex`, `gemini` | Which CLI executes this agent |

If `run-agent` is not specified, the skill auto-detects the caller environment or defaults to `codex`.

### Keep Agents Self-Contained

Agents run in isolation with fresh context. Avoid:

- References to other agents ("then use X agent...")
- Assumptions about prior context ("continuing from before...")
- Scope creep beyond the stated purpose

### Advanced Patterns

For complex agents, consider adding:

- **Scope boundaries**: Explicitly state what's *out of scope*
- **Prohibited actions**: List common mistakes the agent should avoid
- **Output format**: Define structured output when needed

## Agent Examples

Each `.md` or `.txt` file in your `.agents/` folder becomes an agent. The filename becomes the agent name (e.g., `bug-investigator.md` → "bug-investigator").

**`bug-investigator.md`**
```markdown
---
run-agent: codex
---

# Bug Investigator

Investigate bug reports and identify root causes.

## Task
- Collect evidence from error logs, code, and git history
- Generate multiple hypotheses for the cause
- Trace each hypothesis to its root cause
- Report findings with supporting evidence

## Out of Scope
- Fixing the bug (investigation only)
- Making assumptions without evidence

## Done When
- At least 2 hypotheses documented with evidence
- Most likely cause identified with confidence level
- Affected code locations listed
```

For more advanced patterns (completion checklists, prohibited actions, structured output), see [claude-code-workflows/agents](https://github.com/shinpr/claude-code-workflows/tree/main/agents).

## Configuration Reference

### Agent Definition Location

| Priority | Source | Path |
|----------|--------|------|
| 1 | `--agents-dir` argument | Explicit path |
| 2 | Environment variable | `$SUB_AGENTS_DIR` |
| 3 | Default | `{cwd}/.agents/` |

To customize: `export SUB_AGENTS_DIR=/custom/path`

### CLI Selection Priority

1. `--cli` argument (explicit override)
2. Agent definition `run-agent` frontmatter
3. Auto-detect caller environment
4. Default: `codex`

### Script Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--list` | - | List available agents (no other params needed) |
| `--agent` | Yes* | Agent definition name from --list |
| `--prompt` | Yes* | Task description to delegate |
| `--cwd` | Yes* | Working directory (absolute path) |
| `--timeout` | No | Timeout ms (default: 300000) |
| `--cli` | No | Force CLI: `claude`, `cursor-agent`, `codex`, `gemini` |

*Required when not using --list

### Security Note

Agent definitions are system prompts that control what the sub-agent does. A malicious agent definition could instruct the sub-agent to read sensitive files, execute harmful commands, or exfiltrate data.

Only use agent definitions you've written yourself or from sources you trust. Review any third-party agent definitions before use.

## Troubleshooting

### Timeout errors or authentication failures

**If using Cursor CLI:**
Run `cursor-agent login` to authenticate. Sessions can expire, so just run this command again if you see auth errors.

**If using Claude Code:**
Make sure the CLI is properly installed and accessible.

**If using Gemini CLI:**
Run `gemini` once to authenticate via browser.

### Agent not found

Check that:
- Your agent file is in the `.agents/` directory (or path specified by `SUB_AGENTS_DIR`)
- The file has `.md` or `.txt` extension
- The filename uses hyphens or underscores (no spaces)

### CLI not found (exit code 127)

Install the required CLI:
- Codex: `npm install -g @openai/codex`
- Cursor: `curl https://cursor.com/install -fsS | bash`
- Gemini: `npm install -g @google/gemini-cli`
- Claude: `npm install -g @anthropic-ai/claude-code`

### Other execution errors

1. Verify the agent definition has valid `run-agent` frontmatter
2. Ensure your chosen CLI tool is installed and accessible
3. Check that `--cwd` is an absolute path to an existing directory

## Design Philosophy

### Why Independent Contexts?

Every sub-agent starts fresh. No shared state, no context from previous runs.

This means each call has some startup overhead, but you get predictable behavior—the same agent definition produces the same results regardless of what ran before. When you split a large task into sub-agents, each one focuses on its specific goal without interference from unrelated context.

The main agent stays lightweight too. It coordinates work without accumulating all the sub-agent context in its own window.

### Skills vs MCP

This skill does the same thing as [sub-agents-mcp](https://github.com/shinpr/sub-agents-mcp), just packaged differently.

Skills version:
- No server process, just copy files
- Python only (no Node.js)
- No session management

MCP version:
- Requires mcp.json config
- Has session management for multi-turn workflows
- Broader client support currently

## How It Works

Your AI reads the skill definition (SKILL.md), which tells it how to invoke the Python script. The script reads the agent definition (your `.agents/*.md` file), calls the appropriate CLI, and returns the result.

```
skills/sub-agents/
├── SKILL.md              # Instructions for the AI
├── scripts/
│   └── run_subagent.py   # Calls external CLIs
└── references/
    └── cli-formats.md    # CLI output format docs
```

## License

MIT
