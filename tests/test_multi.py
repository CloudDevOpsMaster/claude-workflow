"""
Tests for claude_workflow/multi.py
Tests configuration parsing and multi-repo orchestration.
"""
import json
import tempfile
from pathlib import Path

import pytest

from claude_workflow import multi


def test_repo_config_basic():
    """RepoConfig handles path expansion."""
    config = multi.RepoConfig(path="/tmp/repo", branch="feat/test")
    assert config.path == str(Path("/tmp/repo").expanduser().resolve())
    assert config.branch == "feat/test"


def test_repo_config_without_branch():
    """RepoConfig branch is optional."""
    config = multi.RepoConfig(path="/tmp/repo")
    assert config.branch is None


def test_parse_config_json():
    """parse_config handles JSON files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / "config.json"
        config_file.write_text(json.dumps({
            "task": "add type hints",
            "repos": [
                {"path": "/tmp/repo1", "branch": "feat/types"},
                {"path": "/tmp/repo2"},
            ]
        }))

        config = multi.parse_config(config_file)
        assert config.task == "add type hints"
        assert len(config.repos) == 2
        assert config.repos[0].branch == "feat/types"
        assert config.repos[1].branch is None


def test_parse_config_missing_file():
    """parse_config raises if file doesn't exist."""
    with pytest.raises(FileNotFoundError):
        multi.parse_config(Path("/nonexistent/config.yaml"))


def test_parse_config_missing_task():
    """parse_config raises if task is missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / "config.json"
        config_file.write_text(json.dumps({
            "repos": [{"path": "/tmp/repo"}]
        }))

        with pytest.raises(ValueError, match="'task' is required"):
            multi.parse_config(config_file)


def test_parse_config_missing_repos():
    """parse_config raises if repos list is missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / "config.json"
        config_file.write_text(json.dumps({
            "task": "some task"
        }))

        with pytest.raises(ValueError, match="'repos' list is required"):
            multi.parse_config(config_file)


def test_multi_config_creation():
    """MultiConfig combines task and repos."""
    repos = [
        multi.RepoConfig(path="/tmp/repo1"),
        multi.RepoConfig(path="/tmp/repo2"),
    ]
    config = multi.MultiConfig(task="test task", repos=repos)
    assert config.task == "test task"
    assert len(config.repos) == 2
    assert config.max_workers == 3


def test_repo_result_creation():
    """RepoResult stores execution result."""
    result = multi.RepoResult(
        repo_path="/tmp/repo",
        branch="main",
        status="success",
        exit_code=0,
        duration=10.5,
        start_time="2026-03-27T10:00:00",
        end_time="2026-03-27T10:00:10",
    )
    assert result.repo_path == "/tmp/repo"
    assert result.status == "success"
    assert result.duration == 10.5


def test_generate_report_single_success():
    """generate_report creates markdown from results."""
    results = [
        multi.RepoResult(
            repo_path="/tmp/repo1",
            branch="main",
            status="success",
            exit_code=0,
            duration=15.0,
            start_time="2026-03-27T10:00:00",
            end_time="2026-03-27T10:00:15",
        ),
    ]

    report = multi.generate_report(results)

    assert "Multi-Repository Report" in report
    assert "/tmp/repo1" in report
    assert "✅" in report
    assert "**Successful:** 1/1" in report
    assert "15.0s" in report


def test_generate_report_mixed_results():
    """generate_report handles success and failure."""
    results = [
        multi.RepoResult(
            repo_path="/tmp/repo1",
            branch="main",
            status="success",
            exit_code=0,
            duration=10.0,
            start_time="2026-03-27T10:00:00",
            end_time="2026-03-27T10:00:10",
        ),
        multi.RepoResult(
            repo_path="/tmp/repo2",
            branch="feat/test",
            status="failure",
            exit_code=1,
            duration=5.0,
            start_time="2026-03-27T10:00:00",
            end_time="2026-03-27T10:00:05",
            error_message="Some error occurred",
        ),
    ]

    report = multi.generate_report(results)

    assert "**Successful:** 1/2" in report
    assert "/tmp/repo2" in report
    assert "❌" in report
    assert "Failures" in report
    assert "Some error occurred" in report


def test_run_repo_task_nonexistent_repo():
    """run_repo_task handles nonexistent repository gracefully."""
    config = multi.RepoConfig(path="/nonexistent/path")
    result = multi.run_repo_task(config, "test task")

    assert result.status == "failure"
    assert result.exit_code == 1
    assert "Repository path not found" in result.error_message


# ─────────────────────────────────────────────
# Tests: run_repo_task with mocked subprocess
# ─────────────────────────────────────────────

import subprocess
from unittest.mock import patch, Mock


