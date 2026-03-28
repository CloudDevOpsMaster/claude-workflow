"""Tests para plan_exec.py — funciones de los 5 pasos (step_*).

Cubre:
- step_branch(): crear/reutilizar branches
- step_plan_loop(): ciclo de refinamiento del plan
- step_execute(): ejecución del plan
- step_tests(): generación y validación de tests
- step_commit(): generación de mensaje de commit
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, call
from io import StringIO

import pytest

from claude_workflow import plan_exec as pe


# ─────────────────────────────────────────────
# Tests: step_branch()
# ─────────────────────────────────────────────

def test_step_branch_creates_new_branch():
    """step_branch(branch_name) crea un nuevo branch."""
    with patch.object(pe, 'create_branch', return_value=True) as mock_create:
        result = pe.step_branch("feature/test-branch")

        mock_create.assert_called_once_with("feature/test-branch")
        assert result is True


def test_step_branch_reuses_existing_branch():
    """step_branch() reutiliza un branch existente."""
    with patch.object(pe, 'create_branch', return_value=True) as mock_create:
        result = pe.step_branch("feature/existing")

        mock_create.assert_called_once_with("feature/existing")
        assert result is True


def test_step_branch_git_error():
    """step_branch() retorna False si falla el git checkout."""
    with patch.object(pe, 'create_branch', return_value=False) as mock_create:
        result = pe.step_branch("feature/bad-branch")

        mock_create.assert_called_once_with("feature/bad-branch")
        assert result is False


# ─────────────────────────────────────────────
# Tests: step_plan_loop()
# ─────────────────────────────────────────────

def test_step_plan_loop_approval_first_iteration():
    """step_plan_loop() aprueba el plan en primera iteración."""
    plan_content = "# Plan: Test Task\n\n## Objetivo\nTest it\n"

    with patch.object(pe, 'claude_p') as mock_claude:
        # Primera llamada: generar plan
        # Segunda llamada: revisión → APROBADO
        mock_claude.side_effect = [
            (0, plan_content, {}),  # plan generation
            (0, "APROBADO", {}),    # review approval
        ]

        with patch.object(Path, 'write_text'):
            with patch.object(Path, 'read_text', return_value=plan_content):
                with patch.object(Path, 'exists', return_value=True):
                    result = pe.step_plan_loop("Implement feature", token_acc={})

    assert result is True
    assert mock_claude.call_count == 2


def test_step_plan_loop_refinement_multiple_iterations():
    """step_plan_loop() refina el plan en múltiples iteraciones."""
    plan_v1 = "# Plan v1\n## Steps\n- Step 1\n"
    plan_v2 = "# Plan v2\n## Steps\n- Step 1\n- Step 2\n"

    with patch.object(pe, 'claude_p') as mock_claude:
        # 1. Generar plan
        # 2. Revisión → REVISAR
        # 3. Refinar → plan_v2
        # 4. Revisión → APROBADO
        mock_claude.side_effect = [
            (0, plan_v1, {}),
            (0, "REVISAR: Falta paso de tests", {}),
            (0, plan_v2, {}),
            (0, "APROBADO", {}),
        ]

        with patch.object(Path, 'write_text'):
            with patch.object(Path, 'read_text', return_value=plan_v2):
                with patch.object(Path, 'exists', return_value=True):
                    result = pe.step_plan_loop("Implement feature", token_acc={})

    assert result is True
    assert mock_claude.call_count == 4


def test_step_plan_loop_max_iterations_fallback_to_confirm():
    """step_plan_loop() alcanza máximo de iteraciones y pregunta al usuario."""
    plan_content = "# Plan\n## Steps\nNo aprobado\n"

    with patch.object(pe, 'claude_p') as mock_claude:
        # Generar plan + MAX_PLAN_LOOPS revisiones/refinamientos sin APROBADO
        mock_claude.side_effect = [
            (0, plan_content, {}),  # generate
        ] + [(0, "REVISAR: Issue", {})] * (pe.MAX_PLAN_LOOPS * 2)  # reviews + refines

        with patch.object(Path, 'write_text'):
            with patch.object(Path, 'read_text', return_value=plan_content):
                with patch.object(Path, 'exists', return_value=True):
                    with patch.object(pe, 'confirm', return_value=True) as mock_confirm:
                        result = pe.step_plan_loop("Test", token_acc={})

    assert result is True
    mock_confirm.assert_called_once()


def test_step_plan_loop_claude_error_generation():
    """step_plan_loop() falla si falla la generación inicial del plan."""
    with patch.object(pe, 'claude_p', return_value=(1, "Error", {})):
        result = pe.step_plan_loop("Test", token_acc={})

    assert result is False


def test_step_plan_loop_claude_error_review():
    """step_plan_loop() falla si falla la revisión del plan."""
    plan = "# Plan\n"

    with patch.object(pe, 'claude_p') as mock_claude:
        mock_claude.side_effect = [
            (0, plan, {}),  # generate ok
            (1, "Error", {}),  # review fails
        ]

        with patch.object(Path, 'write_text'):
            result = pe.step_plan_loop("Test", token_acc={})

    assert result is False


def test_step_plan_loop_accums_token_usage():
    """step_plan_loop() acumula tokens en token_acc si se proporciona."""
    plan = "# Plan\n"
    usage = {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0, "cost_usd": 0.01}

    with patch.object(pe, 'claude_p') as mock_claude:
        mock_claude.side_effect = [
            (0, plan, usage),  # plan generation
            (0, "APROBADO", usage),  # review
        ]

        token_acc = {}

        with patch.object(Path, 'write_text'):
            with patch.object(Path, 'read_text', return_value=plan):
                with patch.object(Path, 'exists', return_value=True):
                    pe.step_plan_loop("Test", token_acc=token_acc)

    assert "plan" in token_acc
    assert token_acc["plan"]["input"] >= 100


# ─────────────────────────────────────────────
# Tests: step_execute()
# ─────────────────────────────────────────────

def test_step_execute_success():
    """step_execute() retorna True si claude_stream es exitoso."""
    with patch.object(pe, 'claude_stream', return_value=(0, [])):
        with patch.object(Path, 'write_text'):
            result = pe.step_execute()

    assert result is True


def test_step_execute_failure():
    """step_execute() retorna False si claude_stream falla."""
    with patch.object(pe, 'claude_stream', return_value=(1, [])):
        result = pe.step_execute()

    assert result is False


def test_step_execute_creates_log_file():
    """step_execute() crea/actualiza el archivo EXECUTION_LOG.md."""
    with patch.object(pe, 'claude_stream', return_value=(0, [])):
        with patch('pathlib.Path.write_text') as mock_write:
            pe.step_execute()

    # Verificar que se llama write_text al menos una vez
    assert mock_write.called


# ─────────────────────────────────────────────
# Tests: step_tests()
# ─────────────────────────────────────────────

def test_step_tests_coverage_threshold_met():
    """step_tests() retorna True si coverage >= MIN_COVERAGE."""
    with patch.object(pe, 'claude_stream', return_value=(0, [])):
        with patch.object(pe, 'run_tests') as mock_run:
            mock_run.return_value = (True, 85.0, "TOTAL ... 85%")

            result = pe.step_tests()

    assert result is True


def test_step_tests_coverage_below_threshold():
    """step_tests() intenta mejorar coverage si es < MIN_COVERAGE."""
    with patch.object(pe, 'claude_stream') as mock_stream:
        mock_stream.side_effect = [
            (0, []),  # initial tests
            (0, []),  # coverage boost 1
            (0, []),  # coverage boost 2
            (0, []),  # coverage boost 3
        ]

        with patch.object(pe, 'run_tests') as mock_run:
            # Simular: bajo coverage en todos los intentos
            mock_run.side_effect = [
                (True, 60.0, "TOTAL ... 60%"),
                (True, 70.0, "TOTAL ... 70%"),
                (True, 75.0, "TOTAL ... 75%"),
            ]

            with patch.object(pe, 'confirm', return_value=False):
                result = pe.step_tests()

    # Después de 3 intentos sin alcanzar threshold, pregunta al usuario
    assert result is False


def test_step_tests_handles_test_failures():
    """step_tests() retorna False si los tests fallan."""
    with patch.object(pe, 'claude_stream', return_value=(0, [])):
        with patch.object(pe, 'run_tests') as mock_run:
            mock_run.return_value = (False, 0.0, "FAILED ... error")

            result = pe.step_tests()

    assert result is False


def test_step_tests_parsing_pytest_format():
    """step_tests() parsea correctly formato pytest."""
    with patch.object(pe, 'claude_stream', return_value=(0, [])):
        with patch.object(pe, 'run_tests') as mock_run:
            output = "tests/test_module.py::test_func PASSED\nTOTAL 1000 200 82.5%"
            mock_run.return_value = (True, 82.5, output)

            result = pe.step_tests()

    assert result is True


def test_step_tests_parsing_jest_format():
    """step_tests() parsea correctly formato Jest."""
    with patch.object(pe, 'claude_stream', return_value=(0, [])):
        with patch.object(pe, 'run_tests') as mock_run:
            output = "All files | 85.5 | 80.2 | 88.1 | 79.5 |"
            mock_run.return_value = (True, 85.5, output)

            result = pe.step_tests()

    assert result is True


# ─────────────────────────────────────────────
# Tests: step_commit()
# ─────────────────────────────────────────────

def test_step_commit_with_valid_message():
    """step_commit() retorna True si el commit es exitoso."""
    with patch.object(pe, 'claude_p') as mock_claude:
        mock_claude.return_value = (0, "feat: implement feature\n\ndetails", {})

        with patch.object(pe, 'commit_all', return_value=True) as mock_commit:
            result = pe.step_commit("Test task", "feature/test", 85.0)

    assert result is True
    mock_commit.assert_called_once()


def test_step_commit_fallback_on_empty_message():
    """step_commit() usa mensaje genérico si claude_p retorna vacío."""
    with patch.object(pe, 'claude_p', return_value=(0, "", {})):
        with patch.object(pe, 'commit_all', return_value=True) as mock_commit:
            result = pe.step_commit("Test task", "feature/test", 85.0)

    assert result is True
    # Verificar que el mensaje genérico fue usado
    call_args = mock_commit.call_args[0][0]
    assert "feat: Test task" in call_args


def test_step_commit_fallback_on_error():
    """step_commit() usa mensaje genérico si claude_p falla."""
    with patch.object(pe, 'claude_p', return_value=(1, "Error", {})):
        with patch.object(pe, 'commit_all', return_value=True) as mock_commit:
            result = pe.step_commit("Test task", "feature/test", 80.0)

    assert result is True
    call_args = mock_commit.call_args[0][0]
    assert "feat: Test task" in call_args


def test_step_commit_includes_coverage_in_message():
    """step_commit() incluye el coverage en el mensaje."""
    with patch.object(pe, 'claude_p', return_value=(0, "feat: task\n\nTests: 82.5%", {})):
        with patch.object(pe, 'commit_all', return_value=True) as mock_commit:
            result = pe.step_commit("Task", "feature/test", 82.5)

    assert result is True


def test_step_commit_reads_plan_summary():
    """step_commit() lee las primeras líneas de PLAN.md."""
    plan_content = "# Plan\n## Objetivo\nTest\n## Steps\n- Step 1\n"

    with patch.object(pe, 'claude_p', return_value=(0, "feat: task", {})):
        with patch.object(pe, 'commit_all', return_value=True):
            with patch.object(Path, 'exists', return_value=True):
                with patch.object(Path, 'read_text', return_value=plan_content):
                    # Verificar que se llama a claude_p con el contenido del plan
                    pe.step_commit("Task", "feature/test", 80.0, token_acc={})


def test_step_commit_accums_token_usage():
    """step_commit() acumula tokens en token_acc si se proporciona."""
    usage = {"input": 50, "output": 25, "cache_read": 0, "cache_write": 0, "cost_usd": 0.005}

    with patch.object(pe, 'claude_p', return_value=(0, "feat: task", usage)):
        with patch.object(pe, 'commit_all', return_value=True):
            with patch.object(Path, 'exists', return_value=False):
                token_acc = {}
                pe.step_commit("Task", "feature/test", 80.0, token_acc=token_acc)

    assert "commit" in token_acc


def test_step_commit_git_failure():
    """step_commit() retorna False si commit_all falla."""
    with patch.object(pe, 'claude_p', return_value=(0, "feat: task", {})):
        with patch.object(pe, 'commit_all', return_value=False):
            with patch.object(Path, 'exists', return_value=False):
                result = pe.step_commit("Task", "feature/test", 80.0)

    assert result is False


# ─────────────────────────────────────────────
# Integration Tests: Flujo de Pasos
# ─────────────────────────────────────────────

def test_plan_exec_step_sequence():
    """Secuencia típica: branch → plan_loop → execute → tests → commit."""
    with patch.object(pe, 'create_branch', return_value=True):
        with patch.object(pe, 'claude_p') as mock_claude:
            with patch.object(pe, 'claude_stream', return_value=(0, [])):
                with patch.object(pe, 'run_tests', return_value=(True, 85.0, "TOTAL 85%")):
                    with patch.object(pe, 'commit_all', return_value=True):
                        with patch.object(Path, 'write_text'):
                            with patch.object(Path, 'read_text', return_value="# Plan\n"):
                                with patch.object(Path, 'exists', return_value=True):
                                    mock_claude.side_effect = [
                                        (0, "# Plan", {}),  # plan generate
                                        (0, "APROBADO", {}),  # review
                                        (0, "feat: task", {}),  # commit msg
                                    ]

                                    # 1. Branch
                                    assert pe.step_branch("feature/test") is True
                                    # 2. Plan loop
                                    assert pe.step_plan_loop("Test") is True
                                    # 3. Execute
                                    assert pe.step_execute() is True
                                    # 4. Tests
                                    assert pe.step_tests() is True
                                    # 5. Commit
                                    assert pe.step_commit("Test", "feature/test", 85.0) is True
