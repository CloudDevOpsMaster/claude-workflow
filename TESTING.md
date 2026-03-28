# Testing Guide

Documentación completa para ejecutar, escribir y mantener tests en claude-workflow.

## 📊 Coverage Status

```
Module           Coverage   Tests  Status
────────────────────────────────────────────
iterative.py       60%       188   ✅ Core
multi.py           77%        48   ✅ Orquestación
plan_exec.py       67%       134   ✅ Flujo
────────────────────────────────────────────
TOTAL              64%       248   ✅ Baseline
```

**Meta:** >80% (requiere ~213 statements más)

---

## 🚀 Quick Start

### Ejecutar todos los tests
```bash
uv run --group dev pytest tests/ -v
```

### Ver coverage interactivo
```bash
uv run --group dev pytest tests/ --cov=claude_workflow --cov-report=html
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

### Ejecutar un archivo específico
```bash
uv run --group dev pytest tests/test_iterative.py -v
```

### Ejecutar un test específico
```bash
uv run --group dev pytest tests/test_plan_exec.py::test_parse_coverage_pytest_format -v
```

### Watch mode (rerun en cambios)
```bash
uv run --group dev ptw tests/
```

---

## 📁 Test Files & Organization

### test_iterative.py (188 tests)

**Scope:** Core workflow phases, agents, utilities

#### Categories
- **`_confirm()`** - User confirmation logic
  - Auto mode (returns default)
  - Interactive mode (calls base.confirm)

- **`_collect_project_context()`** - Project introspection
  - Python version detection
  - Pyproject.toml detection
  - Conftest.py detection

- **`AgentRole` enum** - Role definitions
  - All 9 agent roles
  - Role values accessible

- **`AgentResult` class** - Result tracking
  - Success detection (exit_code=0 + error=None)
  - Error handling

- **`TokenStore`** - Token accounting
  - Accumulation across agents
  - File persistence
  - Thread safety

- **`SessionStore`** - Session persistence
  - Save/load per role
  - Save/load for DEV agents
  - Missing file handling

- **`PromptLoader`** - Custom prompts
  - Load from .md files
  - Validate placeholders
  - Fallback to defaults
  - init_prompts_dir()

- **Phase Functions** - Workflow phases
  - `phase0_branch()` - Create branch
  - `phase1_analysis()` - 3 parallel agents
  - `phase2_synthesize()` - Merge analysis
  - `phase3_implement()` - Implementation
  - `phase4_integrate()` - Tests & coverage
  - `phase5_commit()` - Auto-commit

- **Agent Runners** - Individual agents
  - `_run_analyst()` - Analysis
  - `_run_architect()` - Architecture planning
  - `_run_qa_planner()` - QA planning
  - `_run_implementer()` - Implementation
  - `_run_test_writer()` - Test generation
  - `_run_coordinator()` - Coordination
  - `_run_dev_agent()` - Development agent

- **CLI & Main** - Command-line
  - `run()` - Main workflow
  - Dry-run mode
  - Auto mode
  - Skip phases

### test_multi.py (48 tests)

**Scope:** Multi-repository orchestration

#### Categories
- **`RepoConfig`** - Repository configuration
  - Path resolution
  - Optional branch

- **`MultiConfig`** - Multi-repo config
  - Task + repos
  - Max workers

- **`RepoResult`** - Execution results
  - Status (success/failure/timeout)
  - Error messages
  - Duration tracking

- **`run_repo_task()`** - Single repo execution
  - Success case
  - Failure case
  - Timeout handling
  - Exception handling
  - Branch configuration
  - Auto flag

- **`run_parallel()`** - Parallel execution
  - Single repo
  - Multiple repos
  - Result sorting
  - Mixed results

- **`parse_config()`** - Config parsing
  - JSON files
  - YAML files
  - Validation
  - Error handling

- **`generate_report()`** - Report generation
  - Single repo
  - Mixed results
  - Duration display
  - Error details

### test_plan_exec.py (134 tests) - NEW

**Scope:** Plan execution workflow (entire pipeline)

#### Categories
- **Pure Logic Functions** - No I/O
  - `_is_token_exhausted()` - Detect context window exceeded
  - `parse_coverage()` - Extract % from output
  - `_accum_usage()` - Accumulate token counts
  - `timestamp_branch()` - Generate branch name
  - `confirm()` - User confirmation

- **Git Helpers** - Git operations via subprocess
  - `git()` - Raw git command
  - `current_branch()` - Get current branch
  - `create_branch()` - Create new branch
  - `commit_all()` - Stage & commit
  - `_delete_branch()` - Cleanup

- **Claude Integration** - Claude CLI
  - `claude_p()` - Execute prompt with JSON output
  - `claude_stream()` - Stream responses
  - `_call_opencode()` - Fallback to opencode

- **File Operations** - Token reporting
  - `_write_tokens_report()` - Save token usage

- **Test Execution** - pytest/jest
  - `run_tests()` - Detect & run tests
  - Coverage extraction

- **Workflow Steps** - Pipeline phases
  - `step_branch()` - Branch creation
  - `step_plan_loop()` - Plan generation & refinement
  - `step_execute()` - Implementation execution
  - `step_tests()` - Test generation & verification
  - `step_commit()` - Auto-commit

- **CLI** - Command-line interface
  - `main()` - Argument parsing
  - All supported flags

---

## 🎯 Writing New Tests

### Template

```python
import pytest
from unittest.mock import patch, Mock
from pathlib import Path
import tempfile

from claude_workflow import iterative as ci  # or multi, plan_exec


def test_description_of_what_it_does():
    """Clear docstring explaining the test."""
    # Arrange
    setup_data = "value"

    # Act
    result = ci.function_under_test(setup_data)

    # Assert
    assert result == expected_value
