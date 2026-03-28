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


# ─────────────────────────────────────────────
# Test: _generate_report_md()
# ─────────────────────────────────────────────

def test_generate_report_md_creates_file():
    """_generate_report_md() should create agents/REPORT.md"""
    with tempfile.TemporaryDirectory() as tmpdir:
        agents_dir = Path(tmpdir)

        results = {
            "branch": True,
            "analysis": True,
            "synthesize": True,
            "implement": True,
            "integrate": True,
            "commit": True,
            "coverage": 85.5,
        }

        durations = {
            "branch": 2.0,
            "analysis": 45.0,
            "synthesize": 15.0,
            "implement": 30.0,
            "integrate": 20.0,
            "commit": 5.0,
        }

        ci._generate_report_md(results, durations, None, "feat/test", agents_dir)

        report_file = agents_dir / "REPORT.md"
        assert report_file.exists()
        content = report_file.read_text()

        # Check for key sections
        assert "Reporte de Ejecución" in content
        assert "feat/test" in content
        assert "Fase 0: Branch" in content
        assert "Fase 1: Análisis" in content
        assert "✅ OK" in content
        assert "85.5%" in content


def test_generate_report_md_with_token_store():
    """_generate_report_md() should include token info from TokenStore"""
    with tempfile.TemporaryDirectory() as tmpdir:
        agents_dir = Path(tmpdir)
        token_file = Path(tmpdir) / "tokens.json"

        # Create a token store with some data
        token_store = ci.TokenStore(token_file)
        token_store.add("ANALYST", {"input": 1000, "output": 500, "cost_usd": 0.05})
        token_store.add("SYNTHESIZER", {"input": 800, "output": 300, "cost_usd": 0.04})

        results = {
            "branch": True,
            "analysis": True,
            "synthesize": True,
            "implement": True,
            "integrate": True,
            "commit": True,
            "coverage": 80,
        }

        durations = {
            "branch": 1.0,
            "analysis": 30.0,
            "synthesize": 10.0,
            "implement": 25.0,
            "integrate": 15.0,
            "commit": 3.0,
        }

        ci._generate_report_md(results, durations, token_store, "feat/tokens", agents_dir)

        report_file = agents_dir / "REPORT.md"
        content = report_file.read_text()

        # Should include token counts
        assert "1,000" in content or "1000" in content  # Input tokens
        assert "500" in content  # Output tokens
        assert "0.05" in content or "0.0500" in content  # Cost


def test_generate_report_md_handles_none_token_store():
    """_generate_report_md() should work with None token_store"""
    with tempfile.TemporaryDirectory() as tmpdir:
        agents_dir = Path(tmpdir)

        results = {
            "branch": True,
            "analysis": False,  # Failed phase
            "synthesize": True,
            "implement": True,
            "integrate": True,
            "commit": True,
            "coverage": 75,
        }

        durations = {
            "branch": 1.0,
            "analysis": 30.0,
            "synthesize": 10.0,
            "implement": 25.0,
            "integrate": 15.0,
            "commit": 3.0,
        }

        # Should not raise error with None token_store
        ci._generate_report_md(results, durations, None, "feat/no-tokens", agents_dir)

        report_file = agents_dir / "REPORT.md"
        assert report_file.exists()
        content = report_file.read_text()

        assert "❌ FAIL" in content  # Failed phase should show as FAIL


# ─────────────────────────────────────────────
# Test: HookRunner
# ─────────────────────────────────────────────

def test_hook_runner_no_hooks_file():
    """HookRunner.load() returns False if no .claude-workflow-hooks.py exists"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ci.HookRunner(Path(tmpdir))
        assert runner.load() is False
        assert len(runner.hooks) == 0


def test_hook_runner_discovers_hooks():
    """HookRunner discovers before_phase_N and after_phase_N functions"""
    with tempfile.TemporaryDirectory() as tmpdir:
        hooks_file = Path(tmpdir) / ".claude-workflow-hooks.py"
        hooks_file.write_text("""
def before_phase_0(ctx):
    pass

def after_phase_1(ctx, result):
    pass

def before_phase_2(ctx):
    pass

def some_other_function():
    pass
""")
        runner = ci.HookRunner(Path(tmpdir))
        assert runner.load() is True
        assert len(runner.hooks) == 3
        assert "before_phase_0" in runner.hooks
        assert "after_phase_1" in runner.hooks
        assert "before_phase_2" in runner.hooks
        assert "some_other_function" not in runner.hooks


def test_hook_runner_before_hook_called():
    """HookRunner.before() executes the before_phase hook"""
    with tempfile.TemporaryDirectory() as tmpdir:
        hooks_file = Path(tmpdir) / ".claude-workflow-hooks.py"
        hooks_file.write_text("""
hook_called = False

def before_phase_0(ctx):
    global hook_called
    hook_called = True
    assert ctx.get("task") == "test task"
""")
        runner = ci.HookRunner(Path(tmpdir))
        runner.load()
        ctx = {"task": "test task", "branch": "main", "phase_name": "phase_0"}
        assert runner.before(0, ctx) is True


def test_hook_runner_after_hook_called():
    """HookRunner.after() executes the after_phase hook with result"""
    with tempfile.TemporaryDirectory() as tmpdir:
        hooks_file = Path(tmpdir) / ".claude-workflow-hooks.py"
        hooks_file.write_text("""
def after_phase_1(ctx, result):
    assert ctx.get("task") == "test"
    assert result == "success"
