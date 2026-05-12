"""Tests para agent_cli."""
from __future__ import annotations

import json
import sys
from unittest.mock import Mock, patch

import pytest

from claude_workflow import agent_cli
from claude_workflow.cli_backend import CLIResult, reset_default_backend, set_default_backend


# ─────────────────────────────────────────────
# _build_flags — función pura, sin mocks
# ─────────────────────────────────────────────

class TestBuildFlags:
    def test_ask_mode_max_turns_5(self):
        flags = agent_cli._build_flags("ask", force=False)
        idx = flags.index("--max-turns")
        assert flags[idx + 1] == "5"

    def test_ask_mode_no_skip_permissions(self):
        assert "--dangerously-skip-permissions" not in agent_cli._build_flags("ask", force=False)

    def test_code_mode_max_turns_20(self):
        flags = agent_cli._build_flags("code", force=False)
        idx = flags.index("--max-turns")
        assert flags[idx + 1] == "20"

    def test_code_mode_includes_skip_permissions(self):
        assert "--dangerously-skip-permissions" in agent_cli._build_flags("code", force=False)

    def test_force_adds_skip_permissions_to_ask(self):
        assert "--dangerously-skip-permissions" in agent_cli._build_flags("ask", force=True)

    def test_force_no_duplicate_skip_permissions_in_code(self):
        flags = agent_cli._build_flags("code", force=True)
        assert flags.count("--dangerously-skip-permissions") == 1


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _make_backend(exit_code=0, text="ok", session_id=None, usage=None):
    mock = Mock()
    mock.execute.return_value = CLIResult(
        exit_code=exit_code,
        text=text,
        session_id=session_id,
        usage=usage or {},
        backend="claude",
    )
    return mock


def _run_main(argv, backend):
    set_default_backend(backend)
    with pytest.raises(SystemExit) as exc_info:
        with patch("sys.argv", argv):
            agent_cli.main()
    return exc_info.value.code


# ─────────────────────────────────────────────
# Salida en texto
# ─────────────────────────────────────────────

class TestMainTextOutput:
    def setup_method(self):
        reset_default_backend()

    def teardown_method(self):
        reset_default_backend()

    def test_imprime_result_text(self, capsys):
        backend = _make_backend(text="Repo de Python para workflows.")
        _run_main(["agent", "-p", "Resume este repo.", "--output-format", "text"], backend)
        assert "Repo de Python para workflows." in capsys.readouterr().out

    def test_exit_code_propagado(self):
        backend = _make_backend(exit_code=2)
        code = _run_main(["agent", "-p", "test"], backend)
        assert code == 2

    def test_default_output_format_es_text(self, capsys):
        backend = _make_backend(text="respuesta")
        _run_main(["agent", "-p", "test"], backend)
        out = capsys.readouterr().out
        assert "respuesta" in out
        # no debe ser JSON
        with pytest.raises(Exception):
            json.loads(out)


# ─────────────────────────────────────────────
# Salida en JSON
# ─────────────────────────────────────────────

class TestMainJsonOutput:
    def setup_method(self):
        reset_default_backend()

    def teardown_method(self):
        reset_default_backend()

    def test_estructura_json_correcta(self, capsys):
        backend = _make_backend(text="OK", session_id="sess-abc", usage={"input": 10, "output": 5})
        _run_main(["agent", "-p", "test", "--output-format", "json"], backend)
        data = json.loads(capsys.readouterr().out)
        assert data["result"] == "OK"
        assert data["exit_code"] == 0
        assert data["session_id"] == "sess-abc"
        assert data["usage"]["input"] == 10

    def test_exit_code_en_payload_y_proceso(self, capsys):
        backend = _make_backend(exit_code=1, text="error")
        code = _run_main(["agent", "-p", "test", "--output-format", "json"], backend)
        assert code == 1
        assert json.loads(capsys.readouterr().out)["exit_code"] == 1

    def test_session_id_null_cuando_none(self, capsys):
        backend = _make_backend(session_id=None)
        _run_main(["agent", "-p", "test", "--output-format", "json"], backend)
        assert json.loads(capsys.readouterr().out)["session_id"] is None


# ─────────────────────────────────────────────
# Flags enviados al backend
# ─────────────────────────────────────────────

class TestMainFlagMapping:
    def setup_method(self):
        reset_default_backend()

    def teardown_method(self):
        reset_default_backend()

    def _get_flags(self, backend):
        call_args = backend.execute.call_args
        return call_args[1].get("flags") or call_args[0][1]

    def test_ask_mode_max_turns_5(self):
        backend = _make_backend()
        _run_main(["agent", "-p", "test", "--mode", "ask"], backend)
        flags = self._get_flags(backend)
        assert flags[flags.index("--max-turns") + 1] == "5"

    def test_code_mode_skip_permissions(self):
        backend = _make_backend()
        _run_main(["agent", "-p", "test", "--mode", "code"], backend)
        assert "--dangerously-skip-permissions" in self._get_flags(backend)

    def test_force_agrega_skip_permissions(self):
        backend = _make_backend()
        _run_main(["agent", "-p", "test", "--mode", "ask", "--force"], backend)
        assert "--dangerously-skip-permissions" in self._get_flags(backend)

    def test_step_es_agent_cli(self):
        backend = _make_backend()
        _run_main(["agent", "-p", "test"], backend)
        call_kwargs = backend.execute.call_args[1]
        assert call_kwargs.get("step") == "agent-cli"

    def test_output_format_no_se_reenvía_al_backend(self):
        backend = _make_backend()
        _run_main(["agent", "-p", "test", "--output-format", "json"], backend)
        flags = self._get_flags(backend)
        assert "--output-format" not in flags


# ─────────────────────────────────────────────
# Manejo de errores del backend
# ─────────────────────────────────────────────

class TestMainErrorHandling:
    def setup_method(self):
        reset_default_backend()

    def teardown_method(self):
        reset_default_backend()

    def test_excepcion_en_text_mode_stderr(self, capsys):
        backend = Mock()
        backend.execute.side_effect = RuntimeError("subprocess not found")
        set_default_backend(backend)
        code = _run_main(["agent", "-p", "test", "--output-format", "text"], backend)
        assert code == 1
        assert "subprocess not found" in capsys.readouterr().err

    def test_excepcion_en_json_mode_salida_json(self, capsys):
        backend = Mock()
        backend.execute.side_effect = RuntimeError("API key missing")
        set_default_backend(backend)
        code = _run_main(["agent", "-p", "test", "--output-format", "json"], backend)
        assert code == 1
        data = json.loads(capsys.readouterr().out)
        assert data["exit_code"] == 1
        assert "API key missing" in data["result"]