```

### With Mocking

```python
@patch("claude_workflow.iterative.subprocess.run")
def test_git_operation(mock_run):
    """Test git command execution."""
    # Mock the subprocess call
    mock_run.return_value = Mock(
        returncode=0,
        stdout="output text",
        stderr=""
    )

    # Call function
    result = ci.my_function()

    # Assert result
    assert result.success is True

    # Assert mock was called correctly
    mock_run.assert_called_once()
```

### With Fixtures

```python
@pytest.fixture
def agents_dir(tmp_path):
    """Create temporary agents directory."""
    agents = tmp_path / "agents"
    (agents / "analysis").mkdir(parents=True)
    (agents / "implementation").mkdir(parents=True)
    return agents


def test_with_fixtures(agents_dir):
    """Test using fixture."""
    assert (agents_dir / "analysis").exists()
```

### With Temporary Files

```python
def test_file_operations(tmp_path, monkeypatch):
    """Test file I/O with isolation."""
    # Change to temp directory
    monkeypatch.chdir(tmp_path)

    # Create files
    (tmp_path / "test.txt").write_text("content")

    # Test function
    result = ci.function_using_files()

    # Verify files
    assert (tmp_path / "output.txt").exists()
```

### Best Practices

✅ **DO:**
- Use descriptive test names: `test_function_does_X_when_Y_happens()`
- One assertion per test (or related assertions)
- Mock external dependencies (subprocess, file I/O, API calls)
- Use `tmp_path` for file operations
- Use `monkeypatch` for environment changes
- Include docstrings explaining what is tested
- Test both success and failure paths
- Test edge cases and boundary conditions

❌ **DON'T:**
- Don't hardcode paths
- Don't test implementation details (test behavior)
- Don't make real network calls
- Don't run actual subprocess commands
- Don't make tests interdependent
- Don't test other people's code (libraries)
- Don't skip error cases

---

## 🔍 Finding Coverage Gaps

### Visual Report
```bash
uv run --group dev pytest tests/ --cov=claude_workflow --cov-report=html
# Open htmlcov/index.html to see red/yellow/green lines
```

### Terminal Report
```bash
uv run --group dev pytest tests/ --cov=claude_workflow --cov-report=term-missing -q
```

### Per-Module Report
```bash
uv run --group dev pytest tests/ --cov=claude_workflow.iterative --cov-report=term-missing
```

### Verbose Missing Lines
```bash
uv run --group dev pytest tests/ --cov=claude_workflow --cov-report=term-missing:skip-covered
```

---

## 🚨 Common Issues

### "ModuleNotFoundError: No module named 'claude_workflow'"

**Solution:** Make sure you're running from the project root:
```bash
cd /path/to/claude-workflow
uv run --group dev pytest tests/
```

### "pytest: reading from stdin while output is captured"

**Cause:** Test calls `input()` or `confirm()` without mocking
**Solution:** Mock the input or use `monkeypatch`:
```python
@patch("builtins.input", return_value="y")
def test_confirms(mock_input):
    result = ci._confirm("Continue?", default=False)
    assert result is True
```

### "AttributeError: 'NoneType' object has no attribute..."

**Cause:** Mocked function returns None instead of proper object
**Solution:** Return proper mock with required attributes:
```python
@patch("claude_workflow.iterative.subprocess.run")
def test_with_proper_mock(mock_run):
    # Wrong:
    mock_run.return_value = None

    # Right:
    mock_run.return_value = Mock(
        returncode=0,
        stdout="output",
        stderr=""
    )
```

---

## 📈 Coverage Improvement Strategy

### Phase 1: Core Functions (Already Done ✅)
- ✅ Pure logic: parse_coverage, accum_usage, timestamp_branch
- ✅ Helpers: git operations, file I/O
- ✅ Integration: subprocess calls

### Phase 2: Phase Functions (Partially Done)
- ✅ Phase skip paths (mock-based)
- 🟡 Phase success paths (need more complete flows)
- 🟡 Error handling paths (need exception scenarios)

### Phase 3: Multi-Path Functions (Remaining)
- 🔴 Complex branches in phase1_analysis
- 🔴 Multi-agent coordination paths
- 🔴 Resume/retry logic

### Phase 4: Edge Cases (Remaining)
- 🔴 Token exhaustion fallbacks
- 🔴 Timeout handling
- 🔴 Concurrent execution scenarios

---

## 🔄 CI/CD Integration

### GitHub Actions Example
```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: astral-sh/setup-uv@v2
      - run: uv run --group dev pytest tests/ --cov=claude_workflow
      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          files: ./coverage.xml
```

### Pre-commit Hook
```bash
#!/bin/bash
# .git/hooks/pre-commit

cd "$(git rev-parse --show-toplevel)"
uv run --group dev pytest tests/ -q
if [ $? -ne 0 ]; then
    echo "❌ Tests failed. Commit aborted."
    exit 1
fi
echo "✅ Tests passed"
```

---

## 📚 Additional Resources

- [pytest documentation](https://docs.pytest.org/)
- [unittest.mock docs](https://docs.python.org/3/library/unittest.mock.html)
- [Coverage.py docs](https://coverage.readthedocs.io/)
- [Python Testing Best Practices](https://docs.pytest.org/en/latest/goodpractices.html)

---

## 📝 Test Statistics

**Created:** 2026-03-27
**Coverage Improvement:** 36% → 64% (+28pp)
**Tests Added:** 184 (36 → 220 previously)
**Total Tests:** 248

**Breakdown by Module:**
- iterative.py: 188 tests (42% → 60%)
- multi.py: 48 tests (57% → 77%)
- plan_exec.py: 134 tests (13% → 67%)

---

Last updated: 2026-03-27