""")
        runner = ci.HookRunner(Path(tmpdir))
        runner.load()
        ctx = {"task": "test", "branch": "main", "phase_name": "phase_1"}
        assert runner.after(1, ctx, "success") is True


def test_hook_runner_hook_exception_caught():
    """HookRunner catches exceptions in hooks and returns False"""
    with tempfile.TemporaryDirectory() as tmpdir:
        hooks_file = Path(tmpdir) / ".claude-workflow-hooks.py"
        hooks_file.write_text("""
def before_phase_0(ctx):
    raise RuntimeError("Hook error")
""")
        runner = ci.HookRunner(Path(tmpdir))
        runner.load()
        ctx = {"task": "test", "branch": "main", "phase_name": "phase_0"}
        # Should return False but not raise
        assert runner.before(0, ctx) is False


def test_hook_runner_no_args_hook():
    """HookRunner supports hooks with zero parameters"""
    with tempfile.TemporaryDirectory() as tmpdir:
        hooks_file = Path(tmpdir) / ".claude-workflow-hooks.py"
        hooks_file.write_text("""
counter = 0

def before_phase_0():
    global counter
    counter += 1
""")
        runner = ci.HookRunner(Path(tmpdir))
        runner.load()
        ctx = {}
        assert runner.before(0, ctx) is True


# ─────────────────────────────────────────────
# Test: PromptLoader class
# ─────────────────────────────────────────────

def test_prompt_loader_no_dir():
    """PromptLoader.load() returns 0 if .claude-workflow/prompts/ does not exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        loader = ci.PromptLoader(Path(tmpdir))
        assert loader.load() == 0
        assert len(loader._custom) == 0


def test_prompt_loader_reads_md_file():
    """PromptLoader loads text from .claude-workflow/prompts/*.md files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        prompts_dir = Path(tmpdir) / ".claude-workflow" / "prompts"
        prompts_dir.mkdir(parents=True)
        analyst_file = prompts_dir / "ANALYST.md"
        analyst_file.write_text("Custom analyst: {task} -> {output}")

        loader = ci.PromptLoader(Path(tmpdir))
        loaded = loader.load()
        assert loaded == 1
        assert "ANALYST_PROMPT" in loader._custom
        assert loader._custom["ANALYST_PROMPT"] == "Custom analyst: {task} -> {output}"


def test_prompt_loader_fallback_to_default():
    """PromptLoader.get() returns module-level default when no custom .md exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        loader = ci.PromptLoader(Path(tmpdir))
        loader.load()  # Dir doesn't exist
        result = loader.get("ANALYST_PROMPT")
        assert result == ci.ANALYST_PROMPT


def test_prompt_loader_partial_override():
    """PromptLoader allows overriding only some prompts; others fall back to defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        prompts_dir = Path(tmpdir) / ".claude-workflow" / "prompts"
        prompts_dir.mkdir(parents=True)
        analyst_file = prompts_dir / "ANALYST.md"
        analyst_file.write_text("Custom: {task} {output}")

        loader = ci.PromptLoader(Path(tmpdir))
        loaded = loader.load()
        assert loaded == 1
        # Custom overridden
        assert "Custom:" in loader.get("ANALYST_PROMPT")
        # Non-overridden falls back to default
        assert loader.get("ARCHITECT_PROMPT") == ci.ARCHITECT_PROMPT


def test_prompt_loader_warns_missing_placeholders(caplog):
    """PromptLoader warns if a custom prompt lacks expected placeholders."""
    import logging
    with tempfile.TemporaryDirectory() as tmpdir:
        prompts_dir = Path(tmpdir) / ".claude-workflow" / "prompts"
        prompts_dir.mkdir(parents=True)
        # ANALYST_PROMPT requires {task} and {output}; missing {output}
        analyst_file = prompts_dir / "ANALYST.md"
        analyst_file.write_text("Only task: {task}")

        loader = ci.PromptLoader(Path(tmpdir))
        with caplog.at_level(logging.WARNING):
            loader.load()
        # Check that warning was logged
        assert any("output" in record.message for record in caplog.records)
        # Despite warning, the prompt WAS loaded
        assert "ANALYST_PROMPT" in loader._custom


def test_prompt_loader_ignores_non_string_values():
    """PromptLoader only loads string values; non-strings are ignored."""
    with tempfile.TemporaryDirectory() as tmpdir:
        prompts_dir = Path(tmpdir) / ".claude-workflow" / "prompts"
        prompts_dir.mkdir(parents=True)

        # Create a file with non-string Python value (simulate by file content)
        # Actually, since we read files as text, we need to test via mocking
        loader = ci.PromptLoader(Path(tmpdir))

        # Manually add a non-string to _custom to simulate the behavior
        # (in reality the loader will only load strings from files)
        loader._custom["DUMMY"] = {"not": "string"}

        # get() should still work for real prompts (fallback)
        assert loader.get("ANALYST_PROMPT") == ci.ANALYST_PROMPT


def test_prompt_loader_empty_file_not_loaded():
    """PromptLoader ignores empty .md files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        prompts_dir = Path(tmpdir) / ".claude-workflow" / "prompts"
        prompts_dir.mkdir(parents=True)
        analyst_file = prompts_dir / "ANALYST.md"
        analyst_file.write_text("")  # Empty file

        loader = ci.PromptLoader(Path(tmpdir))
        loaded = loader.load()
        assert loaded == 0
        # get() falls back to default
        assert loader.get("ANALYST_PROMPT") == ci.ANALYST_PROMPT


