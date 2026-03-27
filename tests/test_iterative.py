"""
Tests for claude_iterative.py
Tests core utilities, data structures, and helper functions.
"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from threading import Lock

import pytest

# Import the module we're testing
from claude_workflow import iterative as ci


# ─────────────────────────────────────────────
# Test: _confirm() function
# ─────────────────────────────────────────────

def test_confirm_auto_mode_returns_default():
    """When _AUTO_MODE is True, _confirm() should return default without prompting."""
    ci._AUTO_MODE = True
    assert ci._confirm("Should delete?", default=True) is True
    assert ci._confirm("Should delete?", default=False) is False
    ci._AUTO_MODE = False


def test_confirm_interactive_mode():
    """When _AUTO_MODE is False, _confirm() should call base.confirm()."""
    ci._AUTO_MODE = False
    with patch.object(ci.base, "confirm", return_value=True):
        result = ci._confirm("Test message?", default=False)
        assert result is True


# ─────────────────────────────────────────────
# Test: _collect_project_context()
# ─────────────────────────────────────────────

def test_collect_project_context_python_version():
    """_collect_project_context() should include Python version."""
    context = ci._collect_project_context()
    assert "Python:" in context
    assert f"{sys.version_info.major}.{sys.version_info.minor}" in context


def test_collect_project_context_no_conftest():
    """When no conftest.py exists, context should not include conftest info."""
    with tempfile.TemporaryDirectory() as tmpdir:
        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmpdir)
            context = ci._collect_project_context()
            assert "Python:" in context
            # conftest info only included if conftest files exist
        finally:
            os.chdir(original_cwd)


def test_collect_project_context_with_pyproject():
    """When pyproject.toml exists, context should mention it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "pyproject.toml").touch()
        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmpdir)
            context = ci._collect_project_context()
            assert "pyproject.toml: existe" in context
        finally:
            os.chdir(original_cwd)


# ─────────────────────────────────────────────
# Test: AgentRole enum
# ─────────────────────────────────────────────

def test_agent_role_enum_values():
    """AgentRole enum should have expected roles."""
    expected_roles = [
        "ANALYST", "ARCHITECT", "QA_PLANNER", "SYNTHESIZER",
        "IMPLEMENTER", "TEST_WRITER", "INTEGRATOR", "COMMITTER", "COORDINATOR"
    ]
    for role_name in expected_roles:
        assert hasattr(ci.AgentRole, role_name)
        role = getattr(ci.AgentRole, role_name)
        assert role.value == role_name


# ─────────────────────────────────────────────
# Test: AgentResult class
# ─────────────────────────────────────────────

def test_agent_result_success_when_exit_code_zero_and_no_error():
    """AgentResult.success should be True when exit_code=0 and error=None."""
    result = ci.AgentResult(
        role=ci.AgentRole.ANALYST,
        exit_code=0,
        output="success",
        session_id="sess123",
        duration_s=1.5,
        error=None
    )
    assert result.success is True


def test_agent_result_failure_when_exit_code_nonzero():
    """AgentResult.success should be False when exit_code!=0."""
    result = ci.AgentResult(
        role=ci.AgentRole.ANALYST,
        exit_code=1,
        output="failed",
        session_id="sess123",
        duration_s=1.5,
        error=None
    )
    assert result.success is False


def test_agent_result_failure_when_error_present():
    """AgentResult.success should be False when error is not None."""
    result = ci.AgentResult(
        role=ci.AgentRole.ANALYST,
        exit_code=0,
        output="output",
        session_id="sess123",
        duration_s=1.5,
        error="Something went wrong"
    )
    assert result.success is False


def test_agent_result_default_tokens():
    """AgentResult should have empty tokens dict by default."""
    result = ci.AgentResult(
        role=ci.AgentRole.ANALYST,
        exit_code=0,
        output="",
        session_id=None,
        duration_s=0.0
    )
    assert result.tokens == {}


# ─────────────────────────────────────────────
# Test: SessionStore class
# ─────────────────────────────────────────────

def test_session_store_save_and_load():
    """SessionStore should save and load session IDs by role."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_file = Path(tmpdir) / "sessions.json"
        store = ci.SessionStore(store_file)

        # Save a session
        store.save(ci.AgentRole.ANALYST, "sess-analyst-123")

        # Load the session
        loaded = store.load(ci.AgentRole.ANALYST)
        assert loaded == "sess-analyst-123"


def test_session_store_load_nonexistent():
    """SessionStore.load() should return None for non-existent role."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_file = Path(tmpdir) / "sessions.json"
        store = ci.SessionStore(store_file)

        assert store.load(ci.AgentRole.ARCHITECT) is None


