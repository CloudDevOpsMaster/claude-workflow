"""
Fixtures compartidas para todos los tests de claude_workflow.
Proporciona mocks para subprocess, Claude CLI, y estructuras de directorio.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
from typing import Dict, Any, Optional, Tuple

import pytest

from claude_workflow.iterative import (
    AgentRole,
    AgentResult,
    SessionStore,
    TokenStore,
    ParallelRunner,
    PromptLoader,
)


# ─────────────────────────────────────────────────────────────────────────
# Fixtures: Estructuras de Directorio
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def temp_agents_dir(tmp_path: Path) -> Path:
    """
    Crea estructura temporal agents/{role}/ROLE.md con directorios para
    todos los roles definidos en AgentRole enum.

    Retorna: Path al directorio raíz temporal (contiene subdirs agents/)
    """
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    # Crear subdirectorio para cada rol
    for role in AgentRole:
        role_dir = agents_dir / role.value.lower()
        role_dir.mkdir(parents=True, exist_ok=True)

        # Crear archivo ROLE.md con contenido mínimo
        role_file = role_dir / f"{role.value}.md"
        role_file.write_text(f"# {role.value}\n\nRole placeholder for testing.\n")

    return tmp_path


@pytest.fixture
def test_project_structure(tmp_path: Path) -> Path:
    """
    Simula estructura de un proyecto Python con:
    - pyproject.toml
    - tests/conftest.py
    - src/ con archivos Python

    Retorna: Path al directorio raíz del proyecto
    """
    # pyproject.toml
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("""
[project]
name = "test-project"
version = "0.1.0"
requires-python = ">=3.10"
""")

    # tests/conftest.py
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    conftest = tests_dir / "conftest.py"
    conftest.write_text("import pytest\n")

    # src/ con archivos Python
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "__init__.py").write_text("")
    (src_dir / "main.py").write_text("def main(): pass\n")

    return tmp_path


# ─────────────────────────────────────────────────────────────────────────
# Fixtures: Stores (SessionStore, TokenStore)
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def session_store(tmp_path: Path) -> SessionStore:
    """Crea un SessionStore temporal para tests."""
    sessions_file = tmp_path / "sessions.json"
    return SessionStore(sessions_file)


@pytest.fixture
def token_store(tmp_path: Path) -> TokenStore:
    """Crea un TokenStore temporal para tests."""
    tokens_file = tmp_path / "tokens.json"
    return TokenStore(tokens_file)


# ─────────────────────────────────────────────────────────────────────────
# Fixtures: Mocks de Subprocess y Claude
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_subprocess() -> Mock:
    """
    Mock genérico de subprocess.run() que retorna CompletedProcess simulado.

    Default: returncode=0, stdout="", stderr=""
    """
    mock = Mock()
    mock.return_value = Mock(
        returncode=0,
        stdout="",
        stderr="",
    )
    return mock


@pytest.fixture
def mock_git(mock_subprocess: Mock) -> Mock:
    """
    Mock especializado para comandos git (git branch, git checkout, git commit, etc).

    Simula respuestas comunes:
    - git branch → retorna lista de ramas
    - git checkout → retorna éxito
    - git commit → retorna éxito
    """
    return mock_subprocess


@pytest.fixture
def mock_claude_p() -> Mock:
    """
    Mock de claude_p() que simula la respuesta de Claude CLI.

    Retorna tupla: (exit_code, output, session_id, usage_dict)
    Default: (0, "mocked output", "test-session-123", {"input": 100, "output": 50})
    """
    mock = Mock(
        return_value=(
            0,
            "mocked Claude output",
            "test-session-123",
            {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0},
        )
    )
    return mock


@pytest.fixture
def mock_claude_stream() -> Mock:
    """
    Mock de claude_stream() que simula streaming de Claude.

    Retorna tupla: (exit_code, full_output, session_id, usage_dict)
    """
    mock = Mock(
        return_value=(
            0,
            "streamed output from Claude",
            "test-session-456",
            {"input": 200, "output": 150, "cache_read": 0, "cache_write": 0},
        )
    )
    return mock


# ─────────────────────────────────────────────────────────────────────────
# Fixtures: Resultados Simulados
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def agent_result_success() -> AgentResult:
    """AgentResult exitoso (exit_code=0, sin error)."""
    return AgentResult(
        role=AgentRole.ANALYST,
        exit_code=0,
        output="Analysis completed successfully.",
        session_id="sess-analyst-001",
        duration_s=5.2,
        tokens={"input": 100, "output": 50, "cache_read": 0, "cache_write": 0},
    )


@pytest.fixture
def agent_result_failure() -> AgentResult:
    """AgentResult fallido (exit_code != 0, con error)."""
    return AgentResult(
        role=AgentRole.ANALYST,
        exit_code=1,
        output="",
        session_id=None,
        duration_s=0.5,
        tokens={},
        error="Analysis failed due to timeout",
    )


# ─────────────────────────────────────────────────────────────────────────
# Fixtures: ParallelRunner
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def parallel_runner() -> ParallelRunner:
    """Crea un ParallelRunner con configuración de test (workers=2, timeout=30s)."""
    return ParallelRunner(max_workers=2, timeout_s=30, max_retries=1)


# ─────────────────────────────────────────────────────────────────────────
# Fixtures: PromptLoader
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def prompt_loader(tmp_path: Path) -> PromptLoader:
    """Crea un PromptLoader apuntando a directorio temporal."""
    return PromptLoader(repo_root=tmp_path)


@pytest.fixture
def prompt_loader_with_custom_prompts(tmp_path: Path) -> Tuple[PromptLoader, Path]:
    """
    Crea un PromptLoader con prompts personalizados en .claude-workflow/prompts/

    Retorna: (PromptLoader, prompts_dir)
    """
    prompts_dir = tmp_path / ".claude-workflow" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    # Crear algunos prompts de prueba
    (prompts_dir / "ANALYST_PROMPT.md").write_text(
        "# ANALYST PROMPT\n\nAnalyze the task: {task}"
    )
    (prompts_dir / "ARCHITECT_PROMPT.md").write_text(
        "# ARCHITECT PROMPT\n\nDesign the solution: {task}"
    )

    loader = PromptLoader(repo_root=tmp_path)
    loader.load()

    return loader, prompts_dir


# ─────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────


def create_test_plan_file(path: Path, content: str) -> Path:
    """
    Crea un archivo PLAN.md en la ruta especificada con contenido.

    Args:
        path: Directorio donde crear PLAN.md
        content: Contenido del archivo

    Returns: Path al archivo creado
    """
    path.mkdir(parents=True, exist_ok=True)
    plan_file = path / "PLAN.md"
    plan_file.write_text(content)
    return plan_file


def create_test_project_structure(tmp_path: Path) -> Path:
    """
    Simula estructura completa de un proyecto con:
    - pyproject.toml
    - tests/conftest.py
    - src/ con archivos Python
    - agents/role/ROLE.md para cada rol

    Args:
        tmp_path: Directorio base temporal

    Returns: Path al directorio raíz del proyecto
    """
    # pyproject.toml
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("""
[project]
name = "test-project"
version = "0.1.0"
requires-python = ">=3.10"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
""")

    # tests/conftest.py
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "conftest.py").write_text("import pytest\n")

    # src/ con archivos Python
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "__init__.py").write_text("")
    (src_dir / "main.py").write_text("def main(): pass\n")

    # agents/ con roles
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for role in AgentRole:
        role_dir = agents_dir / role.value.lower()
        role_dir.mkdir(parents=True, exist_ok=True)
        (role_dir / f"{role.value}.md").write_text(f"# {role.value}\n")

    # PLAN.md
    (tmp_path / "PLAN.md").write_text("# Test Plan\n")

    return tmp_path


def create_mock_coverage_output(
    file_format: str = "pytest",
    coverage_percent: float = 85.5,
    total_lines: int = 100,
    covered_lines: int = 86,
) -> str:
    """
    Crea output simulado de pytest o jest con información de cobertura.

    Args:
        file_format: "pytest" o "jest"
        coverage_percent: Porcentaje de cobertura
        total_lines: Total de líneas
        covered_lines: Líneas cubiertas

    Returns: String con output simulado
    """
    if file_format == "pytest":
        return f"""
