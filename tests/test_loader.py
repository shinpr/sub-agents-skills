"""Tests for _loader: frontmatter parsing, agent file discovery, validation."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from _loader import (
    DEFAULT_PERMISSION,
    PERMISSION_VALUES,
    extract_description,
    get_agents_dir,
    list_agents,
    load_agent,
    parse_frontmatter,
    validate_permission,
)


class TestParseFrontmatter:
    def test_with_frontmatter(self) -> None:
        content = """---
run-agent: claude
---

# Agent Name

Body content here.
"""
        frontmatter, body = parse_frontmatter(content)
        assert frontmatter == {"run-agent": "claude"}
        assert "# Agent Name" in body
        assert "---" not in body

    def test_without_frontmatter(self) -> None:
        content = """# Agent Name

No frontmatter here.
"""
        frontmatter, body = parse_frontmatter(content)
        assert frontmatter == {}
        assert body == content


class TestExtractDescription:
    def test_extracts_first_non_heading_line(self) -> None:
        body = "# Title\n\nThis is description.\n\nMore content."
        assert extract_description(body) == "This is description."

    def test_returns_empty_for_headings_only(self) -> None:
        body = "# Title\n## Subtitle"
        assert extract_description(body) == ""

    def test_truncates_long_descriptions(self) -> None:
        long_line = "a" * 150
        body = f"# Title\n\n{long_line}"
        result = extract_description(body)
        assert len(result) == 100

    def test_empty_body(self) -> None:
        assert extract_description("") == ""


class TestLoadAgentRejectsInvalidNames:
    """load_agent should reject names that could escape the agents directory."""

    def _make_agents_dir(self, tmpdir: str) -> str:
        agents_dir = Path(tmpdir) / "agents"
        agents_dir.mkdir()
        (agents_dir / "valid-agent.md").write_text("# Valid\n\nA valid agent.")
        return str(agents_dir)

    def test_loads_simple_hyphenated_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            agent = load_agent(agents_dir, "valid-agent")
            assert "Valid" in agent.system_context
            assert agent.file_path.endswith("valid-agent.md")

    @pytest.mark.parametrize(
        "name",
        ["../etc/passwd", "/etc/passwd", "sub/agent", "sub\\agent", "", "agent;rm -rf", ".hidden"],
    )
    def test_rejects_names_outside_the_agents_directory(self, name: str) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, name)


class TestLoadAgent:
    def test_load_agent_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_file = Path(tmpdir) / "test-agent.md"
            agent_file.write_text("""---
run-agent: codex
---

# Test Agent

This is a test agent.

## Instructions
Do something.
""")
            agent = load_agent(tmpdir, "test-agent")
            assert agent.run_agent == "codex"
            assert "# Test Agent" in agent.system_context
            assert "This is a test agent." in agent.description
            assert agent.file_path.endswith("test-agent.md")
            assert agent.model is None
            assert agent.effort is None

    def test_agent_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, pytest.raises(FileNotFoundError):
            load_agent(tmpdir, "nonexistent")


class TestListAgents:
    def test_list_multiple_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "agent-a.md").write_text("# Agent A\n\nFirst agent.")
            (Path(tmpdir) / "agent-b.md").write_text("# Agent B\n\nSecond agent.")
            agents = list_agents(tmpdir)
            assert len(agents) == 2
            names = [a["name"] for a in agents]
            assert "agent-a" in names
            assert "agent-b" in names

    def test_list_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agents = list_agents(tmpdir)
            assert agents == []

    def test_list_nonexistent_directory(self) -> None:
        agents = list_agents("/nonexistent/path")
        assert agents == []


class TestGetAgentsDir:
    def test_args_priority(self) -> None:
        result = get_agents_dir("/custom/path", "/some/cwd")
        assert result == "/custom/path"

    def test_env_priority(self) -> None:
        with patch.dict("os.environ", {"SUB_AGENTS_DIR": "/env/path"}):
            result = get_agents_dir(None, "/some/cwd")
            assert result == "/env/path"

    def test_cwd_fallback(self) -> None:
        result = get_agents_dir(None, "/some/cwd")
        # get_agents_dir builds the path with pathlib, so the separator is
        # platform-native ("/" on POSIX, "\\" on Windows). Compare against the
        # same native construction rather than a hardcoded forward-slash path.
        assert result == str(Path("/some/cwd") / ".agents")


class TestValidatePermission:
    def test_returns_default_for_empty_string(self) -> None:
        assert validate_permission("") == DEFAULT_PERMISSION

    def test_accepts_valid_values(self) -> None:
        for value in PERMISSION_VALUES:
            assert validate_permission(value) == value

    @pytest.mark.parametrize("value", ["dangerous", "readonly"])
    def test_rejects_unknown_value(self, value: str) -> None:
        with pytest.raises(ValueError, match="Invalid permission"):
            validate_permission(value)


class TestLoadAgentPermission:
    def test_default_permission_when_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.md").write_text("---\nrun-agent: codex\n---\n# A\n")
            agent = load_agent(tmpdir, "a")
            assert agent.permission == DEFAULT_PERMISSION

    @pytest.mark.parametrize("permission", ["read-only", "yolo"])
    def test_explicit_permission_is_loaded(self, permission: str) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.md").write_text(
                f"---\nrun-agent: codex\npermission: {permission}\n---\n# A\n"
            )
            agent = load_agent(tmpdir, "a")
            assert agent.permission == permission


class TestLoadAgentModel:
    def test_model_when_specified(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.md").write_text(
                "---\nrun-agent: opencode\nmodel: test-provider/test-model\n---\n# A\n"
            )
            agent = load_agent(tmpdir, "a")
            assert agent.model == "test-provider/test-model"

    @pytest.mark.parametrize("value", ["", "''", '""'])
    def test_empty_model_uses_cli_default(self, value: str) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.md").write_text(f"---\nrun-agent: codex\nmodel: {value}\n---\n# A\n")
            agent = load_agent(tmpdir, "a")
            assert agent.model is None


class TestLoadAgentEffort:
    def test_effort_when_specified(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.md").write_text(
                "---\nrun-agent: glm\nmodel: glm-5.2\neffort: max\n---\n# A\n"
            )
            agent = load_agent(tmpdir, "a")
            assert agent.effort == "max"

    @pytest.mark.parametrize("value", ["", "''", '""'])
    def test_empty_effort_is_omitted(self, value: str) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.md").write_text(
                f"---\nrun-agent: codex\neffort: {value}\n---\n# A\n"
            )
            agent = load_agent(tmpdir, "a")
            assert agent.effort is None