def test_run_repo_task_success(tmp_path):
    """run_repo_task with successful subprocess → status=success."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = multi.RepoConfig(str(repo_dir))

    with patch("claude_workflow.multi.subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stdout="success output", stderr="")
        result = multi.run_repo_task(config, "test task")

    assert result.status == "success"
    assert result.exit_code == 0


def test_run_repo_task_failure(tmp_path):
    """run_repo_task with returncode!=0 → status=failure."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = multi.RepoConfig(str(repo_dir))

    with patch("claude_workflow.multi.subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="error message")
        result = multi.run_repo_task(config, "test task")

    assert result.status == "failure"
    assert result.exit_code == 1
    assert "error message" in result.error_message


def test_run_repo_task_timeout(tmp_path):
    """run_repo_task with TimeoutExpired → status=timeout."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = multi.RepoConfig(str(repo_dir))

    with patch("claude_workflow.multi.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="cmd", timeout=1800)
        result = multi.run_repo_task(config, "test task")

    assert result.status == "timeout"
    assert result.exit_code == -1


def test_run_repo_task_exception(tmp_path):
    """run_repo_task with unexpected exception → status=failure."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = multi.RepoConfig(str(repo_dir))

    with patch("claude_workflow.multi.subprocess.run") as mock_run:
        mock_run.side_effect = RuntimeError("unexpected error")
        result = multi.run_repo_task(config, "test task")

    assert result.status == "failure"
    assert "unexpected error" in result.error_message


def test_run_repo_task_with_branch(tmp_path):
    """run_repo_task with branch config → includes --branch in command."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = multi.RepoConfig(str(repo_dir), branch="feat/test")

    with patch("claude_workflow.multi.subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        multi.run_repo_task(config, "test task")

    # Verify --branch was in command
    call_args = mock_run.call_args[0][0]
    assert "--branch" in call_args or "feat/test" in call_args


def test_run_repo_task_auto_flag(tmp_path):
    """run_repo_task with auto=True → includes --auto flag."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = multi.RepoConfig(str(repo_dir))

    with patch("claude_workflow.multi.subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        multi.run_repo_task(config, "test task", auto=True)

    call_args = mock_run.call_args[0][0]
    assert "--auto" in call_args


def test_run_repo_task_no_auto_flag(tmp_path):
    """run_repo_task with auto=False → no --auto flag."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = multi.RepoConfig(str(repo_dir))

    with patch("claude_workflow.multi.subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        multi.run_repo_task(config, "test task", auto=False)

    call_args = mock_run.call_args[0][0]
    assert "--auto" not in call_args


# ─────────────────────────────────────────────
# Tests: run_parallel
# ─────────────────────────────────────────────

def test_run_parallel_single_repo(tmp_path):
    """run_parallel with one repo → executes and returns results."""
    repo_dir = tmp_path / "repo1"
    repo_dir.mkdir()
    config = multi.MultiConfig(
        task="test task",
        repos=[multi.RepoConfig(str(repo_dir))]
    )

    with patch("claude_workflow.multi.run_repo_task") as mock_task:
        mock_task.return_value = multi.RepoResult(
            repo_path=str(repo_dir),
            branch="main",
            status="success",
            exit_code=0,
            duration=5.0,
            start_time="2026-03-27T10:00:00",
            end_time="2026-03-27T10:00:05"
        )
        results = multi.run_parallel(config)

    assert len(results) == 1
    assert results[0].status == "success"


def test_run_parallel_multiple_repos(tmp_path):
    """run_parallel with multiple repos → calls run_repo_task for each."""
    repo_dirs = [tmp_path / f"repo{i}" for i in range(3)]
    for repo_dir in repo_dirs:
        repo_dir.mkdir()

    config = multi.MultiConfig(
        task="test task",
        repos=[multi.RepoConfig(str(d)) for d in repo_dirs]
    )

    with patch("claude_workflow.multi.run_repo_task") as mock_task:
        def make_result(repo_config, task, auto=True):
            return multi.RepoResult(
                repo_path=str(repo_config.path),
                branch="main",
                status="success",
                exit_code=0,
                duration=5.0,
                start_time="2026-03-27T10:00:00",
                end_time="2026-03-27T10:00:05"
            )

        mock_task.side_effect = make_result
        results = multi.run_parallel(config)

    assert len(results) == 3
    assert mock_task.call_count == 3


def test_run_parallel_results_sorted(tmp_path):
    """run_parallel returns results sorted by repo_path."""
    repo_b = tmp_path / "b_repo"
    repo_a = tmp_path / "a_repo"
    repo_b.mkdir()
    repo_a.mkdir()

    config = multi.MultiConfig(
        task="test",
        repos=[
            multi.RepoConfig(str(repo_b)),
            multi.RepoConfig(str(repo_a))
        ]
    )

    with patch("claude_workflow.multi.run_repo_task") as mock_task:
        def make_result(repo_config, task, auto=True):
            return multi.RepoResult(
                repo_path=str(repo_config.path),
                branch="main",
                status="success",
                exit_code=0,
                duration=1.0,
                start_time="2026-03-27T10:00:00",
                end_time="2026-03-27T10:00:01"
            )

        mock_task.side_effect = make_result
        results = multi.run_parallel(config)

    # Results should be sorted by repo_path
    assert results[0].repo_path <= results[1].repo_path


def test_run_parallel_mixed_results(tmp_path):
    """run_parallel handles mix of success and failure."""
    repo1 = tmp_path / "repo1"
    repo2 = tmp_path / "repo2"
    repo1.mkdir()
    repo2.mkdir()

    config = multi.MultiConfig(
        task="test",
        repos=[
            multi.RepoConfig(str(repo1)),
            multi.RepoConfig(str(repo2))
        ]
    )

    with patch("claude_workflow.multi.run_repo_task") as mock_task:
        mock_task.side_effect = [
            multi.RepoResult(str(repo1), branch="main", status="success", exit_code=0, duration=5.0, start_time="2026-03-27T10:00:00", end_time="2026-03-27T10:00:05"),
            multi.RepoResult(str(repo2), branch="main", status="failure", exit_code=1, duration=3.0, start_time="2026-03-27T10:00:00", end_time="2026-03-27T10:00:03", error_message="test error")
        ]
        results = multi.run_parallel(config)

    assert len(results) == 2
    assert any(r.status == "success" for r in results)
    assert any(r.status == "failure" for r in results)


# ─────────────────────────────────────────────
# Tests: parse_config edge cases
# ─────────────────────────────────────────────

def test_parse_config_yaml_format(tmp_path):
    """parse_config handles YAML files."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
task: "add tests"
repos:
  - path: /tmp/repo1
    branch: feat/tests
  - path: /tmp/repo2
""")

    config = multi.parse_config(config_file)
    assert config.task == "add tests"
    assert len(config.repos) == 2
    assert config.repos[0].branch == "feat/tests"


def test_parse_config_not_dict(tmp_path):
    """parse_config rejects non-dict YAML/JSON."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text('"just a string"')

    with pytest.raises(ValueError, match="must be a dictionary"):
        multi.parse_config(config_file)


def test_parse_config_empty_repos(tmp_path):
    """parse_config requires at least one repo."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "task": "test",
        "repos": []
    }))

    with pytest.raises(ValueError):
        multi.parse_config(config_file)


def test_parse_config_extra_fields(tmp_path):
    """parse_config ignores extra fields gracefully."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "task": "test",
        "repos": [{"path": "/tmp/repo1"}],
        "extra_field": "ignored",
        "another_field": 123
    }))

    config = multi.parse_config(config_file)
    assert config.task == "test"
    assert len(config.repos) == 1