Name                                    Stmts   Miss  Cover
───────────────────────────────────────────────────────────
claude_workflow/__init__.py                 2      0   100%
claude_workflow/iterative.py              500     70    86%
claude_workflow/plan_exec.py              300     45    85%
───────────────────────────────────────────────────────────
TOTAL                                     802     115    86%
        """
    elif file_format == "jest":
        return f"""
-----------|----------|----------|----------|----------|
File       |  % Stmts | % Branch |  % Funcs |  % Lines |
-----------|----------|----------|----------|----------|
All files  |     85.5 |     80.0 |     82.5 |     85.5 |
-----------|----------|----------|----------|----------|
        """
    else:
        raise ValueError(f"Unsupported format: {file_format}")


# ─────────────────────────────────────────────────────────────────────────
# Fixtures: Helpers Exportados como Fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def create_plan_file():
    """Fixture que expone la función helper create_test_plan_file."""
    return create_test_plan_file


@pytest.fixture
def create_project_structure():
    """Fixture que expone la función helper create_test_project_structure."""
    return create_test_project_structure


@pytest.fixture
def coverage_output():
    """Fixture que expone la función helper create_mock_coverage_output."""
    return create_mock_coverage_output


# ─────────────────────────────────────────────────────────────────────────
# Auto-use Fixtures (se aplican a todos los tests automáticamente)
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_auto_mode():
    """
    Reset _AUTO_MODE a False después de cada test para evitar
    interferencias entre tests.
    """
    from claude_workflow import iterative

    original_auto_mode = iterative._AUTO_MODE
    yield
    iterative._AUTO_MODE = original_auto_mode
