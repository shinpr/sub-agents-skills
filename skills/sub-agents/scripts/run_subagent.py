#!/usr/bin/env python3
"""
run_subagent.py - Execute external CLI AIs as sub-agents

Usage:
    scripts/run_subagent.py --agent <name> --prompt "<task>" --cwd <path>
    scripts/run_subagent.py --list

Supported CLIs: claude, cursor-agent, codex, gemini

Environment:
    SUB_AGENTS_DIR: Override default agents directory ({cwd}/.agents/)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# =============================================================================
# Agent Loader - frontmatter parsing and system context extraction
# =============================================================================


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """
    Parse YAML frontmatter from markdown content.
    Returns (frontmatter_dict, body_without_frontmatter)
    """
    pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(pattern, content, re.DOTALL)

    if not match:
        return {}, content

    frontmatter_raw = match.group(1)
    body = match.group(2)

    # Simple YAML parsing (key: value only, no nested structures)
    frontmatter = {}
    for line in frontmatter_raw.split("\n"):
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip().strip("\"'")

    return frontmatter, body


def extract_description(body: str) -> str:
    """Extract description from first non-heading line of body."""
    for line in body.strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:100]
    return ""


_AGENT_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def validate_agent_name(agent_name: str) -> str:
    """
    Validate agent name to prevent path traversal.
    Returns the name if valid, raises ValueError otherwise.
    """
    if not agent_name or not _AGENT_NAME_PATTERN.match(agent_name):
        raise ValueError(f"Invalid agent name: {agent_name!r}")
    return agent_name


def load_agent(agents_dir: str, agent_name: str) -> tuple[str | None, str, str]:
    """
    Load agent definition file and extract run-agent setting.
    Returns (run_agent_cli, system_context, description)
    """
    validate_agent_name(agent_name)
    agents_path = Path(agents_dir)

    # Try .md first, then .txt
    for ext in [".md", ".txt"]:
        agent_file = agents_path / f"{agent_name}{ext}"
        # Defense in depth: verify resolved path stays within agents_dir
        resolved = agent_file.resolve()
        if not str(resolved).startswith(str(agents_path.resolve())):
            raise ValueError(f"Invalid agent name: {agent_name!r}")
        if resolved.exists():
            content = resolved.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter(content)

            run_agent = frontmatter.get("run-agent")
            description = extract_description(body)

            return run_agent, body.strip(), description

    raise FileNotFoundError(f"Agent definition not found: {agent_name}")


def list_agents(agents_dir: str) -> list[dict]:
    """
    List all available agents in the directory.
    Returns list of {"name": str, "description": str}
    """
    agents_path = Path(agents_dir)
    agents = []
    seen_names: set[str] = set()

    if not agents_path.exists():
        return agents

    for ext in [".md", ".txt"]:
        for agent_file in agents_path.glob(f"*{ext}"):
            name = agent_file.stem
            # Skip if already added (prefer .md over .txt)
            if name in seen_names:
                continue
            seen_names.add(name)

            try:
                content = agent_file.read_text(encoding="utf-8")
                _, body = parse_frontmatter(content)
                description = extract_description(body)
                agents.append({"name": name, "description": description})
            except Exception:
                agents.append({"name": name, "description": ""})

    return sorted(agents, key=lambda a: a["name"])


def get_agents_dir(args_agents_dir: str | None, args_cwd: str | None) -> str:
    """
    Determine agents directory.
    Priority: --agents-dir > SUB_AGENTS_DIR > {cwd}/.agents/
    """
    if args_agents_dir:
        return args_agents_dir

    env_dir = os.environ.get("SUB_AGENTS_DIR")
    if env_dir:
        return env_dir

    if args_cwd:
        return str(Path(args_cwd) / ".agents")

    # Fallback for --list without --cwd
    return str(Path.cwd() / ".agents")


# =============================================================================
# CLI Resolver - determine which CLI to use
# =============================================================================


def detect_caller_cli() -> str | None:
    """
    Detect which CLI is calling this script based on environment.
    Returns: 'claude', 'cursor-agent', 'codex', 'gemini', or None
    """
    # Check common environment indicators
    if os.environ.get("CLAUDE_CODE"):
        return "claude"
    if os.environ.get("CURSOR_AGENT"):
        return "cursor-agent"
    if os.environ.get("CODEX_CLI"):
        return "codex"
    if os.environ.get("GEMINI_CLI"):
        return "gemini"

    # Check parent process name (best effort)
    try:
        ppid = os.getppid()
        cmdline_path = f"/proc/{ppid}/cmdline"
        if os.path.exists(cmdline_path):
            with open(cmdline_path) as f:
                cmdline = f.read().lower()
                if "claude" in cmdline:
                    return "claude"
                if "cursor" in cmdline:
                    return "cursor-agent"
                if "codex" in cmdline:
                    return "codex"
                if "gemini" in cmdline:
                    return "gemini"
    except Exception:
        pass

    return None


def resolve_cli(frontmatter_cli: str | None, default: str = "codex") -> str:
    """
    Resolve which CLI to use.
    Priority: frontmatter > caller detection > default
    """
    if frontmatter_cli:
        valid_clis = {"claude", "cursor-agent", "codex", "gemini"}
        if frontmatter_cli in valid_clis:
            return frontmatter_cli

    detected = detect_caller_cli()
    if detected:
        return detected

    return default


# =============================================================================
# Stream Processor - parse CLI output
# =============================================================================


class StreamProcessor:
    """Process streaming JSON output from various CLIs."""

    def __init__(self):
        self.result_json = None
        self.gemini_parts = []
        self.codex_messages = []
        self.is_gemini = False
        self.is_codex = False

    def process_line(self, line: str) -> bool:
        """
        Process a line from CLI output.
        Returns True when result is ready, False to continue.
        """
        line = line.strip()
        if not line or self.result_json is not None:
            return False

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return False

        # Detect Gemini format
        if data.get("type") == "init":
            self.is_gemini = True
            return False

        # Detect Codex format
        if data.get("type") == "thread.started":
            self.is_codex = True
            return False

        # Gemini: accumulate assistant messages
        if self.is_gemini and data.get("type") == "message" and data.get("role") == "assistant":
            content = data.get("content", "")
            if isinstance(content, str):
                self.gemini_parts.append(content)
            return False

        # Codex: accumulate agent_message items
        if self.is_codex and data.get("type") == "item.completed":
            item = data.get("item", {})
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                self.codex_messages.append(item["text"])
            return False

        # Codex: turn.completed signals end
        if self.is_codex and data.get("type") == "turn.completed":
            self.result_json = {
                "type": "result",
                "result": "\n".join(self.codex_messages),
                "status": "success",
            }
            return True

        # Result type signals completion
        if data.get("type") == "result":
            if self.is_gemini:
                self.result_json = {
                    "type": "result",
                    "result": "".join(self.gemini_parts),
                    "status": data.get("status", "success"),
                }
            else:
                self.result_json = data
            return True

        # Fallback: first valid JSON without type field
        if "type" not in data:
            self.result_json = data
            return True

        return False

    def get_result(self):
        return self.result_json


# =============================================================================
# Agent Executor - run CLI and capture output
# =============================================================================


def build_command(cli: str, prompt: str) -> tuple[str, list]:
    """Build command and arguments for the specified CLI."""

    if cli == "codex":
        return "codex", ["exec", "--json", prompt]

    if cli == "claude":
        return "claude", ["--output-format", "stream-json", "--verbose", "-p", prompt]

    if cli == "gemini":
        return "gemini", ["--output-format", "stream-json", "-p", prompt]

    if cli == "cursor-agent":
        args = ["--output-format", "json", "-p", prompt]
        api_key = os.environ.get("CLI_API_KEY")
        if api_key:
            args.extend(["-a", api_key])
        return "cursor-agent", args

    raise ValueError(f"Unknown CLI: {cli}")


def execute_agent(
    cli: str, system_context: str, prompt: str, cwd: str, timeout: int = 600000
) -> dict:
    """
    Execute agent CLI and return result.

    Returns: {
        "result": str,
        "exit_code": int,
        "status": "success" | "partial" | "error",
        "cli": str
    }
    """
    # Format prompt with system context
    formatted_prompt = f"[System Context]\n{system_context}\n\n[User Prompt]\n{prompt}"

    command, args = build_command(cli, formatted_prompt)
    timeout_sec = timeout / 1000

    try:
        process = subprocess.Popen(
            [command] + args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        processor = StreamProcessor()
        stdout_lines = []

        try:
            # Read stdout line by line
            for line in iter(process.stdout.readline, ""):
                stdout_lines.append(line)
                if processor.process_line(line):
                    process.terminate()
                    break

            # Wait for process to finish
            try:
                _, stderr = process.communicate(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                process.kill()
                _, stderr = process.communicate()

                result = processor.get_result()
                return {
                    "result": result.get("result", "") if result else "",
                    "exit_code": 124,
                    "status": "partial" if result else "error",
                    "cli": cli,
                    "error": f"Timeout after {timeout}ms",
                }

            result = processor.get_result()
            exit_code = process.returncode or 0

            # Determine status
            if exit_code == 0 or exit_code in (143, -15) and result:
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
                error_msg = f"CLI exited with code {exit_code}"
                if stderr and stderr.strip():
                    error_msg += f": {stderr.strip()}"
                response["error"] = error_msg
            return response

        except Exception as e:
            process.kill()
            result = processor.get_result()
            return {
                "result": result.get("result", "") if result else "",
                "exit_code": 1,
                "status": "error",
                "cli": cli,
                "error": str(e),
            }

    except FileNotFoundError:
        return {
            "result": "",
            "exit_code": 127,
            "status": "error",
            "cli": cli,
            "error": f"CLI not found: {command}",
        }
    except Exception as e:
        return {"result": "", "exit_code": 1, "status": "error", "cli": cli, "error": str(e)}


# =============================================================================
# Main
# =============================================================================


def main():
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

    # Handle --list
    if args.list:
        agents_dir = get_agents_dir(args.agents_dir, args.cwd)
        agents = list_agents(agents_dir)
        print(json.dumps({"agents": agents, "agents_dir": agents_dir}, ensure_ascii=False))
        sys.exit(0)

    # Validate required args for execution
    if not args.agent:
        print(
            json.dumps(
                {"result": "", "exit_code": 1, "status": "error", "error": "--agent is required"}
            )
        )
        sys.exit(1)

    if not args.prompt:
        print(
            json.dumps(
                {"result": "", "exit_code": 1, "status": "error", "error": "--prompt is required"}
            )
        )
        sys.exit(1)

    if not args.cwd:
        print(
            json.dumps(
                {"result": "", "exit_code": 1, "status": "error", "error": "--cwd is required"}
            )
        )
        sys.exit(1)

    # Validate cwd
    if not os.path.isabs(args.cwd):
        print(
            json.dumps(
                {
                    "result": "",
                    "exit_code": 1,
                    "status": "error",
                    "error": "cwd must be an absolute path",
                }
            )
        )
        sys.exit(1)

    if not os.path.isdir(args.cwd):
        print(
            json.dumps(
                {
                    "result": "",
                    "exit_code": 1,
                    "status": "error",
                    "error": f"cwd does not exist: {args.cwd}",
                }
            )
        )
        sys.exit(1)

    # Determine agents directory
    agents_dir = get_agents_dir(args.agents_dir, args.cwd)

    # Load agent definition
    try:
        run_agent_cli, system_context, _ = load_agent(agents_dir, args.agent)
    except FileNotFoundError as e:
        print(json.dumps({"result": "", "exit_code": 1, "status": "error", "error": str(e)}))
        sys.exit(1)

    # Resolve CLI
    cli = args.cli or resolve_cli(run_agent_cli)

    # Execute
    result = execute_agent(
        cli=cli,
        system_context=system_context,
        prompt=args.prompt,
        cwd=args.cwd,
        timeout=args.timeout,
    )

    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    main()
