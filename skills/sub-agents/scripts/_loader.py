from __future__ import annotations

import os
import re
from pathlib import Path

PERMISSION_VALUES = ("read-only", "safe-edit", "yolo")
DEFAULT_PERMISSION = "safe-edit"

_AGENT_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse flat YAML frontmatter and return its fields and body."""
    pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(pattern, content, re.DOTALL)

    if not match:
        return {}, content

    frontmatter_raw = match.group(1)
    body = match.group(2)

    frontmatter = {}
    for line in frontmatter_raw.split("\n"):
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip().strip("\"'")

    return frontmatter, body


def extract_description(body: str) -> str:
    """Return first non-heading line of body, truncated to 100 chars."""
    for line in body.strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:100]
    return ""


def validate_agent_name(agent_name: str) -> str:
    if not agent_name or not _AGENT_NAME_PATTERN.match(agent_name):
        raise ValueError(
            f"Invalid agent name {agent_name!r}. Start with a letter or number; "
            "use only letters, numbers, '.', '_', or '-'."
        )
    return agent_name


def validate_permission(value: str | None) -> str:
    """Validate a permission value, defaulting empty values to safe-edit."""
    if value is None or value == "":
        return DEFAULT_PERMISSION
    if value not in PERMISSION_VALUES:
        allowed = ", ".join(PERMISSION_VALUES)
        raise ValueError(f"Invalid permission {value!r}. Choose one of: {allowed}.")
    return value


def load_agent(
    agents_dir: str, agent_name: str
) -> tuple[str | None, str, str, str, str, str | None, str | None]:
    validate_agent_name(agent_name)
    agents_path = Path(agents_dir)
    agents_root = agents_path.resolve()

    for ext in [".md", ".txt"]:
        agent_file = agents_path / f"{agent_name}{ext}"
        # Keep symlink targets within the agents directory.
        resolved = agent_file.resolve()
        if not resolved.is_relative_to(agents_root):
            raise ValueError(
                f"Agent definition {agent_name!r} resolves outside the agents directory."
            )
        if resolved.exists():
            content = resolved.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter(content)
            run_agent = frontmatter.get("run-agent")
            permission = validate_permission(frontmatter.get("permission"))
            model = frontmatter.get("model") or None
            effort = frontmatter.get("effort") or None
            description = extract_description(body)
            return run_agent, body.strip(), description, str(resolved), permission, model, effort

    raise FileNotFoundError(
        f"Agent definition {agent_name!r} was not found in {str(agents_root)!r}. "
        "Run --list to choose an available definition."
    )


def list_agents(agents_dir: str) -> list[dict]:
    """List agent names and descriptions, preferring Markdown definitions."""
    agents_path = Path(agents_dir)
    agents = []
    seen_names: set[str] = set()

    if not agents_path.exists():
        return agents

    for ext in [".md", ".txt"]:
        for agent_file in agents_path.glob(f"*{ext}"):
            name = agent_file.stem
            if name in seen_names:
                continue
            seen_names.add(name)

            try:
                content = agent_file.read_text(encoding="utf-8")
                _, body = parse_frontmatter(content)
                description = extract_description(body)
                agents.append({"name": name, "description": description})
            except (OSError, UnicodeDecodeError):
                # Preserve discoverability when description extraction fails.
                agents.append({"name": name, "description": ""})

    return sorted(agents, key=lambda a: a["name"])


def get_agents_dir(args_agents_dir: str | None, args_cwd: str | None) -> str:
    """Resolve the agents directory from argument, ``SUB_AGENTS_DIR``, or cwd."""
    if args_agents_dir:
        return args_agents_dir

    env_dir = os.environ.get("SUB_AGENTS_DIR")
    if env_dir:
        return env_dir

    if args_cwd:
        return str(Path(args_cwd) / ".agents")

    return str(Path.cwd() / ".agents")