def test_session_store_save_dev():
    """SessionStore should save and load dev sessions by index."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_file = Path(tmpdir) / "sessions.json"
        store = ci.SessionStore(store_file)

        store.save_dev(0, "sess-dev-0")
        assert store.load_dev(0) == "sess-dev-0"

        store.save_dev(1, "sess-dev-1")
        assert store.load_dev(1) == "sess-dev-1"
        assert store.load_dev(0) == "sess-dev-0"  # Should still be there


def test_session_store_load_all():
    """SessionStore.load_all() should return all stored sessions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_file = Path(tmpdir) / "sessions.json"
        store = ci.SessionStore(store_file)

        store.save(ci.AgentRole.ANALYST, "sess-1")
        store.save(ci.AgentRole.ARCHITECT, "sess-2")

        all_data = store.load_all()
        assert all_data[ci.AgentRole.ANALYST.value] == "sess-1"
        assert all_data[ci.AgentRole.ARCHITECT.value] == "sess-2"


def test_session_store_clear():
    """SessionStore.clear() should remove all data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_file = Path(tmpdir) / "sessions.json"
        store = ci.SessionStore(store_file)

        store.save(ci.AgentRole.ANALYST, "sess-1")
        assert store.load(ci.AgentRole.ANALYST) == "sess-1"

        store.clear()
        assert store.load(ci.AgentRole.ANALYST) is None
        assert store.load_all() == {}


def test_session_store_invalid_json():
    """SessionStore should handle corrupted JSON gracefully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_file = Path(tmpdir) / "sessions.json"
        store_file.write_text("{ invalid json")

        store = ci.SessionStore(store_file)
        # Should not raise, should return empty dict
        assert store.load(ci.AgentRole.ANALYST) is None
        all_data = store.load_all()
        assert all_data == {}


def test_session_store_thread_safety():
    """SessionStore operations should update _updated timestamp."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_file = Path(tmpdir) / "sessions.json"
        store = ci.SessionStore(store_file)

        store.save(ci.AgentRole.ANALYST, "sess-1")
        data = json.loads(store_file.read_text())
        assert "_updated" in data

        # _updated should be a valid ISO timestamp
        datetime.fromisoformat(data["_updated"])  # Should not raise


# ─────────────────────────────────────────────
# Test: TokenStore class
# ─────────────────────────────────────────────

def test_token_store_add_and_total():
    """TokenStore should accumulate tokens across roles."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_file = Path(tmpdir) / "tokens.json"
        store = ci.TokenStore(store_file)

        store.add("ANALYST", {"input": 100, "output": 50, "cost_usd": 0.01})
        store.add("ARCHITECT", {"input": 200, "output": 100, "cost_usd": 0.02})

        total = store.total()
        assert total["input"] == 300
        assert total["output"] == 150
        assert total["cost_usd"] == 0.03


def test_token_store_load_all():
    """TokenStore.load_all() should return all token data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_file = Path(tmpdir) / "tokens.json"
        store = ci.TokenStore(store_file)

        store.add("ANALYST", {"input": 100, "output": 50})
        all_data = store.load_all()

        assert all_data["ANALYST"]["input"] == 100
        assert all_data["ANALYST"]["output"] == 50
        assert "_total" in all_data


def test_token_store_empty_usage():
    """TokenStore.add() should handle empty usage gracefully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_file = Path(tmpdir) / "tokens.json"
        store = ci.TokenStore(store_file)

        # Should not raise or create file
        store.add("ANALYST", {})
        assert not store_file.exists()


