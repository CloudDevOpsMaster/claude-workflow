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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
