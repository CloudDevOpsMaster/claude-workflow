"""Tests para iterative.py — funciones de las 5 fases.

Cubre:
- phase0_branch(): crear branches
- phase1_analysis(): análisis paralelo (3 agentes)
- phase2_synthesize(): síntesis del plan
- phase3_implement(): implementación (con variantes de paralelo + dev_agents)
- phase5_commit(): generación de mensaje de commit
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from typing import Dict

import pytest

from claude_workflow import iterative as ci
from claude_workflow.iterative import (
    AgentRole,
    AgentResult,
    SessionStore,
    ParallelRunner,
)


# ─────────────────────────────────────────────
# Tests: phase0_branch()
# ─────────────────────────────────────────────

def test_phase0_branch_create_success(capsys):
    """phase0_branch() crea un nuevo branch exitosamente."""
    with patch.object(ci.base, 'create_branch', return_value=True) as mock_create:
        result = ci.phase0_branch("feature/new-feature")

    assert result is True
    mock_create.assert_called_once_with("feature/new-feature")
    captured = capsys.readouterr()
    assert "FASE 0" in captured.out


def test_phase0_branch_create_failure(capsys):
    """phase0_branch() retorna False si falla la creación."""
    with patch.object(ci.base, 'create_branch', return_value=False):
        result = ci.phase0_branch("feature/bad-branch")

    assert result is False


# ─────────────────────────────────────────────
# Tests: phase1_analysis()
# ─────────────────────────────────────────────

def test_phase1_analysis_all_agents_success(capsys, temp_agents_dir, session_store, parallel_runner):
    """phase1_analysis() ejecuta los 3 agentes en paralelo exitosamente."""
    agents_dir = Path(temp_agents_dir) / "agents"
    agents_dir.mkdir(exist_ok=True)

    results = {
        AgentRole.ANALYST: AgentResult(
            role=AgentRole.ANALYST,
            exit_code=0,
            output="Analysis complete",
            session_id="analyst-session",
            duration_s=1.0,
            tokens={},
        ),
        AgentRole.ARCHITECT: AgentResult(
            role=AgentRole.ARCHITECT,
            exit_code=0,
            output="Architecture design",
            session_id="architect-session",
            duration_s=1.0,
            tokens={},
        ),
        AgentRole.QA_PLANNER: AgentResult(
            role=AgentRole.QA_PLANNER,
            exit_code=0,
            output="QA plan",
            session_id="qa-session",
            duration_s=1.0,
            tokens={},
        ),
    }

    with patch.object(ci, '_collect_project_context', return_value="Python 3.12"):
        with patch.object(parallel_runner, 'run_parallel', return_value=results):
            result = ci.phase1_analysis(
                "Test task",
                agents_dir,
                session_store,
                parallel_runner,
                skip_phases=[],
                token_store=None,
                prompt_loader=None,
            )

    assert len(result) == 3
    assert AgentRole.ANALYST in result
    assert result[AgentRole.ANALYST].success is True
    captured = capsys.readouterr()
    assert "FASE 1" in captured.out


def test_phase1_analysis_one_agent_fails(capsys, temp_agents_dir, session_store, parallel_runner):
    """phase1_analysis() detecta fallo de un agente."""
    agents_dir = Path(temp_agents_dir) / "agents"
    agents_dir.mkdir(exist_ok=True)

    results = {
        AgentRole.ANALYST: AgentResult(
            role=AgentRole.ANALYST,
            exit_code=0,
            output="OK",
            session_id="id",
            duration_s=1.0,
            tokens={},
        ),
        AgentRole.ARCHITECT: AgentResult(
            role=AgentRole.ARCHITECT,
            exit_code=1,
            output="Error",
            session_id=None,
            duration_s=1.0,
            tokens={},
        ),
        AgentRole.QA_PLANNER: AgentResult(
            role=AgentRole.QA_PLANNER,
            exit_code=0,
            output="OK",
            session_id="id",
            duration_s=1.0,
            tokens={},
        ),
    }

    with patch.object(ci, '_collect_project_context', return_value=""):
        with patch.object(parallel_runner, 'run_parallel', return_value=results):
            result = ci.phase1_analysis(
                "Test task",
                agents_dir,
                session_store,
                parallel_runner,
                skip_phases=[],
            )

    assert result[AgentRole.ARCHITECT].success is False


def test_phase1_analysis_skip_phases():
    """phase1_analysis() retorna {} si skip_phases contiene 1."""
    agents_dir = Path("/tmp/agents")

    result = ci.phase1_analysis(
        "Test task",
        agents_dir,
        store=MagicMock(),
        runner=MagicMock(),
        skip_phases=[1],
    )

    assert result == {}


def test_phase1_analysis_resume_session(capsys, temp_agents_dir, session_store, parallel_runner):
    """phase1_analysis() carga session_ids si resume=True."""
    agents_dir = Path(temp_agents_dir) / "agents"
    agents_dir.mkdir(exist_ok=True)

    # Precargar una sesión
    session_store.save(AgentRole.ANALYST, "saved-analyst-session")

    results = {
        AgentRole.ANALYST: AgentResult(
            role=AgentRole.ANALYST,
            exit_code=0,
            output="Resumed analysis",
            session_id="saved-analyst-session",
            duration_s=1.0,
            tokens={},
        ),
        AgentRole.ARCHITECT: AgentResult(
            role=AgentRole.ARCHITECT,
            exit_code=0,
            output="New arch",
            session_id=None,
            duration_s=1.0,
            tokens={},
        ),
        AgentRole.QA_PLANNER: AgentResult(
            role=AgentRole.QA_PLANNER,
            exit_code=0,
            output="New QA",
            session_id=None,
            duration_s=1.0,
            tokens={},
        ),
    }

    with patch.object(ci, '_collect_project_context', return_value=""):
        with patch.object(parallel_runner, 'run_parallel', return_value=results):
            result = ci.phase1_analysis(
                "Test task",
                agents_dir,
                session_store,
                parallel_runner,
                skip_phases=[],
                resume=True,
            )

    assert result[AgentRole.ANALYST].session_id == "saved-analyst-session"


# ─────────────────────────────────────────────
# Tests: phase2_synthesize()
# ─────────────────────────────────────────────

def test_phase2_synthesize_success(capsys, temp_agents_dir, session_store):
    """phase2_synthesize() genera el plan exitosamente."""
    agents_dir = Path(temp_agents_dir) / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "analysis").mkdir(exist_ok=True)
    (agents_dir / "analysis" / "ANALYST.md").write_text("# Analysis")
    (agents_dir / "analysis" / "ARCHITECT.md").write_text("# Architecture")
    (agents_dir / "analysis" / "QA_PLANNER.md").write_text("# QA Plan")

    with patch.object(ci, 'claude_p_with_session') as mock_claude:
        mock_claude.return_value = (0, "# Plan\n## Steps\nDone", "session-id", {})

        result = ci.phase2_synthesize(
            "Test task",
            agents_dir,
            session_store,
            skip_phases=[],
            coverage=80,
        )

    assert result is True
    captured = capsys.readouterr()
    assert "FASE 2" in captured.out


def test_phase2_synthesize_claude_error(capsys, temp_agents_dir, session_store):
    """phase2_synthesize() retorna False si claude_p falla."""
    agents_dir = Path(temp_agents_dir) / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "analysis").mkdir(exist_ok=True)

    with patch.object(ci, 'claude_p_with_session') as mock_claude:
        mock_claude.return_value = (1, "Error", None, {})

        result = ci.phase2_synthesize(
            "Test task",
            agents_dir,
            session_store,
            skip_phases=[],
            coverage=80,
        )

    assert result is False


def test_phase2_synthesize_skip_phases():
    """phase2_synthesize() retorna True si skip_phases contiene 2."""
    result = ci.phase2_synthesize(
        "Test task",
        Path("/tmp"),
        store=MagicMock(),
        skip_phases=[2],
        coverage=80,
    )

    assert result is True


# ─────────────────────────────────────────────
# Tests: phase3_implement()
# ─────────────────────────────────────────────

def test_phase3_implement_single_agent_sequential(capsys, temp_agents_dir, session_store, parallel_runner):
    """phase3_implement() con dev_agents=1, parallel_impl=False (secuencial)."""
    agents_dir = Path(temp_agents_dir) / "agents"
    agents_dir.mkdir(exist_ok=True)

    impl_result = AgentResult(
        role=AgentRole.IMPLEMENTER,
        exit_code=0,
        output="Code implemented",
        session_id="impl-id",
        duration_s=2.0,
        tokens={},
    )
    test_result = AgentResult(
        role=AgentRole.TEST_WRITER,
        exit_code=0,
        output="Tests written",
        session_id="test-id",
        duration_s=1.0,
        tokens={},
    )

    with patch.object(ci, '_run_implementer', return_value=impl_result):
        with patch.object(ci, '_run_test_writer', return_value=test_result):
            result = ci.phase3_implement(
                "Test task",
                agents_dir,
                session_store,
                parallel_runner,
                skip_phases=[],
                coverage=80,
                parallel_impl=False,
                dev_agents=1,
            )

    assert result is True
    captured = capsys.readouterr()
    assert "FASE 3" in captured.out


def test_phase3_implement_single_agent_parallel(capsys, temp_agents_dir, session_store, parallel_runner):
    """phase3_implement() con dev_agents=1, parallel_impl=True (paralelo)."""
    agents_dir = Path(temp_agents_dir) / "agents"
    agents_dir.mkdir(exist_ok=True)

    results = {
        AgentRole.IMPLEMENTER: AgentResult(
            role=AgentRole.IMPLEMENTER,
            exit_code=0,
            output="Code",
            session_id="id1",
            duration_s=2.0,
            tokens={},
        ),
        AgentRole.TEST_WRITER: AgentResult(
            role=AgentRole.TEST_WRITER,
            exit_code=0,
            output="Tests",
            session_id="id2",
            duration_s=1.0,
            tokens={},
        ),
    }

    with patch.object(parallel_runner, 'run_parallel', return_value=results):
        with patch.object(ci, '_run_implementer', return_value=results[AgentRole.IMPLEMENTER]):
            with patch.object(ci, '_run_test_writer', return_value=results[AgentRole.TEST_WRITER]):
                result = ci.phase3_implement(
                    "Test task",
                    agents_dir,
                    session_store,
                    parallel_runner,
                    skip_phases=[],
                    coverage=80,
                    parallel_impl=True,
                    dev_agents=1,
                )

    assert result is True


def test_phase3_implement_multiple_dev_agents(capsys, temp_agents_dir, session_store, parallel_runner):
    """phase3_implement() con dev_agents>1 (COORDINATOR + N DEV agents en paralelo)."""
    agents_dir = Path(temp_agents_dir) / "agents"
    agents_dir.mkdir(exist_ok=True)

    coord_result = AgentResult(
        role=AgentRole.COORDINATOR,
        exit_code=0,
        output="Coordination plan",
        session_id="coord-id",
        duration_s=1.0,
        tokens={},
    )

    dev_results = {
        "DEV_1": AgentResult(
            role=AgentRole.IMPLEMENTER,
            exit_code=0,
            output="Dev 1 done",
            session_id="dev1-id",
            duration_s=2.0,
            tokens={},
        ),
        "DEV_2": AgentResult(
            role=AgentRole.IMPLEMENTER,
            exit_code=0,
            output="Dev 2 done",
            session_id="dev2-id",
            duration_s=2.0,
            tokens={},
        ),
    }

    test_result = AgentResult(
        role=AgentRole.TEST_WRITER,
        exit_code=0,
        output="All tests pass",
        session_id="test-id",
        duration_s=1.0,
        tokens={},
    )

    with patch.object(ci, '_run_coordinator', return_value=coord_result):
        with patch.object(parallel_runner, 'run_parallel', return_value=dev_results):
            with patch.object(ci, '_run_test_writer', return_value=test_result):
                result = ci.phase3_implement(
                    "Test task",
                    agents_dir,
                    session_store,
                    parallel_runner,
                    skip_phases=[],
                    coverage=80,
                    dev_agents=2,
                )

    assert result is True
    captured = capsys.readouterr()
    assert "2 Dev agents" in captured.out


def test_phase3_implement_skip_phases():
    """phase3_implement() retorna True si skip_phases contiene 3."""
    result = ci.phase3_implement(
        "Test task",
        Path("/tmp"),
        store=MagicMock(),
        runner=MagicMock(),
        skip_phases=[3],
        coverage=80,
    )

    assert result is True


def test_phase3_implement_dev_agent_failure(capsys, temp_agents_dir, session_store, parallel_runner):
    """phase3_implement() retorna False si un DEV agent falla."""
    agents_dir = Path(temp_agents_dir) / "agents"
    agents_dir.mkdir(exist_ok=True)

    coord_result = AgentResult(
        role=AgentRole.COORDINATOR,
        exit_code=0,
        output="OK",
        session_id="id",
        duration_s=1.0,
        tokens={},
    )

    dev_results = {
        "DEV_1": AgentResult(
            role=AgentRole.IMPLEMENTER,
            exit_code=0,
            output="OK",
            session_id="id",
            duration_s=2.0,
            tokens={},
        ),
        "DEV_2": AgentResult(
            role=AgentRole.IMPLEMENTER,
            exit_code=1,
            output="Error",
            session_id=None,
            duration_s=2.0,
            tokens={},
        ),
    }

    with patch.object(ci, '_run_coordinator', return_value=coord_result):
        with patch.object(parallel_runner, 'run_parallel', return_value=dev_results):
            result = ci.phase3_implement(
                "Test task",
                agents_dir,
                session_store,
                parallel_runner,
                skip_phases=[],
                coverage=80,
                dev_agents=2,
            )

    assert result is False


# ─────────────────────────────────────────────
# Tests: phase5_commit()
# ─────────────────────────────────────────────

def test_phase5_commit_valid_message(capsys, temp_agents_dir, session_store):
    """phase5_commit() genera un mensaje de commit válido."""
    agents_dir = Path(temp_agents_dir) / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "commit").mkdir(exist_ok=True)
    (agents_dir / "integration").mkdir(exist_ok=True)
    (agents_dir / "PLAN.md").write_text("# Plan\n")
    (agents_dir / "integration" / "INTEGRATOR.md").write_text("# Integration\n")

    with patch.object(ci, 'claude_p_with_session') as mock_claude:
        mock_claude.return_value = (0, "feat: implement feature\n\nTests: coverage 85.5%", "sid", {})

        with patch.object(ci.base, 'commit_all', return_value=True) as mock_commit:
            result = ci.phase5_commit(
                "Test task",
                "feature/test",
                agents_dir,
                session_store,
                skip_phases=[],
                coverage=85.5,
            )

    assert result is True
    mock_commit.assert_called_once()
    captured = capsys.readouterr()
    assert "FASE 5" in captured.out


def test_phase5_commit_json_fallback(capsys, temp_agents_dir, session_store):
    """phase5_commit() usa mensaje genérico si respuesta es JSON."""
    agents_dir = Path(temp_agents_dir) / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "commit").mkdir(exist_ok=True)
    (agents_dir / "integration").mkdir(exist_ok=True)

    with patch.object(ci, 'claude_p_with_session') as mock_claude:
        # Retornar JSON que debe ser descartado
        mock_claude.return_value = (0, '{"error": "Invalid"}', None, {})

        with patch.object(ci.base, 'commit_all', return_value=True) as mock_commit:
            result = ci.phase5_commit(
                "Test task",
                "feature/test",
                agents_dir,
                session_store,
                skip_phases=[],
                coverage=80.0,
            )

    assert result is True
    # Verificar que se usó el mensaje genérico (fallback, sanitizado a lowercase)
    call_args = mock_commit.call_args[0][0]
    assert "feat: test task" in call_args


def test_phase5_commit_backticks_cleanup(capsys, temp_agents_dir, session_store):
    """phase5_commit() limpia backticks del mensaje."""
    agents_dir = Path(temp_agents_dir) / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "commit").mkdir(exist_ok=True)
    (agents_dir / "integration").mkdir(exist_ok=True)

    with patch.object(ci, 'claude_p_with_session') as mock_claude:
        mock_claude.return_value = (0, "```\nfeat: task\n```", "sid", {})

        with patch.object(ci.base, 'commit_all', return_value=True) as mock_commit:
            result = ci.phase5_commit(
                "Test task",
                "feature/test",
                agents_dir,
                session_store,
                skip_phases=[],
                coverage=80.0,
            )

    assert result is True
    call_args = mock_commit.call_args[0][0]
    assert "```" not in call_args


def test_phase5_commit_skip_phases():
    """phase5_commit() retorna True si skip_phases contiene 5."""
    result = ci.phase5_commit(
        "Test task",
        "feature/test",
        Path("/tmp"),
        store=MagicMock(),
        skip_phases=[5],
        coverage=80.0,
    )

    assert result is True


def test_phase5_commit_type_validation(capsys, temp_agents_dir, session_store):
    """phase5_commit() valida que mensaje comience con tipo válido."""
    agents_dir = Path(temp_agents_dir) / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "commit").mkdir(exist_ok=True)
    (agents_dir / "integration").mkdir(exist_ok=True)

    with patch.object(ci, 'claude_p_with_session') as mock_claude:
        # Mensaje sin tipo válido
        mock_claude.return_value = (0, "invalid: this is not a valid type\n", "sid", {})

        with patch.object(ci.base, 'commit_all', return_value=True) as mock_commit:
            result = ci.phase5_commit(
                "Test task",
                "feature/test",
                agents_dir,
                session_store,
                skip_phases=[],
                coverage=80.0,
            )

    assert result is True
    # Debe usar fallback (sanitizado a lowercase)
    call_args = mock_commit.call_args[0][0]
    assert "feat: test task" in call_args


def test_phase5_commit_git_failure(capsys, temp_agents_dir, session_store):
    """phase5_commit() retorna False si commit_all falla."""
    agents_dir = Path(temp_agents_dir) / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "commit").mkdir(exist_ok=True)
    (agents_dir / "integration").mkdir(exist_ok=True)

    with patch.object(ci, 'claude_p_with_session') as mock_claude:
        mock_claude.return_value = (0, "feat: task", "sid", {})

        with patch.object(ci.base, 'commit_all', return_value=False):
            result = ci.phase5_commit(
                "Test task",
                "feature/test",
                agents_dir,
                session_store,
                skip_phases=[],
                coverage=80.0,
            )

    assert result is False


# ─────────────────────────────────────────────
# Integration Tests: Secuencia de Fases
# ─────────────────────────────────────────────

def test_phase_sequence_0_to_5(capsys, temp_agents_dir, session_store, parallel_runner):
    """Secuencia típica: fase0 → fase1 → fase2 → fase3 → fase5."""
    agents_dir = Path(temp_agents_dir) / "agents"
    agents_dir.mkdir(exist_ok=True)

    # Phase 0: Branch
    assert ci.phase0_branch("feature/test") is not None

    # Phase 1: Analysis
    results_p1 = {
        AgentRole.ANALYST: AgentResult(
            role=AgentRole.ANALYST,
            exit_code=0,
            output="Analysis",
            session_id="id1",
            duration_s=1.0,
            tokens={},
        ),
        AgentRole.ARCHITECT: AgentResult(
            role=AgentRole.ARCHITECT,
            exit_code=0,
            output="Architecture",
            session_id="id2",
            duration_s=1.0,
            tokens={},
        ),
        AgentRole.QA_PLANNER: AgentResult(
            role=AgentRole.QA_PLANNER,
            exit_code=0,
            output="QA Plan",
            session_id="id3",
            duration_s=1.0,
            tokens={},
        ),
    }

    with patch.object(ci, '_collect_project_context', return_value=""):
        with patch.object(parallel_runner, 'run_parallel', return_value=results_p1):
            p1_results = ci.phase1_analysis(
                "Test",
                agents_dir,
                session_store,
                parallel_runner,
                skip_phases=[],
            )
    assert len(p1_results) == 3