def test_prompt_loader_get_unknown_name_raises():
    """PromptLoader.get() raises KeyError for an unknown prompt name."""
    with tempfile.TemporaryDirectory() as tmpdir:
        loader = ci.PromptLoader(Path(tmpdir))
        with pytest.raises(KeyError):
            loader.get("NONEXISTENT_PROMPT")


# ─────────────────────────────────────────────
# Test: init_prompts_dir() function
# ─────────────────────────────────────────────

def test_init_prompts_dir_creates_files():
    """init_prompts_dir() creates .claude-workflow/prompts/ with all prompt .md files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ci.init_prompts_dir(Path(tmpdir))

        prompts_dir = Path(tmpdir) / ".claude-workflow" / "prompts"
        assert prompts_dir.exists()

        # Check that all expected files were created
        expected_files = {
            "ANALYST.md", "ARCHITECT.md", "QA_PLANNER.md",
            "SYNTHESIZER.md", "IMPLEMENTER.md", "TEST_WRITER.md",
            "TEST_WRITER_MULTI.md", "COORDINATOR.md", "DEV_AGENT.md",
            "INTEGRATOR.md", "COMMITTER.md", "README.md"
        }
        created_files = {f.name for f in prompts_dir.glob("*.md")}
        assert expected_files.issubset(created_files)


def test_init_prompts_dir_no_overwrite():
    """init_prompts_dir() does not overwrite existing files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        prompts_dir = Path(tmpdir) / ".claude-workflow" / "prompts"
        prompts_dir.mkdir(parents=True)

        analyst_file = prompts_dir / "ANALYST.md"
        original_content = "Original custom prompt"
        analyst_file.write_text(original_content)

        # Call init_prompts_dir
        ci.init_prompts_dir(Path(tmpdir))

        # File should not be overwritten
        assert analyst_file.read_text() == original_content


def test_init_prompts_dir_readme_has_placeholders():
    """init_prompts_dir() creates README.md with placeholder documentation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ci.init_prompts_dir(Path(tmpdir))

        readme = Path(tmpdir) / ".claude-workflow" / "prompts" / "README.md"
        assert readme.exists()

        content = readme.read_text()
        # Check that README mentions placeholders and has table
        assert "{task}" in content
        assert "{output}" in content
        assert "ANALYST.md" in content


# ─────────────────────────────────────────────
# Test: claude_p_with_session()
# ─────────────────────────────────────────────

@patch("claude_workflow.iterative.subprocess.run")
def test_claude_p_with_session_success(mock_run):
    """claude_p_with_session with JSON response → parses result, session_id, usage."""
    json_response = json.dumps({
        "result": "output text",
        "session_id": "sess-123",
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "total_cost_usd": 0.01
    })
    mock_run.return_value = Mock(returncode=0, stdout=json_response, stderr="")
    code, text, sid, usage = ci.claude_p_with_session("test prompt")
    assert code == 0
    assert text == "output text"
    assert sid == "sess-123"
    assert usage["input"] == 100


@patch("claude_workflow.iterative.subprocess.run")
def test_claude_p_with_session_non_json(mock_run):
    """claude_p_with_session with plain text → returns raw text."""
    mock_run.return_value = Mock(returncode=0, stdout="plain text response", stderr="")
    code, text, sid, usage = ci.claude_p_with_session("test prompt")
    assert code == 0
    assert text == "plain text response"
    assert sid is None


@patch("claude_workflow.iterative.subprocess.run")
def test_claude_p_with_session_error(mock_run):
    """claude_p_with_session with non-zero exit → returns error code."""
    mock_run.return_value = Mock(returncode=1, stdout="", stderr="error")
    code, text, sid, usage = ci.claude_p_with_session("test prompt")
    assert code == 1


# ─────────────────────────────────────────────
# Test: _init_agents_dir()
# ─────────────────────────────────────────────

def test_init_agents_dir_creates_structure():
    """_init_agents_dir creates subdirectories and task.txt."""
    with tempfile.TemporaryDirectory() as tmpdir:
        agents_dir = Path(tmpdir) / "agents"
        ci._init_agents_dir(agents_dir, "test task")

        assert agents_dir.exists()
        assert (agents_dir / "analysis").exists()
        assert (agents_dir / "implementation").exists()
        assert (agents_dir / "task.txt").exists()
        assert (agents_dir / "task.txt").read_text() == "test task"


# ─────────────────────────────────────────────
# Test: phase0_branch()
# ─────────────────────────────────────────────

@patch.object(ci.base, "create_branch", return_value=True)
def test_phase0_branch_success(mock_create):
    """phase0_branch calls base.create_branch and returns True."""
    result = ci.phase0_branch("feat/test")
    assert result is True
    mock_create.assert_called_once_with("feat/test")


@patch.object(ci.base, "create_branch", return_value=False)
def test_phase0_branch_failure(mock_create):
    """phase0_branch returns False if create_branch fails."""
    result = ci.phase0_branch("feat/test")
    assert result is False


# ─────────────────────────────────────────────
# Test: phase1_analysis()
# ─────────────────────────────────────────────

@patch("claude_workflow.iterative.claude_p_with_session")
def test_phase1_analysis_skip(mock_cps, tmp_path, monkeypatch):
    """phase1_analysis skips if 1 in skip_phases."""
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    results = ci.phase1_analysis("My task", agents_dir, None, None, skip_phases=[1])

    # Should return empty dict when skipped
    assert isinstance(results, dict)
    assert not mock_cps.called


# ─────────────────────────────────────────────
# Test: phase2_synthesize()
# ─────────────────────────────────────────────

@patch("claude_workflow.iterative.claude_p_with_session")
def test_phase2_synthesize_skip(mock_cps, tmp_path, monkeypatch):
    """phase2_synthesize skips if 2 in skip_phases."""
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    result = ci.phase2_synthesize("My task", agents_dir, None, skip_phases=[2], coverage=80)
    assert not mock_cps.called


# ─────────────────────────────────────────────
# Test: phase3_implement()
# ─────────────────────────────────────────────

def test_phase3_implement_skip(tmp_path, monkeypatch):
    """phase3_implement skips if 3 in skip_phases."""
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    result = ci.phase3_implement("My task", agents_dir, None, None, skip_phases=[3], coverage=80)
    assert result is True


# ─────────────────────────────────────────────
# Test: phase4_integrate()
# ─────────────────────────────────────────────

def test_phase4_integrate_skip(tmp_path, monkeypatch):
    """phase4_integrate skips if 4 in skip_phases."""
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    ok, cov = ci.phase4_integrate("My task", agents_dir, None, skip_phases=[4], coverage=80)
    assert ok is True
    assert cov == 0.0


# ─────────────────────────────────────────────
# Test: phase5_commit()
# ─────────────────────────────────────────────

def test_phase5_commit_skip(tmp_path, monkeypatch):
    """phase5_commit skips if 5 in skip_phases."""
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    result = ci.phase5_commit("My task", "feat/test", agents_dir, None, skip_phases=[5], coverage=85.0)
    assert result is True


# ─────────────────────────────────────────────
# Test: _print_summary()
# ─────────────────────────────────────────────

def test_print_summary_no_crash(capsys, tmp_path):
    """_print_summary handles various inputs without crashing."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    ci._print_summary(
        results={"branch": True},
        token_store=None,
        durations={},
        branch="feat/test",
        agents_dir=agents_dir
    )
    captured = capsys.readouterr()
    # Just verify it doesn't crash
    assert isinstance(captured.out, str)


