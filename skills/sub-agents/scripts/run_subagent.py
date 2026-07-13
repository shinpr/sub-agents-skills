#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure sibling modules import correctly when invoked via absolute path.
sys.path.insert(0, str(Path(__file__).parent))

from _builder import AgentInvocation  # noqa: E402
from _constants import DEFAULT_TIMEOUT_MS, SUPPORTED_CLIS_HELP  # noqa: E402
from _executor import execute_agent  # noqa: E402
from _loader import get_agents_dir, list_agents, load_agent  # noqa: E402
from _resolver import resolve_cli  # noqa: E402


def _print_error(error: str, exit_code: int = 1, cli: str | None = None) -> None:
    payload = {"result": "", "exit_code": exit_code, "status": "error", "error": error}
    if cli is not None:
        payload["cli"] = cli
    print(json.dumps(payload))


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute external CLI AIs as sub-agents")
    parser.add_argument("--list", action="store_true", help="List available agents")
    parser.add_argument("--agent", help="Agent definition name")
    parser.add_argument("--prompt", help="Task prompt")
    parser.add_argument("--cwd", help="Working directory (absolute path)")
    parser.add_argument("--agents-dir", help="Directory containing agent definitions")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_MS,
        help=f"Timeout in ms (default: {DEFAULT_TIMEOUT_MS})",
    )
    parser.add_argument("--cli", help=f"Force specific CLI ({SUPPORTED_CLIS_HELP})")

    args = parser.parse_args()

    if args.list:
        agents_dir = get_agents_dir(args.agents_dir, args.cwd)
        agents = list_agents(agents_dir)
        print(json.dumps({"agents": agents, "agents_dir": agents_dir}, ensure_ascii=False))
        sys.exit(0)

    if not args.agent:
        _print_error("Missing required argument: --agent.")
        sys.exit(1)
    if not args.prompt:
        _print_error("Missing required argument: --prompt.")
        sys.exit(1)
    if not args.cwd:
        _print_error("Missing required argument: --cwd.")
        sys.exit(1)
    if not os.path.isabs(args.cwd):
        _print_error(f"Invalid --cwd {args.cwd!r}: expected an absolute path.")
        sys.exit(1)
    if not os.path.isdir(args.cwd):
        _print_error(f"Invalid --cwd {args.cwd!r}: directory does not exist.")
        sys.exit(1)

    agents_dir = get_agents_dir(args.agents_dir, args.cwd)

    try:
        run_agent_cli, system_context, _, agent_file, permission, model, effort = load_agent(
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
        model=model,
        effort=effort,
    )

    try:
        result = execute_agent(invocation, timeout_ms=args.timeout)
    except ValueError as e:
        _print_error(str(e), cli=cli)
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    main()
