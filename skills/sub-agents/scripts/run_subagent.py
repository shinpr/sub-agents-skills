#!/usr/bin/env python3
"""run_subagent.py - Execute external CLI AIs as sub-agents.

Usage:
    scripts/run_subagent.py --agent <name> --prompt "<task>" --cwd <path>
    scripts/run_subagent.py --list

Supported CLIs: claude, cursor-agent, codex, gemini.

Environment:
    SUB_AGENTS_DIR: Override default agents directory ({cwd}/.agents/).
    CLI_API_KEY:    Forwarded as CURSOR_API_KEY to cursor-agent (env, never argv).

Implementation is split into sibling modules:
    _loader.py   - frontmatter parsing and agent discovery
    _resolver.py - CLI auto-detection
    _stream.py   - StreamProcessor (NDJSON parsing)
    _builder.py  - command/args construction per CLI
    _executor.py - subprocess driver and response shaping
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure sibling modules import correctly when invoked via absolute path.
sys.path.insert(0, str(Path(__file__).parent))

from _builder import AgentInvocation  # noqa: E402
from _executor import execute_agent  # noqa: E402
from _loader import get_agents_dir, list_agents, load_agent  # noqa: E402
from _resolver import resolve_cli  # noqa: E402


def _print_error(error: str, exit_code: int = 1) -> None:
    print(json.dumps({"result": "", "exit_code": exit_code, "status": "error", "error": error}))


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute external CLI AIs as sub-agents")
    parser.add_argument("--list", action="store_true", help="List available agents")
    parser.add_argument("--agent", help="Agent definition name")
    parser.add_argument("--prompt", help="Task prompt")
    parser.add_argument("--cwd", help="Working directory (absolute path)")
    parser.add_argument("--agents-dir", help="Directory containing agent definitions")
    parser.add_argument(
        "--timeout", type=int, default=600000, help="Timeout in ms (default: 600000)"
    )
    parser.add_argument("--cli", help="Force specific CLI (claude, cursor-agent, codex, gemini)")

    args = parser.parse_args()

    if args.list:
        agents_dir = get_agents_dir(args.agents_dir, args.cwd)
        agents = list_agents(agents_dir)
        print(json.dumps({"agents": agents, "agents_dir": agents_dir}, ensure_ascii=False))
        sys.exit(0)

    # Validate required args for execution
    if not args.agent:
        _print_error("--agent is required")
        sys.exit(1)
    if not args.prompt:
        _print_error("--prompt is required")
        sys.exit(1)
    if not args.cwd:
        _print_error("--cwd is required")
        sys.exit(1)
    if not os.path.isabs(args.cwd):
        _print_error("cwd must be an absolute path")
        sys.exit(1)
    if not os.path.isdir(args.cwd):
        _print_error(f"cwd does not exist: {args.cwd}")
        sys.exit(1)

    agents_dir = get_agents_dir(args.agents_dir, args.cwd)

    try:
        run_agent_cli, system_context, _, agent_file, permission = load_agent(
            agents_dir, args.agent
        )
    except (FileNotFoundError, ValueError) as e:
        _print_error(str(e))
        sys.exit(1)

    cli = args.cli or resolve_cli(run_agent_cli)
    invocation = AgentInvocation(
        cli=cli,
        prompt=args.prompt,
        cwd=args.cwd,
        system_context=system_context,
        agent_file=agent_file,
        permission=permission,
    )

    # Catch ValueError from build_invocation_args / permission_flags / TOML
    # escaping so unknown --cli values or unsafe agent paths surface as JSON
    # errors rather than tracebacks. All other CLI-side failures are already
    # shaped into the response by execute_agent.
    try:
        result = execute_agent(invocation, timeout_ms=args.timeout)
    except ValueError as e:
        _print_error(str(e))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    main()
