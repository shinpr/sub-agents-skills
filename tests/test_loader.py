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
    def test_with_frontmatter(self):
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

    def test_without_frontmatter(self):
        content = """# Agent Name

No frontmatter here.
"""
        frontmatter, body = parse_frontmatter(content)
        assert frontmatter == {}
        assert body == content


class TestExtractDescription:
    def test_extracts_first_non_heading_line(self):
        body = "# Title\n\nThis is description.\n\nMore content."
        assert extract_description(body) == "This is description."

    def test_returns_empty_for_headings_only(self):
        body = "# Title\n## Subtitle"
        assert extract_description(body) == ""

    def test_truncates_long_descriptions(self):
        long_line = "a" * 150
        body = f"# Title\n\n{long_line}"
        result = extract_description(body)
        assert len(result) == 100

    def test_empty_body(self):
        assert extract_description("") == ""


class TestLoadAgentRejectsInvalidNames:
    """load_agent should reject names that could escape the agents directory."""

    def _make_agents_dir(self, tmpdir):
        agents_dir = Path(tmpdir) / "agents"
        agents_dir.mkdir()
        (agents_dir / "valid-agent.md").write_text("# Valid\n\nA valid agent.")
        return str(agents_dir)

    def test_loads_simple_hyphenated_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            _, context, _, file_path, _ = load_agent(agents_dir, "valid-agent")
            assert "Valid" in context
            assert file_path.endswith("valid-agent.md")

    def test_rejects_parent_directory_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, "../etc/passwd")

    def test_rejects_nested_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, "../../secret")

    def test_rejects_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, "/etc/passwd")

    def test_rejects_forward_slash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, "sub/agent")

    def test_rejects_backslash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, "sub\\agent")

    def test_rejects_empty_string(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, "")

    def test_rejects_shell_metacharacters(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, "agent;rm -rf")

    def test_rejects_leading_dot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = self._make_agents_dir(tmpdir)
            with pytest.raises(ValueError, match="Invalid agent name"):
                load_agent(agents_dir, ".hidden")


class TestLoadAgent:
    def test_load_agent_md(self):
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
            run_agent, system_context, desc, file_path, permission = load_agent(
                tmpdir, "test-agent"
            )
            assert run_agent == "codex"
            assert "# Test Agent" in system_context
            assert "This is a test agent." in desc
            assert file_path.endswith("test-agent.md")
            assert permission == DEFAULT_PERMISSION

    def test_agent_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir, pytest.raises(FileNotFoundError):
            load_agent(tmpdir, "nonexistent")


class TestListAgents:
    def test_list_multiple_agents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "agent-a.md").write_text("# Agent A\n\nFirst agent.")
            (Path(tmpdir) / "agent-b.md").write_text("# Agent B\n\nSecond agent.")
            agents = list_agents(tmpdir)
            assert len(agents) == 2
            names = [a["name"] for a in agents]
            assert "agent-a" in names
            assert "agent-b" in names

    def test_list_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agents = list_agents(tmpdir)
            assert agents == []

    def test_list_nonexistent_directory(self):
        agents = list_agents("/nonexistent/path")
        assert agents == []


class TestGetAgentsDir:
    def test_args_priority(self):
        result = get_agents_dir("/custom/path", "/some/cwd")
        assert result == "/custom/path"

    def test_env_priority(self):
        with patch.dict("os.environ", {"SUB_AGENTS_DIR": "/env/path"}):
            result = get_agents_dir(None, "/some/cwd")
            assert result == "/env/path"

    def test_cwd_fallback(self):
        result = get_agents_dir(None, "/some/cwd")
        assert result == "/some/cwd/.agents"


class TestValidatePermission:
    def test_returns_default_for_none(self):
        assert validate_permission(None) == DEFAULT_PERMISSION

    def test_returns_default_for_empty_string(self):
        assert validate_permission("") == DEFAULT_PERMISSION

    def test_accepts_valid_values(self):
        for value in PERMISSION_VALUES:
            assert validate_permission(value) == value

    def test_rejects_invalid_value(self):
        with pytest.raises(ValueError, match="Invalid permission"):
            validate_permission("dangerous")

    def test_rejects_typo(self):
        with pytest.raises(ValueError, match="Invalid permission"):
            validate_permission("readonly")


class TestLoadAgentPermission:
    def test_default_permission_when_omitted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.md").write_text("---\nrun-agent: codex\n---\n# A\n")
            _, _, _, _, permission = load_agent(tmpdir, "a")
            assert permission == DEFAULT_PERMISSION

    def test_explicit_read_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.md").write_text(
                "---\nrun-agent: codex\npermission: read-only\n---\n# A\n"
            )
            _, _, _, _, permission = load_agent(tmpdir, "a")
            assert permission == "read-only"

    def test_explicit_yolo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.md").write_text(
                "---\nrun-agent: claude\npermission: yolo\n---\n# A\n"
            )
            _, _, _, _, permission = load_agent(tmpdir, "a")
            assert permission == "yolo"

    def test_invalid_permission_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.md").write_text(
                "---\nrun-agent: codex\npermission: dangerous\n---\n# A\n"
            )
            with pytest.raises(ValueError, match="Invalid permission"):
                load_agent(tmpdir, "a")
