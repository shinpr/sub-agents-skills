"""Tests for Codex marketplace packaging."""

from __future__ import annotations

import filecmp
import json
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

REPO_ROOT = Path(__file__).parent.parent


def test_codex_marketplace_uses_non_root_plugin_path():
    marketplace = json.loads(
        (REPO_ROOT / ".agents" / "plugins" / "marketplace.json").read_text()
    )

    [plugin] = marketplace["plugins"]

    assert plugin["name"] == "runner"
    assert plugin["source"] == {
        "source": "local",
        "path": "./plugins/runner",
    }


def test_codex_plugin_manifest_points_to_copied_skills():
    plugin_root = REPO_ROOT / "plugins" / "runner"
    manifest = json.loads((plugin_root / ".codex-plugin" / "plugin.json").read_text())

    skills_path = (plugin_root / manifest["skills"]).resolve()

    assert manifest["name"] == "runner"
    assert skills_path == (plugin_root / "skills").resolve()
    assert (skills_path / "sub-agents" / "SKILL.md").is_file()


def test_codex_plugin_skill_copy_matches_canonical_skill():
    canonical = REPO_ROOT / "skills" / "sub-agents"
    copied = REPO_ROOT / "plugins" / "runner" / "skills" / "sub-agents"

    assert _compare_dirs(canonical, copied) == []


def test_codex_plugin_default_prompt_uses_namespaced_skill():
    metadata = (
        REPO_ROOT
        / "plugins"
        / "runner"
        / "skills"
        / "sub-agents"
        / "agents"
        / "openai.yaml"
    ).read_text()

    assert "$runner:sub-agents" in metadata
    assert "Use $sub-agents" not in metadata


def test_manifest_versions_match_project_version():
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    project_version = project["project"]["version"]
    manifest_paths = [
        REPO_ROOT / ".claude-plugin" / "plugin.json",
        REPO_ROOT / "plugins" / "runner" / ".codex-plugin" / "plugin.json",
    ]

    for manifest_path in manifest_paths:
        manifest = json.loads(manifest_path.read_text())
        assert manifest["version"] == project_version

    marketplace = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    [plugin] = marketplace["plugins"]
    assert plugin["version"] == project_version


def _compare_dirs(left: Path, right: Path) -> list[str]:
    comparison = filecmp.dircmp(left, right)
    differences = [
        *(f"missing from plugin copy: {name}" for name in comparison.left_only),
        *(f"extra in plugin copy: {name}" for name in comparison.right_only),
        *(f"content differs: {name}" for name in comparison.diff_files),
    ]

    for subdir in comparison.common_dirs:
        differences.extend(_compare_dirs(left / subdir, right / subdir))

    return differences
