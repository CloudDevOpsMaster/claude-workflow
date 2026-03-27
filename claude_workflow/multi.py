#!/usr/bin/env python3
"""
claude_workflow/multi.py
Multi-repository orchestration for claude-iterative.

Reads a YAML config with task and repo list, executes claude-iterative
in parallel across repos, and generates a unified MULTI_REPORT.md.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

try:
    import yaml
except ImportError:
    yaml = None


@dataclass
class RepoConfig:
    """Configuration for a single repository."""
    path: str
    branch: Optional[str] = None

    def __post_init__(self):
        self.path = str(Path(self.path).expanduser().resolve())


@dataclass
class MultiConfig:
    """Multi-repo configuration."""
    task: str
    repos: List[RepoConfig]
    max_workers: int = 3


@dataclass
class RepoResult:
    """Result from executing claude-iterative in one repo."""
    repo_path: str
    branch: str
    status: str  # "success", "failure", "timeout"
    exit_code: int
    duration: float
    start_time: str
    end_time: str
    error_message: Optional[str] = None


def parse_config(config_file: Path) -> MultiConfig:
    """Load and parse YAML/JSON config file.

    Args:
        config_file: Path to config file (.yaml, .json)

    Returns:
        MultiConfig object

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config format is invalid
    """
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    content = config_file.read_text()

    # Try YAML first if available
    if yaml:
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {config_file}: {e}")
    else:
        # Fallback to JSON
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {config_file}: {e}")

    if not isinstance(data, dict):
        raise ValueError("Config must be a dictionary with 'task' and 'repos' keys")

    task = data.get("task")
    if not task:
        raise ValueError("'task' is required in config")

    repos_data = data.get("repos", [])
    if not repos_data:
        raise ValueError("'repos' list is required in config")

    repos = [
        RepoConfig(
            path=repo.get("path") if isinstance(repo, dict) else repo,
            branch=repo.get("branch") if isinstance(repo, dict) else None,
        )
        for repo in repos_data
    ]

    return MultiConfig(task=task, repos=repos)


def run_repo_task(config: RepoConfig, task: str, auto: bool = True) -> RepoResult:
    """Execute claude-iterative in a single repository.

    Args:
        config: RepoConfig with path and optional branch
        task: Task description for claude-iterative
        auto: Run in auto mode (no prompts)

    Returns:
        RepoResult with status, exit code, duration, etc.
    """
    repo_path = Path(config.path)
    if not repo_path.exists():
        return RepoResult(
            repo_path=str(repo_path),
            branch=config.branch or "N/A",
            status="failure",
            exit_code=1,
            duration=0.0,
            start_time=datetime.now().isoformat(),
            end_time=datetime.now().isoformat(),
            error_message=f"Repository path not found: {repo_path}",
        )

    start_time = datetime.now()
    t0 = time.perf_counter()

    # Build claude-iterative command
    cmd = [
        "claude-iterative",
        "-t", task,
        "--type", "feature",
    ]
    if config.branch:
        cmd.extend(["--branch", config.branch])
    if auto:
        cmd.append("--auto")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=1800,  # 30 minute timeout per repo
        )
        duration = time.perf_counter() - t0
        status = "success" if result.returncode == 0 else "failure"
        error_msg = result.stderr if result.returncode != 0 else None

        return RepoResult(
            repo_path=str(repo_path),
            branch=config.branch or "auto",
            status=status,
            exit_code=result.returncode,
            duration=duration,
            start_time=start_time.isoformat(),
            end_time=datetime.now().isoformat(),
            error_message=error_msg,
        )
    except subprocess.TimeoutExpired:
        duration = time.perf_counter() - t0
        return RepoResult(
            repo_path=str(repo_path),
            branch=config.branch or "N/A",
            status="timeout",
            exit_code=-1,
            duration=duration,
            start_time=start_time.isoformat(),
            end_time=datetime.now().isoformat(),
            error_message="Task execution exceeded 30 minute timeout",
        )
    except Exception as e:
        duration = time.perf_counter() - t0
        return RepoResult(
            repo_path=str(repo_path),
            branch=config.branch or "N/A",
            status="failure",
            exit_code=1,
            duration=duration,
            start_time=start_time.isoformat(),
            end_time=datetime.now().isoformat(),
            error_message=str(e),
        )


def run_parallel(config: MultiConfig, auto: bool = True) -> List[RepoResult]:
    """Execute claude-iterative across all repos in parallel.

    Args:
        config: MultiConfig with task and repos
        auto: Run in auto mode

    Returns:
        List of RepoResult objects
    """
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        futures = {
            executor.submit(run_repo_task, repo, config.task, auto): repo
            for repo in config.repos
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)

    # Sort by repo path for consistent output
    results.sort(key=lambda r: r.repo_path)
    return results


def generate_report(results: List[RepoResult]) -> str:
    """Generate MULTI_REPORT.md from results.

    Args:
        results: List of RepoResult objects

    Returns:
        Markdown report string
    """
    lines = [
        "# Multi-Repository Report\n",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n",
        "## Summary\n",
        "| Repository | Branch | Status | Duration (s) | Exit Code |",
        "|---|---|---|---:|---:|",
    ]

    total_duration = 0.0
    success_count = 0

    for result in results:
        status_mark = "✅" if result.status == "success" else "❌"
        status_text = result.status.upper()
        branch = result.branch or "N/A"
        lines.append(
            f"| {result.repo_path} | {branch} | {status_mark} {status_text} | {result.duration:>6.1f}s | {result.exit_code:>3} |"
        )
        total_duration += result.duration
        if result.status == "success":
            success_count += 1

    # Summary section
    lines.extend([
        "\n## Totals\n",
        f"- **Total Repositories:** {len(results)}",
        f"- **Successful:** {success_count}/{len(results)}",
        f"- **Total Duration:** {total_duration:.1f}s ({total_duration/60:.1f}m)",
    ])

    # Failures section
    failures = [r for r in results if r.status != "success"]
    if failures:
        lines.append("\n## Failures\n")
        for result in failures:
            lines.append(f"### {result.repo_path}\n")
            lines.append(f"**Status:** {result.status}")
            lines.append(f"**Exit Code:** {result.exit_code}")
            if result.error_message:
                lines.append(f"**Error:** {result.error_message}")
            lines.append("")

    return "\n".join(lines)


def main() -> int:
    """CLI entry point for claude-multi."""
    parser = argparse.ArgumentParser(
        description="Execute claude-iterative across multiple repositories in parallel."
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to YAML/JSON config file with 'task' and 'repos' list",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        default=True,
        help="Run in auto mode (no prompts) — default: True",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("MULTI_REPORT.md"),
        help="Output report file — default: MULTI_REPORT.md",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        help="Max parallel workers — default: 3",
    )

    args = parser.parse_args()

    try:
        config = parse_config(args.config)
        config.max_workers = args.workers

        print(f"📋 Task: {config.task}")
        print(f"🔀 Repositories: {len(config.repos)}")
        print(f"👷 Workers: {config.max_workers}\n")

        results = run_parallel(config, auto=args.auto)

        report = generate_report(results)
        args.output.write_text(report)

        print(f"\n✅ Report written to {args.output}")

        # Return 0 if all succeeded, 1 otherwise
        success_count = sum(1 for r in results if r.status == "success")
        return 0 if success_count == len(results) else 1

    except (FileNotFoundError, ValueError) as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"❌ Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
