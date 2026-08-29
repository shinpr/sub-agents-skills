"""Behavior checks for the Command Code permission mod."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
POLICY_MOD = (
    Path(__file__).parent.parent / "skills" / "sub-agents" / "scripts" / "command_code_policy.mjs"
)

POLICY_PROBE = """
const { default: policy } = await import(process.argv[1]);
let beforeToolCall;
const permission = process.argv[2];
const cmd = {
  cwd: process.argv[3],
  addFlag() {},
  getFlag() { return permission; },
  hooks(value) { beforeToolCall = value.beforeToolCall; },
};
policy(cmd);
const result = beforeToolCall(JSON.parse(process.argv[4]));
process.stdout.write(JSON.stringify(result ?? null));
"""


def run_policy(permission: str, cwd: Path, tool_name: str, tool_input: dict):
    completed = subprocess.run(
        [
            NODE,
            "--input-type=module",
            "-e",
            POLICY_PROBE,
            POLICY_MOD.as_uri(),
            permission,
            str(cwd),
            json.dumps({"toolName": tool_name, "input": tool_input}),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


@pytest.mark.skipif(NODE is None, reason="Command Code requires Node.js")
class TestCommandCodePolicy:
    def test_read_only_blocks_file_edits_and_shell(self, tmp_path):
        target = tmp_path / "file.txt"
        assert run_policy("read-only", tmp_path, "write_file", {"file_path": str(target)})["block"]
        assert run_policy("read-only", tmp_path, "shell_command", {"command": "pwd"})["block"]

    def test_safe_edit_allows_workspace_write(self, tmp_path):
        target = tmp_path / "file.txt"
        assert run_policy("safe-edit", tmp_path, "write_file", {"file_path": str(target)}) is None

    def test_safe_edit_blocks_external_path_and_nested_agent(self, tmp_path):
        outside = tmp_path.parent / "outside.txt"
        assert run_policy("safe-edit", tmp_path, "read_file", {"file_path": str(outside)})["block"]
        assert run_policy("safe-edit", tmp_path, "agent", {"prompt": "nested"})["block"]

    def test_yolo_does_not_apply_runner_restrictions(self, tmp_path):
        outside = tmp_path.parent / "outside.txt"
        assert run_policy("yolo", tmp_path, "write_file", {"file_path": str(outside)}) is None

    def test_unknown_permission_fails_closed(self, tmp_path):
        result = run_policy("unknown", tmp_path, "read_file", {"file_path": str(tmp_path)})
        assert result["block"] is True
        assert "Unknown runner permission" in result["additionalContext"]