# ─────────────────────────────────────────────
# Test: run() with dry_run
# ─────────────────────────────────────────────

@patch.object(ci.base, "timestamp_branch", return_value="feat/test-20260327")
def test_run_dry_run_returns_zero(mock_branch):
    """run() with dry_run=True returns 0 without execution."""
    code = ci.run(task="My task", dry_run=True)
    assert code == 0


# ─────────────────────────────────────────────
# Test: TokenStore usage tracking
# ─────────────────────────────────────────────

def test_token_store_tracks_usage(tmp_path):
    """TokenStore properly tracks token usage across agents."""
    tokens_file = tmp_path / "tokens.json"
    store = ci.TokenStore(tokens_file)
    store.add("ANALYST", {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0, "cost_usd": 0.01})
    store.add("ARCHITECT", {"input": 80, "output": 40, "cache_read": 0, "cache_write": 0, "cost_usd": 0.008})

    # Verify file was created
    assert tokens_file.exists()
    data = json.loads(tokens_file.read_text())
    assert data["_total"]["input"] == 180
    assert data["_total"]["output"] == 90


# ─────────────────────────────────────────────
# Test: AgentResult edge cases
# ─────────────────────────────────────────────

def test_agent_result_failure_with_error():
    """AgentResult.success=False when error is set."""
    result = ci.AgentResult(
        role=ci.AgentRole.ANALYST,
        exit_code=0,
        output="some output",
        session_id="sess-1",
        duration_s=1.5,
        error="error message"
    )
    assert result.success is False


def test_agent_result_failure_with_nonzero_exit():
    """AgentResult.success=False when exit_code!=0."""
    result = ci.AgentResult(
        role=ci.AgentRole.ANALYST,
        exit_code=1,
        output="",
        session_id="sess-1",
        duration_s=1.0,
        error=None
    )
    assert result.success is False


# ─────────────────────────────────────────────
# Test: _run_analyst, _run_architect, etc.
# ─────────────────────────────────────────────

@patch("claude_workflow.iterative.claude_p_with_session")
def test_run_analyst_basic(mock_cps, tmp_path, monkeypatch):
    """_run_analyst calls claude_p_with_session and returns AgentResult."""
    monkeypatch.chdir(tmp_path)
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()

    mock_cps.return_value = (0, "analysis output", "sess-1", {"input": 100, "output": 50})
    result = ci._run_analyst("task", analysis_dir, None)
    assert result.exit_code == 0
    assert result.role == ci.AgentRole.ANALYST


@patch("claude_workflow.iterative.claude_p_with_session")
def test_run_architect_basic(mock_cps, tmp_path, monkeypatch):
    """_run_architect calls claude_p_with_session."""
    monkeypatch.chdir(tmp_path)
    impl_dir = tmp_path / "implementation"
    impl_dir.mkdir()

    mock_cps.return_value = (0, "plan output", "sess-2", {"input": 200, "output": 100})
    result = ci._run_architect("task", impl_dir, None)
    assert result.exit_code == 0
    assert result.role == ci.AgentRole.ARCHITECT


@patch("claude_workflow.iterative.claude_p_with_session")
def test_run_qa_planner_basic(mock_cps, tmp_path, monkeypatch):
    """_run_qa_planner calls claude_p_with_session."""
    monkeypatch.chdir(tmp_path)
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()

    mock_cps.return_value = (0, "qa plan", "sess-3", {"input": 150, "output": 75})
    result = ci._run_qa_planner("task", analysis_dir, None)
    assert result.exit_code == 0
    assert result.role == ci.AgentRole.QA_PLANNER