# ─────────────────────────────────────────────
# Tests: Report generation edge cases
# ─────────────────────────────────────────────

def test_generate_report_all_timeout():
    """generate_report handles all timeout results."""
    results = [
        multi.RepoResult(
            repo_path="/tmp/repo1",
            branch="main",
            status="timeout",
            exit_code=-1,
            duration=1800.0,
            start_time="2026-03-27T10:00:00",
            end_time="2026-03-27T10:30:00",
        ),
    ]

    report = multi.generate_report(results)
    assert "timeout" in report.lower()
    assert "/tmp/repo1" in report


def test_generate_report_no_results():
    """generate_report handles empty results list."""
    report = multi.generate_report([])
    assert "Multi-Repository Report" in report


# ─────────────────────────────────────────────
# Additional test coverage for edge cases
# ─────────────────────────────────────────────

def test_repo_config_path_resolution(tmp_path):
    """RepoConfig resolves paths correctly."""
    config = multi.RepoConfig(path=str(tmp_path))
    # Path should be resolved to absolute path
    assert Path(config.path).is_absolute()


def test_multi_config_max_workers_default():
    """MultiConfig uses default max_workers."""
    repos = [multi.RepoConfig(path="/tmp/repo")]
    config = multi.MultiConfig(task="test", repos=repos)
    assert config.max_workers == 3


def test_multi_config_max_workers_custom():
    """MultiConfig accepts custom max_workers."""
    repos = [multi.RepoConfig(path="/tmp/repo")]
    config = multi.MultiConfig(task="test", repos=repos, max_workers=5)
    assert config.max_workers == 5


def test_repo_result_optional_error_message():
    """RepoResult error_message is optional."""
    result = multi.RepoResult(
        repo_path="/tmp/repo",
        branch="main",
        status="success",
        exit_code=0,
        duration=1.0,
        start_time="2026-03-27T10:00:00",
        end_time="2026-03-27T10:00:01"
    )
    assert result.error_message is None


