"""Tests for cli_backend module."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

from claude_workflow import cli_backend as cb


# ─────────────────────────────────────────────
# is_token_exhausted tests
# ─────────────────────────────────────────────

class TestIsTokenExhausted:
    """Tests for is_token_exhausted function."""

    def test_success_returns_false(self):
        """Exit code 0 should never be token exhausted."""
        assert cb.is_token_exhausted(0, "context length exceeded") is False

    def test_context_length_exceeded(self):
        """Should detect context length exceeded."""
        assert cb.is_token_exhausted(1, "Error: context length exceeded") is True

    def test_context_window(self):
        """Should detect context window errors."""
        assert cb.is_token_exhausted(1, "context window full") is True

    def test_token_limit(self):
        """Should detect token limit errors."""
        assert cb.is_token_exhausted(1, "token limit reached") is True

    def test_max_tokens(self):
        """Should detect max_tokens errors."""
        assert cb.is_token_exhausted(1, "max_tokens exceeded") is True

    def test_prompt_too_long(self):
        """Should detect prompt is too long errors."""
        assert cb.is_token_exhausted(1, "prompt is too long") is True

    def test_case_insensitive(self):
        """Should be case insensitive."""
        assert cb.is_token_exhausted(1, "CONTEXT LENGTH EXCEEDED") is True

    def test_unrelated_error(self):
        """Should return False for unrelated errors."""
        assert cb.is_token_exhausted(1, "Connection refused") is False


# ─────────────────────────────────────────────
# CLIResult tests
# ─────────────────────────────────────────────

class TestCLIResult:
    """Tests for CLIResult dataclass."""

    def test_success_property(self):
        """Success should be True when exit_code is 0."""
        result = cb.CLIResult(exit_code=0, text="ok")
        assert result.success is True

        result = cb.CLIResult(exit_code=1, text="error")
        assert result.success is False

    def test_token_exhausted_property(self):
        """token_exhausted should detect token errors."""
        result = cb.CLIResult(exit_code=1, text="context length exceeded")
        assert result.token_exhausted is True

        result = cb.CLIResult(exit_code=1, text="other error")
        assert result.token_exhausted is False

    def test_default_values(self):
        """Should have sensible defaults."""
        result = cb.CLIResult(exit_code=0, text="test")
        assert result.usage == {}
        assert result.session_id is None
        assert result.backend == "unknown"


# ─────────────────────────────────────────────
# ClaudeCLI tests
# ─────────────────────────────────────────────

class TestClaudeCLI:
    """Tests for ClaudeCLI backend."""

    def test_name(self):
        """Should have correct name."""
        cli = cb.ClaudeCLI()
        assert cli.name == "claude"

    @patch("claude_workflow.cli_backend.subprocess.run")
    def test_is_available_true(self, mock_run):
        """Should return True when claude CLI is installed."""
        mock_run.return_value = Mock(returncode=0)
        cli = cb.ClaudeCLI()
        assert cli.is_available() is True
        mock_run.assert_called_once()

    @patch("claude_workflow.cli_backend.subprocess.run")
    def test_is_available_false(self, mock_run):
        """Should return False when claude CLI is not installed."""
        mock_run.side_effect = FileNotFoundError()
        cli = cb.ClaudeCLI()
        assert cli.is_available() is False

    @patch("claude_workflow.cli_backend.subprocess.run")
    def test_execute_success(self, mock_run):
        """Should parse JSON response correctly."""
        response = {
            "result": "Hello world",
            "session_id": "sess-123",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
            },
            "total_cost_usd": 0.01,
            "duration_ms": 500,
        }
        mock_run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(response),
        )

        cli = cb.ClaudeCLI()
        result = cli.execute("test prompt")

        assert result.exit_code == 0
        assert result.text == "Hello world"
        assert result.session_id == "sess-123"
        assert result.usage["input"] == 100
        assert result.usage["output"] == 50
        assert result.backend == "claude"

    @patch("claude_workflow.cli_backend.subprocess.run")
    def test_execute_with_flags(self, mock_run):
        """Should pass flags to command."""
        mock_run.return_value = Mock(returncode=0, stdout="{}")

        cli = cb.ClaudeCLI()
        cli.execute("test", flags=["--max-turns", "5"])

        call_args = mock_run.call_args[0][0]
        assert "--max-turns" in call_args
        assert "5" in call_args


# ─────────────────────────────────────────────
# CursorCLI tests
# ─────────────────────────────────────────────

class TestCursorCLI:
    """Tests for CursorCLI backend."""

    def test_name(self):
        """Should have correct name."""
        cli = cb.CursorCLI()
        assert cli.name == "cursor"

    @patch.dict(os.environ, {"CURSOR_API_KEY": ""})
    def test_is_available_no_api_key(self):
        """Should return False without API key."""
        cli = cb.CursorCLI()
        assert cli.is_available() is False

    @patch("claude_workflow.cli_backend.subprocess.run")
    @patch.dict(os.environ, {"CURSOR_API_KEY": "test-key"})
    def test_is_available_with_api_key(self, mock_run):
        """Should check agent command when API key is set."""
        mock_run.return_value = Mock(returncode=0)
        cli = cb.CursorCLI()
        assert cli.is_available() is True

    def test_map_flags_dangerously_skip(self):
        """Should map --dangerously-skip-permissions to --force."""
        cli = cb.CursorCLI()
        result = cli._map_flags(["--dangerously-skip-permissions"])
        assert "--force" in result

    def test_map_flags_skip_unsupported(self):
        """Should skip unsupported flags."""
        cli = cb.CursorCLI()
        result = cli._map_flags([
            "--allowedTools", "Read,Write",
            "--max-turns", "10",
            "--resume", "sess-123",
        ])
        assert "--allowedTools" not in result
        assert "--max-turns" not in result
        assert "--resume" not in result

    def test_map_flags_keep_output_format(self):
        """Should keep --output-format flag."""
        cli = cb.CursorCLI()
        result = cli._map_flags(["--output-format", "json"])
        assert "--output-format" in result
        assert "json" in result

    @patch("claude_workflow.cli_backend.subprocess.run")
    def test_execute_adds_force(self, mock_run):
        """Should always add --force flag."""
        mock_run.return_value = Mock(returncode=0, stdout="{}")

        cli = cb.CursorCLI()
        cli.execute("test")

        call_args = mock_run.call_args[0][0]
        assert "--force" in call_args


# ─────────────────────────────────────────────
# FallbackBackend tests
# ─────────────────────────────────────────────

class TestFallbackBackend:
    """Tests for FallbackBackend."""

    def test_name_shows_current(self):
        """Name should show current backend."""
        primary = Mock()
        primary.name = "claude"
        fallback = Mock()
        fallback.name = "cursor"

        backend = cb.FallbackBackend(primary, fallback)
        assert "claude" in backend.name

    def test_is_available_primary(self):
        """Should return True if primary is available."""
        primary = Mock()
        primary.is_available.return_value = True
        fallback = Mock()
        fallback.is_available.return_value = False

        backend = cb.FallbackBackend(primary, fallback)
        assert backend.is_available() is True

    def test_is_available_fallback(self):
        """Should return True if only fallback is available."""
        primary = Mock()
        primary.is_available.return_value = False
        fallback = Mock()
        fallback.is_available.return_value = True

        backend = cb.FallbackBackend(primary, fallback)
        assert backend.is_available() is True

    def test_execute_uses_primary_on_success(self):
        """Should use primary backend on success."""
        primary = Mock()
        primary.execute.return_value = cb.CLIResult(
            exit_code=0, text="ok", backend="claude"
        )
        fallback = Mock()

        backend = cb.FallbackBackend(primary, fallback)
        result = backend.execute("test")

        assert result.text == "ok"
        primary.execute.assert_called_once()
        fallback.execute.assert_not_called()

    def test_execute_switches_on_token_exhaustion(self, tmp_path, monkeypatch):
        """Should switch to fallback on token exhaustion."""
        monkeypatch.chdir(tmp_path)

        primary = Mock()
        primary.name = "claude"
        primary.execute.return_value = cb.CLIResult(
            exit_code=1, text="context length exceeded", backend="claude"
        )

        fallback = Mock()
        fallback.name = "cursor"
        fallback.is_available.return_value = True
        fallback.execute.return_value = cb.CLIResult(
            exit_code=0, text="ok from cursor", backend="cursor"
        )

        backend = cb.FallbackBackend(primary, fallback, auto_switch=True)
        result = backend.execute("test", step="test_step")

        assert result.text == "ok from cursor"
        assert backend.has_switched is True
        fallback.execute.assert_called_once()

    def test_no_switch_when_disabled(self):
        """Should not switch when auto_switch is False."""
        primary = Mock()
        primary.execute.return_value = cb.CLIResult(
            exit_code=1, text="context length exceeded", backend="claude"
        )
        fallback = Mock()

        backend = cb.FallbackBackend(primary, fallback, auto_switch=False)
        result = backend.execute("test")

        assert result.text == "context length exceeded"
        assert backend.has_switched is False
        fallback.execute.assert_not_called()


# ─────────────────────────────────────────────
# Factory function tests
# ─────────────────────────────────────────────

class TestFactoryFunctions:
    """Tests for factory functions."""

    def setup_method(self):
        """Reset backend before each test."""
        cb.reset_default_backend()

    def teardown_method(self):
        """Reset backend after each test."""
        cb.reset_default_backend()

    def test_get_default_backend_creates_fallback(self):
        """Should create FallbackBackend by default."""
        backend = cb.get_default_backend()
        assert isinstance(backend, cb.FallbackBackend)

    def test_get_default_backend_singleton(self):
        """Should return same instance on multiple calls."""
        backend1 = cb.get_default_backend()
        backend2 = cb.get_default_backend()
        assert backend1 is backend2

    def test_get_default_backend_prefer_cursor(self):
        """Should use Cursor as primary when prefer_cursor=True."""
        backend = cb.get_default_backend(prefer_cursor=True)
        assert isinstance(backend, cb.FallbackBackend)
        assert isinstance(backend._primary, cb.CursorCLI)

    def test_get_default_backend_no_fallback(self):
        """Should return primary only when enable_fallback=False."""
        cb.reset_default_backend()
        backend = cb.get_default_backend(enable_fallback=False)
        assert isinstance(backend, cb.ClaudeCLI)

    def test_set_default_backend(self):
        """Should allow setting custom backend."""
        custom = Mock()
        cb.set_default_backend(custom)
        assert cb.get_default_backend() is custom

    def test_reset_default_backend(self):
        """Should reset to None."""
        cb.get_default_backend()  # Create one
        cb.reset_default_backend()
        # Next call should create new instance
        backend = cb.get_default_backend()
        assert backend is not None