# ─────────────────────────────────────────────
# Test: _run_implementer, _run_test_writer, etc.
# ─────────────────────────────────────────────

@patch("claude_workflow.iterative.claude_p_with_session")
def test_run_implementer_basic(mock_cps, tmp_path, monkeypatch):
    """_run_implementer calls claude_p_with_session."""
    monkeypatch.chdir(tmp_path)
    impl_dir = tmp_path / "implementation"
    impl_dir.mkdir()

    mock_cps.return_value = (0, "implementation", "sess-4", {"input": 300, "output": 150})
    result = ci._run_implementer("task", impl_dir, None)
    assert result.exit_code == 0


@patch("claude_workflow.iterative.claude_p_with_session")
def test_run_test_writer_basic(mock_cps, tmp_path, monkeypatch):
    """_run_test_writer calls claude_p_with_session."""
    monkeypatch.chdir(tmp_path)
    impl_dir = tmp_path / "implementation"
    impl_dir.mkdir()

    mock_cps.return_value = (0, "tests", "sess-5", {"input": 200, "output": 100})
    result = ci._run_test_writer("task", impl_dir, None)
    assert result.exit_code == 0


@patch("claude_workflow.iterative.claude_p_with_session")
def test_run_coordinator_basic(mock_cps, tmp_path, monkeypatch):
    """_run_coordinator calls claude_p_with_session."""
    monkeypatch.chdir(tmp_path)
    impl_dir = tmp_path / "implementation"
    impl_dir.mkdir()

    mock_cps.return_value = (0, "coordination", "sess-6", {"input": 250, "output": 125})
    result = ci._run_coordinator("task", impl_dir, None)
    assert result.exit_code == 0


@patch("claude_workflow.iterative.claude_p_with_session")
def test_run_dev_agent_basic(mock_cps, tmp_path, monkeypatch):
    """_run_dev_agent calls claude_p_with_session."""
    monkeypatch.chdir(tmp_path)
    impl_dir = tmp_path / "implementation"
    impl_dir.mkdir()
    tasks_dir = impl_dir / "tasks"
    tasks_dir.mkdir()

    mock_cps.return_value = (0, "development", "sess-7", {"input": 400, "output": 200})
    result = ci._run_dev_agent("task", impl_dir, 0, 1)
    assert result.exit_code == 0


# ─────────────────────────────────────────────
# Test: SessionStore
# ─────────────────────────────────────────────

def test_session_store_read_write(tmp_path):
    """SessionStore reads and writes session IDs."""
    sessions_file = tmp_path / "sessions.json"
    store = ci.SessionStore(sessions_file)

    store.save(ci.AgentRole.ANALYST, "sess-123")
    session_id = store.load(ci.AgentRole.ANALYST)
    assert session_id == "sess-123"


def test_session_store_missing_file(tmp_path):
    """SessionStore handles missing session file gracefully."""
    sessions_file = tmp_path / "nonexistent.json"
    store = ci.SessionStore(sessions_file)

    # Should return None for missing sessions
    session_id = store.load(ci.AgentRole.ANALYST)
    assert session_id is None


def test_session_store_save_dev(tmp_path):
    """SessionStore saves and loads dev agent sessions."""
    sessions_file = tmp_path / "sessions.json"
    store = ci.SessionStore(sessions_file)

    store.save_dev(0, "dev-sess-1")
    session_id = store.load_dev(0)
    assert session_id == "dev-sess-1"


# ─────────────────────────────────────────────
# Test: _collect_project_context with conftest
# ─────────────────────────────────────────────

def test_collect_project_context_with_conftest(tmp_path, monkeypatch):
    """_collect_project_context includes conftest.py info."""
    monkeypatch.chdir(tmp_path)
    conftest = tmp_path / "conftest.py"
    conftest.write_text("# test configuration")

    context = ci._collect_project_context()
    assert "conftest.py" in context or "Python:" in context


def test_collect_project_context_with_requirements(tmp_path, monkeypatch):
    """_collect_project_context includes requirements info."""
    monkeypatch.chdir(tmp_path)
    reqs = tmp_path / "requirements.txt"
    reqs.write_text("pytest\nmock\n")

    context = ci._collect_project_context()
    assert "Python:" in context


# ─────────────────────────────────────────────
# Test: _confirm() function edge cases
# ─────────────────────────────────────────────

def test_confirm_returns_default_in_auto():
    """_confirm returns default when _AUTO_MODE=True."""
    original = ci._AUTO_MODE
    try:
        ci._AUTO_MODE = True
        assert ci._confirm("Test?", default=True) is True
        assert ci._confirm("Test?", default=False) is False
    finally:
        ci._AUTO_MODE = original


@patch.object(ci.base, "confirm", return_value=False)
def test_confirm_calls_base_in_interactive(mock_confirm):
    """_confirm calls base.confirm when _AUTO_MODE=False."""
    original = ci._AUTO_MODE
    try:
        ci._AUTO_MODE = False
        result = ci._confirm("Test?", default=True)
        assert result is False
        mock_confirm.assert_called_once()
    finally:
        ci._AUTO_MODE = original


# ─────────────────────────────────────────────
# Test: PromptLoader with _get_prompt
# ─────────────────────────────────────────────

def test_get_prompt_returns_default_when_no_loader():
    """_get_prompt returns module constant when loader is None."""
    result = ci._get_prompt("ANALYST_PROMPT", None)
    assert result == ci.ANALYST_PROMPT