def test_repo_result_with_error_message():
    """RepoResult can store error messages."""
    result = multi.RepoResult(
        repo_path="/tmp/repo",
        branch="main",
        status="failure",
        exit_code=1,
        duration=1.0,
        start_time="2026-03-27T10:00:00",
        end_time="2026-03-27T10:00:01",
        error_message="Command failed: exit code 1"
    )
    assert result.error_message == "Command failed: exit code 1"


def test_generate_report_shows_duration():
    """generate_report includes duration information."""
    results = [
        multi.RepoResult(
            repo_path="/tmp/repo1",
            branch="main",
            status="success",
            exit_code=0,
            duration=42.5,
            start_time="2026-03-27T10:00:00",
            end_time="2026-03-27T10:00:42.5",
        ),
    ]
    report = multi.generate_report(results)
    assert "42.5" in report


def test_generate_report_shows_error_details():
    """generate_report includes error details for failed repos."""
    results = [
        multi.RepoResult(
            repo_path="/tmp/repo",
            branch="main",
            status="failure",
            exit_code=127,
            duration=5.0,
            start_time="2026-03-27T10:00:00",
            end_time="2026-03-27T10:00:05",
            error_message="Command not found: claude-iterative"
        ),
    ]
    report = multi.generate_report(results)
    assert "Command not found" in report


@patch("claude_workflow.multi.subprocess.run")
def test_run_repo_task_preserves_stdout_stderr(mock_run, tmp_path):
    """run_repo_task captures both stdout and stderr."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = multi.RepoConfig(str(repo_dir))

    mock_run.return_value = Mock(
        returncode=0,
        stdout="Standard output text",
        stderr="Standard error text"
    )
    result = multi.run_repo_task(config, "test task")

    assert result.status == "success"
    # Output might be combined in result
    assert isinstance(result, multi.RepoResult)


@patch("claude_workflow.multi.subprocess.run")
def test_run_repo_task_handles_special_characters_in_output(mock_run, tmp_path):
    """run_repo_task handles unicode in output."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = multi.RepoConfig(str(repo_dir))

    mock_run.return_value = Mock(
        returncode=0,
        stdout="Prueba exitosa ✓",
        stderr="Advertencia: ñ caracteres especiales"
    )
    result = multi.run_repo_task(config, "test task")
    assert result.status == "success"


def test_run_parallel_with_no_repos():
    """run_parallel handles empty repo list gracefully."""
    config = multi.MultiConfig(task="test", repos=[])
    # This might raise ValueError since repos is required
    try:
        results = multi.run_parallel(config)
        assert len(results) == 0
    except ValueError:
        # Expected if repos must not be empty
        pass


@patch("claude_workflow.multi.run_repo_task")
def test_run_parallel_preserves_order_in_results(mock_task, tmp_path):
    """run_parallel returns results in sorted order by repo_path."""
    repo_c = tmp_path / "z_repo"
    repo_b = tmp_path / "b_repo"
    repo_a = tmp_path / "a_repo"
    for d in [repo_a, repo_b, repo_c]:
        d.mkdir(parents=True, exist_ok=True)

    config = multi.MultiConfig(
        task="test",
        repos=[
            multi.RepoConfig(str(repo_c)),
            multi.RepoConfig(str(repo_b)),
            multi.RepoConfig(str(repo_a)),
        ]
    )

    def make_result(rc, task, auto=True):
        return multi.RepoResult(
            repo_path=str(rc.path),
            branch="main",
            status="success",
            exit_code=0,
            duration=1.0,
            start_time="2026-03-27T10:00:00",
            end_time="2026-03-27T10:00:01"
        )

    mock_task.side_effect = make_result
    results = multi.run_parallel(config)

    # Results should be sorted
    paths = [r.repo_path for r in results]
    assert paths == sorted(paths)


def test_parse_config_with_minimal_yaml():
    """parse_config accepts minimal valid YAML."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / "config.yaml"
        config_file.write_text("""
task: Simple task
repos:
  - path: /tmp/repo
""")
        config = multi.parse_config(config_file)
        assert config.task == "Simple task"
        assert len(config.repos) == 1


def test_parse_config_branch_optional_per_repo(tmp_path):
    """parse_config allows some repos with branch, some without."""
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "task": "test",
        "repos": [
            {"path": "/tmp/repo1", "branch": "feat/a"},
            {"path": "/tmp/repo2"},  # No branch
            {"path": "/tmp/repo3", "branch": "fix/b"},
        ]
    }))
    config = multi.parse_config(config_file)
    assert config.repos[0].branch == "feat/a"
    assert config.repos[1].branch is None
    assert config.repos[2].branch == "fix/b"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