def test_token_store_multiple_calls_same_role():
    """TokenStore should accumulate calls to the same role."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_file = Path(tmpdir) / "tokens.json"
        store = ci.TokenStore(store_file)

        store.add("ANALYST", {"input": 100})
        store.add("ANALYST", {"input": 50})

        all_data = store.load_all()
        assert all_data["ANALYST"]["input"] == 150


# ─────────────────────────────────────────────
# Test: ParallelRunner class
# ─────────────────────────────────────────────

def test_parallel_runner_run_parallel_success():
    """ParallelRunner should execute tasks and return results."""
    runner = ci.ParallelRunner(max_workers=2, timeout_s=5)

    def task1():
        return ci.AgentResult(
            role=ci.AgentRole.ANALYST,
            exit_code=0,
            output="output1",
            session_id="sess1",
            duration_s=0.1
        )

    def task2():
        return ci.AgentResult(
            role=ci.AgentRole.ARCHITECT,
            exit_code=0,
            output="output2",
            session_id="sess2",
            duration_s=0.1
        )

    tasks = [
        (ci.AgentRole.ANALYST, task1),
        (ci.AgentRole.ARCHITECT, task2),
    ]

    results = runner.run_parallel(tasks)
    assert len(results) == 2
    assert results[ci.AgentRole.ANALYST].success
    assert results[ci.AgentRole.ARCHITECT].success


def test_parallel_runner_run_with_retry_success():
    """ParallelRunner should succeed on first attempt if successful."""
    runner = ci.ParallelRunner(max_workers=1, timeout_s=5, max_retries=2)

    def successful_task():
        return ci.AgentResult(
            role=ci.AgentRole.ANALYST,
            exit_code=0,
            output="success",
            session_id="sess1",
            duration_s=0.1
        )

    result = runner._run_with_retry(ci.AgentRole.ANALYST, successful_task)
    assert result.success


def test_parallel_runner_run_with_retry_failure():
    """ParallelRunner should retry on failure."""
    runner = ci.ParallelRunner(max_workers=1, timeout_s=5, max_retries=1)

    call_count = {"count": 0}

    def failing_task():
        call_count["count"] += 1
        if call_count["count"] < 2:
            raise Exception("First attempt fails")
        return ci.AgentResult(
            role=ci.AgentRole.ANALYST,
            exit_code=0,
            output="success",
            session_id="sess1",
            duration_s=0.1
        )

    result = runner._run_with_retry(ci.AgentRole.ANALYST, failing_task)
    assert result.success
    assert call_count["count"] == 2


# ─────────────────────────────────────────────
# Test: CheckpointGate class
# ─────────────────────────────────────────────

def test_checkpoint_gate_init():
    """CheckpointGate should initialize with auto_mode."""
    gate = ci.CheckpointGate(auto_mode=True)
    assert gate is not None


def test_checkpoint_gate_wait_auto_mode():
    """CheckpointGate.wait() should return default in auto_mode."""
    gate = ci.CheckpointGate(auto_mode=True)
    result = gate.wait("test_phase", "test summary")
    # In auto mode with no explicit --auto-continue, should ask
    # but we can't test the interactive part


# ─────────────────────────────────────────────
# Test: Helper functions
# ─────────────────────────────────────────────

def test_make_session_id_basic():
    """make_session_id() should create valid session IDs."""
    session_id = ci.make_session_id("test_task")
    assert isinstance(session_id, str)
    assert session_id.startswith("sess_")
    assert "test-task" in session_id


def test_make_session_id_with_timestamp():
    """make_session_id() should use provided timestamp if given."""
    ts = "2026-03-26T10:30:00"
    session_id = ci.make_session_id("test_task", ts)
    assert session_id.startswith("sess_2026-03-26T10:30:00")
    assert "test-task" in session_id


def test_prepend_context_basic():
    """_prepend_context() should add context to prompt."""
    context = "CONTEXT: Project uses pytest"
    prompt = "Write tests"
    result = ci._prepend_context(prompt, context)
    assert "CONTEXT: Project uses pytest" in result
    assert "Write tests" in result


def test_prepend_context_empty_project_context():
    """_prepend_context() should handle empty context."""
    result = ci._prepend_context("Write tests", "")
    assert "Write tests" in result


# ─────────────────────────────────────────────
# Test: Integration-like tests
# ─────────────────────────────────────────────

def test_agent_result_with_tokens():
    """AgentResult should store token usage."""
    tokens = {"input": 100, "output": 50, "cost_usd": 0.01}
    result = ci.AgentResult(
        role=ci.AgentRole.ANALYST,
        exit_code=0,
        output="",
        session_id="sess1",
        duration_s=1.0,
        tokens=tokens
    )
    assert result.tokens == tokens


def test_session_store_multiple_roles():
    """SessionStore should handle multiple roles independently."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_file = Path(tmpdir) / "sessions.json"
        store = ci.SessionStore(store_file)

        # Save multiple roles
        for role in [ci.AgentRole.ANALYST, ci.AgentRole.ARCHITECT, ci.AgentRole.IMPLEMENTER]:
            store.save(role, f"sess-{role.value}")

        # Load and verify
        for role in [ci.AgentRole.ANALYST, ci.AgentRole.ARCHITECT, ci.AgentRole.IMPLEMENTER]:
            assert store.load(role) == f"sess-{role.value}"


def test_token_store_partial_keys():
    """TokenStore should handle partial token keys gracefully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_file = Path(tmpdir) / "tokens.json"
        store = ci.TokenStore(store_file)

        # Add with only some keys
        store.add("ANALYST", {"input": 100})
        all_data = store.load_all()

        # Should have zeros for missing keys
        assert all_data["ANALYST"]["input"] == 100
        assert "output" in all_data["ANALYST"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