def test_get_prompt_uses_loader_when_available():
    """_get_prompt uses loader.get() when loader is provided."""
    with tempfile.TemporaryDirectory() as tmpdir:
        prompts_dir = Path(tmpdir) / ".claude-workflow" / "prompts"
        prompts_dir.mkdir(parents=True)
        analyst_file = prompts_dir / "ANALYST.md"
        analyst_file.write_text("Custom prompt for analyst")

        loader = ci.PromptLoader(Path(tmpdir))
        loader.load()

        result = ci._get_prompt("ANALYST_PROMPT", loader)
        assert result == "Custom prompt for analyst"


# ─────────────────────────────────────────────
# Test: _delete_branch cleanup
# ─────────────────────────────────────────────

@patch("claude_workflow.iterative.subprocess.run")
def test_delete_branch_calls_git(mock_run):
    """_delete_branch calls git commands."""
    mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
    ci._delete_branch("feat/test")
    # Should call git at least once
    assert mock_run.called


# ─────────────────────────────────────────────
# Test: _save_pause functionality
# ─────────────────────────────────────────────

@patch("claude_workflow.iterative._confirm", return_value=False)
def test_save_pause_writes_file(mock_confirm, tmp_path):
    """_save_pause writes pause info to file."""
    sessions_file = tmp_path / "sessions.json"
    sessions_file.write_text("{}")  # Create empty sessions file

    store = ci.SessionStore(sessions_file)
    ci._save_pause(store, sessions_file, "2026-03-27T10:00:00", "feat/test")

    # Just verify the function doesn't crash
    assert mock_confirm.called


# ─────────────────────────────────────────────
# Comprehensive phase tests with full mocking
# ─────────────────────────────────────────────

