"""Tests for _resolver: caller detection and CLI resolution priority."""

from __future__ import annotations

from unittest.mock import patch

from _resolver import detect_caller_cli, resolve_cli


class TestResolveCli:
    def test_frontmatter_priority(self):
        assert resolve_cli("claude") == "claude"
        assert resolve_cli("codex") == "codex"

    def test_default_fallback(self):
        # No frontmatter and no caller env -> default
        with patch.dict("os.environ", {}, clear=True):
            with patch("os.path.exists", return_value=False):
                assert resolve_cli(None) == "codex"

    def test_invalid_frontmatter_uses_default(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("os.path.exists", return_value=False):
                assert resolve_cli("invalid-cli") == "codex"


class TestDetectCallerCli:
    def test_detects_claude_from_env(self):
        with patch.dict("os.environ", {"CLAUDE_CODE": "1"}, clear=True):
            assert detect_caller_cli() == "claude"

    def test_detects_codex_from_env(self):
        with patch.dict("os.environ", {"CODEX_CLI": "1"}, clear=True):
            assert detect_caller_cli() == "codex"

    def test_returns_none_when_no_indicator(self):
        """No env indicator and /proc absent — must return None deterministically."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("os.path.exists", return_value=False):
                assert detect_caller_cli() is None
