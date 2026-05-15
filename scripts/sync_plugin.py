#!/usr/bin/env python3
"""Sync canonical skills into the Codex plugin directory."""

from __future__ import annotations

import argparse
import filecmp
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_SKILL = REPO_ROOT / "skills" / "sub-agents"
PLUGIN_SKILL = REPO_ROOT / "plugins" / "runner" / "skills" / "sub-agents"


def compare_dirs(left: Path, right: Path) -> list[str]:
    comparison = filecmp.dircmp(left, right)
    differences: list[str] = []

    for name in comparison.left_only:
        differences.append(f"missing from plugin copy: {left / name}")
    for name in comparison.right_only:
        differences.append(f"extra in plugin copy: {right / name}")
    for name in comparison.diff_files:
        differences.append(f"content differs: {left / name}")

    for subdir in comparison.common_dirs:
        differences.extend(compare_dirs(left / subdir, right / subdir))

    return differences


def sync() -> None:
    if not CANONICAL_SKILL.is_dir():
        raise SystemExit(f"canonical skill not found: {CANONICAL_SKILL}")

    if PLUGIN_SKILL.exists():
        shutil.rmtree(PLUGIN_SKILL)

    PLUGIN_SKILL.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(CANONICAL_SKILL, PLUGIN_SKILL)


def check() -> None:
    if not PLUGIN_SKILL.is_dir():
        raise SystemExit(f"plugin skill copy not found: {PLUGIN_SKILL}")

    differences = compare_dirs(CANONICAL_SKILL, PLUGIN_SKILL)
    if differences:
        for difference in differences:
            print(difference)
        raise SystemExit("plugin skill copy is out of sync; run scripts/sync_plugin.py")

    print("Plugin skill copy is in sync.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="check for drift without writing")
    args = parser.parse_args()

    if args.check:
        check()
    else:
        sync()


if __name__ == "__main__":
    main()