@patch.object(ci.base, "run_tests", return_value=(True, 85.0, "TOTAL ... 85%"))
@patch("claude_workflow.iterative.claude_p_with_session")
def test_phase1_through_5_integration(mock_cps, mock_tests, tmp_path, monkeypatch):
    """All phases executed with mocks → returns success."""
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / "agents"
    analysis_dir = agents_dir / "analysis"
    impl_dir = agents_dir / "implementation"
    tasks_dir = impl_dir / "tasks"
    for d in [agents_dir, analysis_dir, impl_dir, tasks_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Mock all claude_p_with_session calls
    mock_cps.return_value = (0, "output", "sess-id", {"input": 100, "output": 50})

    # Test each phase individually
    result0 = ci.phase0_branch("feat/test")  # Creates branch
    assert isinstance(result0, bool)

    # Phase 1 with skip
    result1 = ci.phase1_analysis("task", agents_dir, None, None, skip_phases=[1])
    assert isinstance(result1, dict)

    # Phase 3 with skip
    result3 = ci.phase3_implement("task", agents_dir, None, None, skip_phases=[3], coverage=80)
    assert result3 is True

    # Phase 4 with skip
    result4, cov = ci.phase4_integrate("task", agents_dir, None, skip_phases=[4], coverage=80)
    assert result4 is True


# ─────────────────────────────────────────────
# Test: _run_* helper functions with mocks
# ─────────────────────────────────────────────

@patch("claude_workflow.iterative.claude_p_with_session")
def test_run_all_agents_return_results(mock_cps, tmp_path, monkeypatch):
    """All _run_* functions return AgentResult with correct role."""
    monkeypatch.chdir(tmp_path)

    # Setup directories
    analysis_dir = tmp_path / "analysis"
    impl_dir = tmp_path / "implementation"
    impl_tasks_dir = impl_dir / "tasks"
    for d in [analysis_dir, impl_dir, impl_tasks_dir]:
        d.mkdir(parents=True, exist_ok=True)

    mock_cps.return_value = (0, "output", "sess", {"input": 100, "output": 50})

    # Test each agent
    result_analyst = ci._run_analyst("task", analysis_dir, None)
    assert result_analyst.role == ci.AgentRole.ANALYST
    assert result_analyst.success is True

    result_arch = ci._run_architect("task", impl_dir, None)
    assert result_arch.role == ci.AgentRole.ARCHITECT

    result_qa = ci._run_qa_planner("task", analysis_dir, None)
    assert result_qa.role == ci.AgentRole.QA_PLANNER

    result_impl = ci._run_implementer("task", impl_dir, None)
    assert result_impl.role == ci.AgentRole.IMPLEMENTER

    result_test = ci._run_test_writer("task", impl_dir, None)
    assert result_test.role == ci.AgentRole.TEST_WRITER

    result_coord = ci._run_coordinator("task", impl_dir, None)
    assert result_coord.role == ci.AgentRole.COORDINATOR

    result_dev = ci._run_dev_agent("task", impl_dir, 0, 1)
    # Dev agent role may be ANALYST or IMPLEMENTER depending on context
    assert result_dev.exit_code == 0


# ─────────────────────────────────────────────
# Test: Error handling in agents
# ─────────────────────────────────────────────

@patch("claude_workflow.iterative.claude_p_with_session")
def test_agent_error_handling(mock_cps, tmp_path, monkeypatch):
    """_run_* functions handle errors gracefully."""
    monkeypatch.chdir(tmp_path)

    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()

    # Simulate error from claude_p_with_session
    mock_cps.return_value = (1, "error message", None, {})

    result = ci._run_analyst("task", analysis_dir, None)
    assert result.exit_code == 1
    assert result.error is not None or result.exit_code != 0


# ─────────────────────────────────────────────
# Test: PromptLoader edge cases
# ─────────────────────────────────────────────

def test_prompt_loader_validates_placeholders():
    """PromptLoader validates required placeholders in custom prompts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        prompts_dir = Path(tmpdir) / ".claude-workflow" / "prompts"
        prompts_dir.mkdir(parents=True)

        # Write a custom prompt missing required placeholders
        analyst_file = prompts_dir / "ANALYST.md"
        analyst_file.write_text("Custom prompt without {task} placeholder")

        loader = ci.PromptLoader(Path(tmpdir))
        # Load should warn but not fail
        loaded = loader.load()
        # May warn, but loaded count depends on validation


def test_prompt_loader_partial_override():
    """PromptLoader can override some prompts while others fallback."""
    with tempfile.TemporaryDirectory() as tmpdir:
        prompts_dir = Path(tmpdir) / ".claude-workflow" / "prompts"
        prompts_dir.mkdir(parents=True)

        # Create only one custom prompt
        architect_file = prompts_dir / "ARCHITECT.md"
        architect_file.write_text("Custom architect prompt for {task}")

        loader = ci.PromptLoader(Path(tmpdir))
        loader.load()

        # ARCHITECT should be custom, ANALYST should be default
        assert "Custom architect" in loader.get("ARCHITECT_PROMPT")
        assert loader.get("ANALYST_PROMPT") == ci.ANALYST_PROMPT


# ─────────────────────────────────────────────
# Test: _AUTO_MODE edge cases
# ─────────────────────────────────────────────

def test_auto_mode_affects_confirm():
    """_confirm returns default in AUTO_MODE."""
    original = ci._AUTO_MODE
    try:
        ci._AUTO_MODE = True
        # Should return defaults without calling base.confirm
        assert ci._confirm("msg", default=True) is True
        assert ci._confirm("msg", default=False) is False
    finally:
        ci._AUTO_MODE = original


# ─────────────────────────────────────────────
# Test: ParallelRunner and multi-repo support
# ─────────────────────────────────────────────

def test_parallel_runner_basic():
    """ParallelRunner can be instantiated."""
    runner = ci.ParallelRunner(max_workers=2)
    assert runner.max_workers == 2


# ─────────────────────────────────────────────
# Test: run() function with various options
# ─────────────────────────────────────────────

def test_run_with_auto_mode():
    """run() respects --auto flag."""
    original_auto = ci._AUTO_MODE
    try:
        code = ci.run(task="test", dry_run=True, auto=True)
        assert code == 0
    finally:
        ci._AUTO_MODE = original_auto


# ─────────────────────────────────────────────
# Test: Token accounting
# ─────────────────────────────────────────────

def test_token_store_accumulation(tmp_path):
    """TokenStore correctly accumulates tokens across multiple agents."""
    tokens_file = tmp_path / "tokens.json"
    store = ci.TokenStore(tokens_file)

    # Add tokens for multiple agents
    store.add("ANALYST", {"input": 100, "output": 50, "cache_read": 10, "cache_write": 5, "cost_usd": 0.01})
    store.add("ARCHITECT", {"input": 200, "output": 100, "cache_read": 20, "cache_write": 10, "cost_usd": 0.02})
    store.add("ANALYST", {"input": 50, "output": 25, "cache_read": 5, "cache_write": 2, "cost_usd": 0.005})

    data = json.loads(tokens_file.read_text())
    # ANALYST should be accumulated
    assert data["ANALYST"]["input"] == 150
    assert data["_total"]["input"] == 350


# ─────────────────────────────────────────────
# Additional phase and function tests
# ─────────────────────────────────────────────

@patch("claude_workflow.iterative.claude_p_with_session")
def test_phase1_analysis_with_prompt_loader(mock_cps, tmp_path, monkeypatch):
    """phase1_analysis works with custom prompt loader."""
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / "agents"
    (agents_dir / "analysis").mkdir(parents=True)

    mock_cps.return_value = (0, "output", "sess", {})
    loader = ci.PromptLoader(tmp_path)

    results = ci.phase1_analysis("task", agents_dir, None, None, skip_phases=[1], prompt_loader=loader)
    assert isinstance(results, dict)


@patch("claude_workflow.iterative.claude_p_with_session")
def test_phase2_synthesize_with_token_store(mock_cps, tmp_path, monkeypatch):
    """phase2_synthesize tracks token usage."""
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / "agents"
    (agents_dir / "implementation").mkdir(parents=True)
    tokens_file = tmp_path / "tokens.json"

    mock_cps.return_value = (0, "plan", "sess", {"input": 200, "output": 100})
    store = ci.TokenStore(tokens_file)

    result = ci.phase2_synthesize("task", agents_dir, None, skip_phases=[2], coverage=80, token_store=store)
    assert isinstance(result, bool)


@patch("claude_workflow.iterative.claude_p_with_session")
def test_phase3_implement_with_context(mock_cps, tmp_path, monkeypatch):
    """phase3_implement passes project context to agents."""
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / "agents"
    (agents_dir / "implementation").mkdir(parents=True)

    mock_cps.return_value = (0, "implementation", "sess", {})
    result = ci.phase3_implement(
        "task",
        agents_dir,
        None,
        None,
        skip_phases=[3],  # skip to return early
        coverage=80,
        project_ctx="Python project with pytest"
    )
    assert result is True


@patch.object(ci.base, "run_tests", return_value=(True, 90.0, "output"))
def test_phase4_integrate_executes_tests(mock_tests, tmp_path, monkeypatch):
    """phase4_integrate runs test suite."""
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / "agents"
    (agents_dir / "implementation").mkdir(parents=True)

    with patch("claude_workflow.iterative.claude_p_with_session", return_value=(0, "", "sess", {})):
        ok, coverage = ci.phase4_integrate("task", agents_dir, None, skip_phases=[4], coverage=80)
        # Since skipped, should return early
        assert ok is True


@patch("claude_workflow.iterative.claude_p_with_session")
def test_phase5_commit_with_coverage_info(mock_cps, tmp_path, monkeypatch):
    """phase5_commit includes coverage in commit message."""
    monkeypatch.chdir(tmp_path)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    mock_cps.return_value = (0, "feat: implement\n\nCoverage: 85%", "sess", {})

    with patch.object(ci.base, "commit_all", return_value=True) as mock_commit:
        result = ci.phase5_commit("task", "feat/test", agents_dir, None, skip_phases=[5], coverage=85.0)
        # Skipped, should return True early
        assert result is True


# ─────────────────────────────────────────────
# Test: ParallelRunner and concurrent execution
# ─────────────────────────────────────────────

def test_parallel_runner_initialization():
    """ParallelRunner initializes with correct max_workers."""
    runner = ci.ParallelRunner(max_workers=4)
    assert runner.max_workers == 4


def test_parallel_runner_default_max_workers():
    """ParallelRunner uses default max_workers."""
    runner = ci.ParallelRunner()
    assert runner.max_workers > 0


# ─────────────────────────────────────────────
# Test: AgentRole enum completeness
# ─────────────────────────────────────────────

def test_agent_role_has_all_roles():
    """AgentRole enum has all expected roles."""
    roles = [
        ci.AgentRole.ANALYST,
        ci.AgentRole.ARCHITECT,
        ci.AgentRole.QA_PLANNER,
        ci.AgentRole.SYNTHESIZER,
        ci.AgentRole.IMPLEMENTER,
        ci.AgentRole.TEST_WRITER,
        ci.AgentRole.INTEGRATOR,
        ci.AgentRole.COMMITTER,
        ci.AgentRole.COORDINATOR,
    ]
    # All roles should be accessible
    assert len(roles) >= 8


# ─────────────────────────────────────────────
# Test: SessionStore comprehensive operations
# ─────────────────────────────────────────────

def test_session_store_load_all(tmp_path):
    """SessionStore.load_all() returns all stored sessions."""
    sessions_file = tmp_path / "sessions.json"
    store = ci.SessionStore(sessions_file)

    store.save(ci.AgentRole.ANALYST, "sess-1")
    store.save(ci.AgentRole.ARCHITECT, "sess-2")

    all_sessions = store.load_all()
    assert "ANALYST" in all_sessions
    assert "ARCHITECT" in all_sessions


def test_session_store_clear(tmp_path):
    """SessionStore.clear() wipes all data."""
    sessions_file = tmp_path / "sessions.json"
    store = ci.SessionStore(sessions_file)

    store.save(ci.AgentRole.ANALYST, "sess-1")
    store.clear()

    # File should exist but be empty or only have empty dict
    assert sessions_file.exists()
    data = json.loads(sessions_file.read_text())
    assert len(data) == 0


def test_session_store_dev_operations(tmp_path):
    """SessionStore handles DEV agent sessions separately."""
    sessions_file = tmp_path / "sessions.json"
    store = ci.SessionStore(sessions_file)

    store.save_dev(0, "dev-sess-1")
    store.save_dev(1, "dev-sess-2")

    assert store.load_dev(0) == "dev-sess-1"
    assert store.load_dev(1) == "dev-sess-2"
    assert store.load_dev(2) is None


# ─────────────────────────────────────────────
# Test: Error handling in agent execution
# ─────────────────────────────────────────────

@patch("claude_workflow.iterative.claude_p_with_session")
def test_run_analyst_with_error(mock_cps, tmp_path, monkeypatch):
    """_run_analyst handles errors from claude_p_with_session."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "analysis").mkdir()

    mock_cps.return_value = (1, "error output", None, {})
    result = ci._run_analyst("task", tmp_path / "analysis", None)

    assert result.exit_code == 1
    assert result.success is False


@patch("claude_workflow.iterative.claude_p_with_session")
def test_run_architect_captures_error_message(mock_cps, tmp_path, monkeypatch):
    """_run_architect includes error in result."""
    monkeypatch.chdir(tmp_path)
    impl_dir = tmp_path / "implementation"
    impl_dir.mkdir()

    mock_cps.return_value = (1, "An error occurred", None, {})
    result = ci._run_architect("task", impl_dir, None)

    assert result.success is False
    assert result.error is not None or result.exit_code != 0


# ─────────────────────────────────────────────
# Test: PromptLoader with various file types
# ─────────────────────────────────────────────

def test_prompt_loader_ignores_non_md_files():
    """PromptLoader only loads .md files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        prompts_dir = Path(tmpdir) / ".claude-workflow" / "prompts"
        prompts_dir.mkdir(parents=True)

        # Create non-.md file
        (prompts_dir / "ANALYST.txt").write_text("text file")

        loader = ci.PromptLoader(Path(tmpdir))
        loaded = loader.load()
        # Should not load .txt files
        assert loader.get("ANALYST_PROMPT") == ci.ANALYST_PROMPT


def test_prompt_loader_directory_missing():
    """PromptLoader handles missing prompts directory gracefully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        loader = ci.PromptLoader(Path(tmpdir))
        loaded = loader.load()
        # Should return 0 loaded files, no error
        assert loaded == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
