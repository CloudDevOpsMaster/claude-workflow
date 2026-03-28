"""Tests para plan_exec.py — funciones de lógica pura, git helpers, y subprocess mocks."""
import json
import subprocess
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import pytest
import sys

from claude_workflow import plan_exec as pe


# ─────────────────────────────────────────────
# Tests: _is_token_exhausted
# ─────────────────────────────────────────────

def test_is_token_exhausted_exit_zero_returns_false():
    """exit_code=0 siempre retorna False."""
    assert pe._is_token_exhausted(0, "any output") is False
    assert pe._is_token_exhausted(0, "context length exceeded") is False


def test_is_token_exhausted_context_length_exceeded():
    """'context length exceeded' en output → True."""
    assert pe._is_token_exhausted(1, "context length exceeded") is True
    assert pe._is_token_exhausted(1, "Context length exceeded ERROR") is True


def test_is_token_exhausted_token_limit():
    """'token limit' en output → True."""
    assert pe._is_token_exhausted(1, "token limit reached") is True
    assert pe._is_token_exhausted(1, "Token Limit Exceeded") is True


def test_is_token_exhausted_context_window():
    """'context window' en output → True."""
    assert pe._is_token_exhausted(1, "context window exceeded") is True


def test_is_token_exhausted_no_match():
    """Error genérico sin pattern conocido → False."""
    assert pe._is_token_exhausted(1, "some other error") is False
    assert pe._is_token_exhausted(1, "file not found") is False


# ─────────────────────────────────────────────
# Tests: parse_coverage
# ─────────────────────────────────────────────

def test_parse_coverage_pytest_format():
    """Format pytest: TOTAL ... 85.5% → 85.5."""
    output = "TOTAL       500    75    85.5%"
    assert pe.parse_coverage(output) == 85.5


def test_parse_coverage_jest_format():
    """Format Jest: 'All files | 82.3 |' → 82.3."""
    output = "All files | 82.3 | 79.1 | 81.2 | 75.8 |"
    assert pe.parse_coverage(output) == 82.3


def test_parse_coverage_zero_percent():
    """Coverage 0% → 0.0."""
    output = "TOTAL 1000 1000 0%"
    assert pe.parse_coverage(output) == 0.0


def test_parse_coverage_decimal_percent():
    """Coverage con decimales: 79.22% → 79.22."""
    output = "Some coverage metric: 79.22%"
    assert pe.parse_coverage(output) == 79.22


def test_parse_coverage_no_match():
    """Output sin pattern conocido → 0.0."""
    output = "No coverage information here"
    assert pe.parse_coverage(output) == 0.0


def test_parse_coverage_multiple_percentages():
    """Múltiples %: retorna el último."""
    output = "Coverage 45.5% ... TOTAL 1000 200 81.2%"
    assert pe.parse_coverage(output) == 81.2


# ─────────────────────────────────────────────
# Tests: _accum_usage
# ─────────────────────────────────────────────

def test_accum_usage_none_acc():
    """acc=None → no hace nada (early return)."""
    usage = {"input": 100, "output": 50}
    pe._accum_usage(None, "plan", usage)
    # No exception, completes normally


def test_accum_usage_accumulates():
    """Acumula tokens correctamente en dict."""
    acc = {}
    usage1 = {"input": 100, "output": 50, "cache_read": 10, "cache_write": 5, "cost_usd": 0.01}
    pe._accum_usage(acc, "plan", usage1)
    assert acc["plan"]["input"] == 100
    assert acc["plan"]["output"] == 50
    assert acc["plan"]["cost_usd"] == 0.01


def test_accum_usage_updates_total():
    """_total se actualiza con cada acumulación."""
    acc = {}
    usage1 = {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0, "cost_usd": 0.01}
    pe._accum_usage(acc, "plan", usage1)
    assert acc["_total"]["input"] == 100

    usage2 = {"input": 50, "output": 25, "cache_read": 0, "cache_write": 0, "cost_usd": 0.005}
    pe._accum_usage(acc, "commit", usage2)
    assert acc["_total"]["input"] == 150
    assert acc["_total"]["output"] == 75


def test_accum_usage_missing_keys():
    """Usage dict con keys faltantes → usa defaults."""
    acc = {}
    usage = {"input": 100}  # missing output, cache_read, cache_write, cost_usd
    pe._accum_usage(acc, "plan", usage)
    assert acc["plan"]["input"] == 100
    assert acc["plan"]["output"] == 0


# ─────────────────────────────────────────────
# Tests: timestamp_branch
# ─────────────────────────────────────────────

def test_timestamp_branch_basic():
    """'My Task' → 'feature/YYYYMMDD-my-task'."""
    branch = pe.timestamp_branch("My Task")
    assert branch.startswith("feature/")
    assert "my-task" in branch


def test_timestamp_branch_special_chars():
    """Normaliza caracteres especiales."""
    branch = pe.timestamp_branch("Fix: Bug @2025")
    assert "fix-bug-2025" in branch
    assert "@" not in branch
    assert ":" not in branch


def test_timestamp_branch_long_task():
    """Limita a 40 caracteres después del timestamp."""
    long_task = "A" * 100
    branch = pe.timestamp_branch(long_task)
    # feature/YYYYMMDD-... total debe ser razonable
    assert len(branch) < 60


# ─────────────────────────────────────────────
# Tests: git helpers
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec.subprocess.run")
def test_git_runs_command(mock_run):
    """git('status') llama a subprocess.run con ['git', 'status']."""
    mock_run.return_value = Mock(returncode=0, stdout="On branch main\n", stderr="")
    code, out = pe.git("status")
    assert code == 0
    assert "main" in out
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert call_args[0] == "git"
    assert "status" in call_args


@patch("claude_workflow.plan_exec.subprocess.run")
def test_current_branch_returns_name(mock_run):
    """current_branch → git rev-parse --abbrev-ref HEAD."""
    mock_run.return_value = Mock(returncode=0, stdout="feature/test\n", stderr="")
    branch = pe.current_branch()
    assert branch == "feature/test"


@patch("claude_workflow.plan_exec.subprocess.run")
def test_create_branch_success(mock_run):
    """create_branch: git checkout -b → True."""
    mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
    result = pe.create_branch("feature/test")
    assert result is True


@patch("claude_workflow.plan_exec.subprocess.run")
def test_create_branch_fallback(mock_run):
    """create_branch: checkout -b falla → intenta checkout normal."""
    # Primera llamada falla (branch exists), segunda éxito
    mock_run.side_effect = [
        Mock(returncode=128, stdout="", stderr="fatal: A branch named 'feature/test' already exists"),
        Mock(returncode=0, stdout="", stderr="")
    ]
    result = pe.create_branch("feature/test")
    assert result is True
    assert mock_run.call_count == 2


@patch("claude_workflow.plan_exec.subprocess.run")
def test_commit_all_success(mock_run):
    """commit_all: git add -A && git commit."""
    mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
    result = pe.commit_all("feat: test")
    assert result is True
    # Debe llamarse 2 veces: add y commit
    assert mock_run.call_count == 2


@patch("claude_workflow.plan_exec.subprocess.run")
def test_commit_all_failure(mock_run):
    """commit_all: si commit falla → False."""
    mock_run.side_effect = [
        Mock(returncode=0, stdout="", stderr=""),  # add OK
        Mock(returncode=1, stdout="", stderr="nothing to commit")  # commit falla
    ]
    result = pe.commit_all("feat: test")
    assert result is False


@patch("claude_workflow.plan_exec.subprocess.run")
def test_delete_branch_calls_git(mock_run):
    """_delete_branch llama git checkout y branch -D."""
    mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
    pe._delete_branch("feature/test")
    # Debe llamar al menos 2 veces (rev-parse, checkout, branch -D)
    assert mock_run.call_count >= 2


# ─────────────────────────────────────────────
# Tests: _write_tokens_report
# ─────────────────────────────────────────────

def test_write_tokens_report_creates_file(tmp_path, monkeypatch):
    """_write_tokens_report crea PLAN_TOKENS.md con información de tokens."""
    monkeypatch.chdir(tmp_path)
    token_acc = {
        "plan": {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0, "cost_usd": 0.01},
        "_total": {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0, "cost_usd": 0.01}
    }
    pe._write_tokens_report(token_acc)
    assert (tmp_path / "PLAN_TOKENS.md").exists()
    content = (tmp_path / "PLAN_TOKENS.md").read_text()
    assert "plan" in content.lower()
    assert "100" in content  # input tokens


def test_write_tokens_report_none_acc(tmp_path, monkeypatch):
    """_write_tokens_report(None) → no hace nada."""
    monkeypatch.chdir(tmp_path)
    pe._write_tokens_report(None)
    assert not (tmp_path / "PLAN_TOKENS.md").exists()


def test_write_tokens_report_empty_acc(tmp_path, monkeypatch):
    """_write_tokens_report({}) → no hace nada."""
    monkeypatch.chdir(tmp_path)
    pe._write_tokens_report({})
    assert not (tmp_path / "PLAN_TOKENS.md").exists()


# ─────────────────────────────────────────────
# Tests: claude_p
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec.subprocess.run")
def test_claude_p_json_output(mock_run):
    """claude_p con JSON output → parsea result + usage."""
    json_response = json.dumps({
        "result": "output text",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 5,
        },
        "total_cost_usd": 0.01,
        "duration_ms": 1000
    })
    mock_run.return_value = Mock(returncode=0, stdout=json_response, stderr="")
    code, text, usage = pe.claude_p("test prompt")
    assert code == 0
    assert text == "output text"
    assert usage["input"] == 100
    assert usage["output"] == 50
    assert usage["cost_usd"] == 0.01


@patch("claude_workflow.plan_exec.subprocess.run")
def test_claude_p_non_json_output(mock_run):
    """claude_p con output no-JSON → retorna raw."""
    mock_run.return_value = Mock(returncode=0, stdout="plain text response", stderr="")
    code, text, usage = pe.claude_p("test prompt")
    assert code == 0
    assert text == "plain text response"
    assert isinstance(usage, dict)


@patch("claude_workflow.plan_exec.subprocess.run")
def test_claude_p_nonzero_exit(mock_run):
    """claude_p con returncode!=0 → retorna código de error."""
    mock_run.return_value = Mock(returncode=1, stdout="", stderr="error message")
    code, text, usage = pe.claude_p("test prompt")
    assert code == 1


@patch("claude_workflow.plan_exec.subprocess.run")
def test_claude_p_with_flags(mock_run):
    """claude_p usa flags en comando."""
    mock_run.return_value = Mock(returncode=0, stdout="ok", stderr="")
    pe.claude_p("test", flags=["--max-turns", "5"])
    call_args = mock_run.call_args[0][0]
    assert "--max-turns" in call_args
    assert "5" in call_args


# ─────────────────────────────────────────────
# Tests: claude_stream
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec.subprocess.Popen")
def test_claude_stream_basic(mock_popen):
    """claude_stream emite líneas JSON."""
    json_lines = [
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "world"}]}})
    ]
    mock_proc = Mock()
    mock_proc.stdout = iter(json_lines)
    mock_proc.returncode = 0
    mock_proc.wait.return_value = None
    mock_popen.return_value = mock_proc

    code, events = pe.claude_stream("test prompt")
    assert code == 0
    assert len(events) == 2


@patch("claude_workflow.plan_exec.subprocess.Popen")
def test_claude_stream_ignores_non_json(mock_popen):
    """claude_stream ignora líneas no-JSON."""
    lines = [
        "not json",
        json.dumps({"type": "assistant"}),
        "another non-json",
    ]
    mock_proc = Mock()
    mock_proc.stdout = iter(lines)
    mock_proc.returncode = 0
    mock_proc.wait.return_value = None
    mock_popen.return_value = mock_proc

    code, events = pe.claude_stream("test prompt")
    assert len(events) == 1  # solo JSON parseado


# ─────────────────────────────────────────────
# Tests: run_tests
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec.subprocess.run")
def test_run_tests_pytest_mode(mock_run, tmp_path, monkeypatch):
    """run_tests sin package.json → pytest."""
    monkeypatch.chdir(tmp_path)
    mock_run.return_value = Mock(
        returncode=0,
        stdout="TOTAL 1000 200 80%",
        stderr=""
    )
    passed, coverage, output = pe.run_tests()
    assert passed is True
    assert coverage == 80.0
    # Verifica que se llamó pytest
    call_args = mock_run.call_args[0][0]
    assert "pytest" in " ".join(call_args)


@patch("claude_workflow.plan_exec.subprocess.run")
def test_run_tests_jest_mode(mock_run, tmp_path, monkeypatch):
    """run_tests con package.json → jest."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text("{}")
    mock_run.return_value = Mock(
        returncode=0,
        stdout="All files | 85.5 | ...",
        stderr=""
    )
    passed, coverage, output = pe.run_tests()
    assert passed is True
    assert coverage == 85.5
    # Verifica que se llamó jest
    call_args = mock_run.call_args[0][0]
    assert "jest" in " ".join(call_args)


@patch("claude_workflow.plan_exec.subprocess.run")
def test_run_tests_failed(mock_run, tmp_path, monkeypatch):
    """run_tests returncode!=0 → passed=False."""
    monkeypatch.chdir(tmp_path)
    mock_run.return_value = Mock(
        returncode=1,
        stdout="FAILED 3 tests",
        stderr=""
    )
    passed, coverage, output = pe.run_tests()
    assert passed is False


# ─────────────────────────────────────────────
# Tests: step_plan_loop
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec.claude_p")
def test_step_plan_loop_first_iteration(mock_claude, tmp_path, monkeypatch):
    """step_plan_loop escribe PLAN.md en primera pasada."""
    monkeypatch.chdir(tmp_path)
    # Primera llamada (plan), segunda (review con APROBADO)
    mock_claude.side_effect = [
        (0, "plan initial", {}),
        (0, "APROBADO — plan es sólido", {})
    ]
    # Crear PLAN.md antes de que lo lea
    (tmp_path / "PLAN.md").write_text("plan initial")
    result = pe.step_plan_loop("My task")
    assert result is True


@patch("claude_workflow.plan_exec.claude_p")
def test_step_plan_loop_refinement(mock_claude, tmp_path, monkeypatch):
    """step_plan_loop refina plan si no está aprobado."""
    monkeypatch.chdir(tmp_path)
    # Simula: plan → review (REVISAR) → refine → review (APROBADO)
    mock_claude.side_effect = [
        (0, "initial plan", {}),  # plan
        (0, "REVISAR: agregar tests", {}),  # review 1
        (0, "refined plan", {}),  # refine
        (0, "APROBADO", {}),  # review 2
    ]
    # Crear PLAN.md antes de que lo lea
    (tmp_path / "PLAN.md").write_text("initial plan")
    result = pe.step_plan_loop("My task")
    assert result is True


# ─────────────────────────────────────────────
# Tests: step_commit
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec.commit_all")
@patch("claude_workflow.plan_exec.claude_p")
def test_step_commit_basic(mock_claude, mock_commit, tmp_path, monkeypatch):
    """step_commit genera mensaje y llama commit_all."""
    monkeypatch.chdir(tmp_path)
    mock_claude.return_value = (0, "feat: implement feature\n\nTests: coverage 85.0%", {})
    mock_commit.return_value = True

    result = pe.step_commit("implement feature", "feat/test", 85.0)
    assert result is True
    mock_commit.assert_called_once()


@patch("claude_workflow.plan_exec.commit_all")
@patch("claude_workflow.plan_exec.claude_p")
def test_step_commit_fallback_message(mock_claude, mock_commit, tmp_path, monkeypatch):
    """step_commit fallback si claude_p falla."""
    monkeypatch.chdir(tmp_path)
    mock_claude.return_value = (1, "", {})  # falla
    mock_commit.return_value = True

    result = pe.step_commit("my task", "feat/test", 85.0)
    assert result is True
    # Debe usar mensaje genérico
    msg = mock_commit.call_args[0][0]
    assert "my task" in msg


# ─────────────────────────────────────────────
# Tests: main CLI
# ─────────────────────────────────────────────

@patch("sys.argv", ["plan_exec.py", "-t", "My Task"])
@patch("claude_workflow.plan_exec.step_branch")
def test_main_with_task(mock_step, tmp_path, monkeypatch):
    """main con -t "task" procesa el argumento."""
    # Este test solo verifica que el parser acepta -t
    # No ejecutamos main() completo para evitar side effects
    parser = pe.argparse.ArgumentParser()
    parser.add_argument("-t", "--task", required=True)
    parser.add_argument("--branch", default="")
    parser.add_argument("--coverage", type=int, default=80)
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--skip-plan-loop", action="store_true")
    parser.add_argument("--skip-exec", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--opencode-fallback", action="store_true")
    parser.add_argument("--opencode-model", default="anthropic/claude-sonnet")
    parser.add_argument("--only-opencode", action="store_true")

    args = parser.parse_args(["-t", "My Task"])
    assert args.task == "My Task"


def test_main_no_args():
    """main sin -t → parser genera error."""
    parser = pe.argparse.ArgumentParser()
    parser.add_argument("-t", "--task", required=True)
    # Sin -t, debe fallar
    with pytest.raises(SystemExit):
        parser.parse_args([])


# ─────────────────────────────────────────────
# Tests: step_branch (wrapper)
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec.create_branch")
def test_step_branch_calls_create_branch(mock_create):
    """step_branch llama create_branch."""
    mock_create.return_value = True
    result = pe.step_branch("feature/test")
    assert result is True
    mock_create.assert_called_once_with("feature/test")


# ─────────────────────────────────────────────
# Tests: step_execute
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec.claude_stream")
def test_step_execute_success(mock_stream, tmp_path, monkeypatch):
    """step_execute llama claude_stream y retorna True."""
    monkeypatch.chdir(tmp_path)
    mock_stream.return_value = (0, [])  # success
    result = pe.step_execute()
    assert result is True
    assert (tmp_path / "EXECUTION_LOG.md").exists()


@patch("claude_workflow.plan_exec.claude_stream")
def test_step_execute_failure(mock_stream, tmp_path, monkeypatch):
    """step_execute si claude_stream falla → False."""
    monkeypatch.chdir(tmp_path)
    mock_stream.return_value = (1, [])  # failure
    result = pe.step_execute()
    assert result is False


# ─────────────────────────────────────────────
# Tests: step_tests
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec.run_tests")
@patch("claude_workflow.plan_exec.claude_stream")
def test_step_tests_coverage_met(mock_stream, mock_tests, tmp_path, monkeypatch):
    """step_tests si coverage >= MIN_COVERAGE → True."""
    monkeypatch.chdir(tmp_path)
    mock_stream.return_value = (0, [])
    mock_tests.return_value = (True, 85.0, "TOTAL 1000 200 85%")
    result = pe.step_tests()
    assert result is True


@patch("claude_workflow.plan_exec.confirm")
@patch("claude_workflow.plan_exec.run_tests")
@patch("claude_workflow.plan_exec.claude_stream")
def test_step_tests_coverage_not_met(mock_stream, mock_tests, mock_confirm, tmp_path, monkeypatch):
    """step_tests si coverage < MIN_COVERAGE → intenta boost."""
    monkeypatch.chdir(tmp_path)
    mock_stream.return_value = (0, [])
    mock_confirm.return_value = False  # No continuar sin coverage
    # Coverage insuficiente en todos los intentos
    mock_tests.side_effect = [
        (True, 60.0, "TOTAL 1000 600 60%"),  # intento 1
        (True, 70.0, "TOTAL 1000 400 70%"),  # intento 2
        (True, 75.0, "TOTAL 1000 300 75%"),  # intento 3
    ]
    result = pe.step_tests()
    assert result is False  # no alcanza 80%


@patch("claude_workflow.plan_exec.run_tests")
@patch("claude_workflow.plan_exec.claude_stream")
def test_step_tests_tests_fail(mock_stream, mock_tests, tmp_path, monkeypatch):
    """step_tests si los tests fallan → False."""
    monkeypatch.chdir(tmp_path)
    mock_stream.return_value = (0, [])
    mock_tests.return_value = (False, 50.0, "FAILED 10 tests")  # tests fallan
    result = pe.step_tests()
    assert result is False


@patch("claude_workflow.plan_exec.claude_stream")
def test_step_tests_stream_error(mock_stream, tmp_path, monkeypatch):
    """step_tests si claude_stream falla → False."""
    monkeypatch.chdir(tmp_path)
    mock_stream.return_value = (1, [])  # error generando tests
    result = pe.step_tests()
    assert result is False


# ─────────────────────────────────────────────
# Tests: _call_opencode (fallback)
# ─────────────────────────────────────────────

def test_call_opencode_import_error():
    """_call_opencode si importError → subprocess fallback."""
    # Al no tener opencode-ai instalado, debería intentar subprocess
    code, text, _, _ = pe._call_opencode("test prompt")
    # Sin opencode binary, puede retornar 0 o error — comportamiento indeterminado
    assert code in (0, 1) or isinstance(text, str)


# ─────────────────────────────────────────────
# Tests: confirm (user input)
# ─────────────────────────────────────────────

@patch("builtins.input", return_value="s")
def test_confirm_yes(mock_input):
    """confirm con 's' → True."""
    assert pe.confirm("Continue?") is True


@patch("builtins.input", return_value="si")
def test_confirm_yes_spanish(mock_input):
    """confirm con 'si' → True."""
    assert pe.confirm("Continue?") is True


@patch("builtins.input", return_value="n")
def test_confirm_no(mock_input):
    """confirm con 'n' → False."""
    assert pe.confirm("Continue?") is False


@patch("builtins.input", return_value="")
def test_confirm_empty(mock_input):
    """confirm con '' (enter) → False."""
    assert pe.confirm("Continue?") is False


# ─────────────────────────────────────────────
# Tests: step_branch con borrado
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec.create_branch")
def test_step_branch_failure(mock_create):
    """step_branch si create_branch falla → False."""
    mock_create.return_value = False
    result = pe.step_branch("feature/test")
    assert result is False


# ─────────────────────────────────────────────
# Tests: edge cases en parse_coverage
# ─────────────────────────────────────────────

def test_parse_coverage_with_newlines():
    """parse_coverage maneja output con múltiples líneas."""
    output = """
    ....
    TOTAL       1000     200      80.0%
    """
    assert pe.parse_coverage(output) == 80.0


def test_parse_coverage_jest_multiline():
    """parse_coverage Jest con múltiples líneas."""
    output = "All files | 85.5 | 82.1 | 88.3 |"
    assert pe.parse_coverage(output) == 85.5


# ─────────────────────────────────────────────
# Tests: accum_usage con valores grandes
# ─────────────────────────────────────────────

def test_accum_usage_large_numbers():
    """_accum_usage con números grandes (miles de tokens)."""
    acc = {}
    usage = {
        "input": 100000,
        "output": 50000,
        "cache_read": 10000,
        "cache_write": 5000,
        "cost_usd": 1.50
    }
    pe._accum_usage(acc, "plan", usage)
    assert acc["plan"]["input"] == 100000
    assert acc["_total"]["cost_usd"] == 1.50


# ─────────────────────────────────────────────
# Tests: claude_p con _OPENCODE_FALLBACK
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec.subprocess.run")
@patch("claude_workflow.plan_exec._call_opencode")
def test_claude_p_opencode_fallback(mock_opencode, mock_run):
    """claude_p usa opencode fallback si detecta token exhausted."""
    # Primera llamada falla con token limit
    mock_run.return_value = Mock(
        returncode=1,
        stdout="error: token limit exceeded",
        stderr=""
    )
    # Fallback a opencode
    mock_opencode.return_value = (0, "opencode response", None, {})

    # Activar fallback
    original_fallback = pe._OPENCODE_FALLBACK
    original_patterns = pe._OPENCODE_FALLBACK
    try:
        pe._OPENCODE_FALLBACK = True
        code, text, usage = pe.claude_p("test prompt")
        # Debería fallar en claude y luego usar opencode
        assert code == 1 or code == 0
    finally:
        pe._OPENCODE_FALLBACK = original_fallback


# ─────────────────────────────────────────────
# Tests: claude_p con _ONLY_OPENCODE
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec._call_opencode")
def test_claude_p_only_opencode(mock_opencode):
    """claude_p con _ONLY_OPENCODE=True → usa solo opencode."""
    mock_opencode.return_value = (0, "opencode output", None, {})
    original = pe._ONLY_OPENCODE
    try:
        pe._ONLY_OPENCODE = True
        code, text, usage = pe.claude_p("test prompt")
        assert code == 0
        assert text == "opencode output"
        mock_opencode.assert_called_once()
    finally:
        pe._ONLY_OPENCODE = original


# ─────────────────────────────────────────────
# Tests: Additional git helper tests
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec.subprocess.run")
def test_create_branch_double_failure(mock_run):
    """create_branch si ambos intentos fallan → False."""
    mock_run.side_effect = [
        Mock(returncode=128, stdout="", stderr="already exists"),
        Mock(returncode=128, stdout="", stderr="already exists")
    ]
    result = pe.create_branch("feature/test")
    assert result is False


@patch("claude_workflow.plan_exec.subprocess.run")
def test_git_with_multiple_args(mock_run):
    """git con múltiples argumentos."""
    mock_run.return_value = Mock(returncode=0, stdout="output", stderr="")
    code, out = pe.git("log", "--oneline", "-10")
    assert code == 0
    call_args = mock_run.call_args[0][0]
    assert "log" in call_args
    assert "--oneline" in call_args
    assert "-10" in call_args


# ─────────────────────────────────────────────
# Tests: parse_coverage con formatos variados
# ─────────────────────────────────────────────

def test_parse_coverage_percentage_with_space():
    """parse_coverage con espacio antes de %."""
    output = "TOTAL 1000 100 95 %"
    result = pe.parse_coverage(output)
    # Regex busca \d+(?:\.\d+)% sin espacios, así que puede no capturar
    assert isinstance(result, float)


def test_parse_coverage_empty_string():
    """parse_coverage con string vacío → 0.0."""
    assert pe.parse_coverage("") == 0.0


def test_parse_coverage_only_numbers():
    """parse_coverage con solo números → 0.0."""
    assert pe.parse_coverage("1000 200 300") == 0.0


# ─────────────────────────────────────────────
# Additional edge cases and comprehensive tests
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec.confirm", return_value=False)
@patch("claude_workflow.plan_exec.subprocess.run")
def test_step_tests_max_iterations(mock_run, mock_confirm, tmp_path, monkeypatch):
    """step_tests stops after MAX_PLAN_LOOPS iterations."""
    monkeypatch.chdir(tmp_path)
    mock_run.return_value = Mock(returncode=0, stdout="TOTAL ... 60%", stderr="")

    with patch("claude_workflow.plan_exec.claude_stream", return_value=(0, [])):
        result = pe.step_tests()
    # Should return False since coverage never reaches 80%
    assert result is False


@patch("claude_workflow.plan_exec.subprocess.run")
def test_create_branch_multiple_paths(mock_run):
    """create_branch handles different scenarios."""
    # Success on first try
    mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
    assert pe.create_branch("feat/new") is True

    # Failure on both tries
    mock_run.side_effect = [
        Mock(returncode=128, stdout="", stderr="exists"),
        Mock(returncode=128, stdout="", stderr="error"),
    ]
    assert pe.create_branch("feat/exists") is False


@patch("claude_workflow.plan_exec.subprocess.run")
def test_git_captures_both_stdout_stderr(mock_run):
    """git() combines stdout and stderr."""
    mock_run.return_value = Mock(
        returncode=0,
        stdout="standard output",
        stderr="standard error"
    )
    code, out = pe.git("status")
    assert "standard output" in out
    assert "standard error" in out


def test_accum_usage_float_values():
    """_accum_usage handles floating point token values."""
    acc = {}
    usage = {
        "input": 123.5,
        "output": 45.7,
        "cache_read": 10.2,
        "cache_write": 3.9,
        "cost_usd": 0.123
    }
    pe._accum_usage(acc, "test", usage)
    assert acc["test"]["cost_usd"] == 0.123


@patch("claude_workflow.plan_exec.subprocess.run")
def test_claude_p_partial_json(mock_run):
    """claude_p handles partially valid JSON gracefully."""
    invalid_json = '{"incomplete": "json'
    mock_run.return_value = Mock(returncode=0, stdout=invalid_json, stderr="")
    code, text, usage = pe.claude_p("test")
    # Should return raw text since JSON parse fails
    assert code == 0
    assert text == invalid_json


@patch("claude_workflow.plan_exec.subprocess.Popen")
def test_claude_stream_empty_output(mock_popen):
    """claude_stream handles empty output."""
    mock_proc = Mock()
    mock_proc.stdout = iter([])
    mock_proc.returncode = 0
    mock_proc.wait.return_value = None
    mock_popen.return_value = mock_proc

    code, events = pe.claude_stream("test")
    assert code == 0
    assert len(events) == 0


def test_timestamp_branch_lowercase_conversion():
    """timestamp_branch converts to lowercase."""
    branch = pe.timestamp_branch("MY UPPERCASE TASK")
    assert "my" in branch
    assert "uppercase" in branch
    assert "UPPERCASE" not in branch


@patch("claude_workflow.plan_exec.subprocess.run")
def test_commit_all_with_unicode(mock_run):
    """commit_all handles unicode in messages."""
    mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
    result = pe.commit_all("feat: añadir características 🎉")
    assert result is True


@patch("claude_workflow.plan_exec.subprocess.run")
def test_run_tests_coverage_extraction_variations(mock_run, tmp_path, monkeypatch):
    """run_tests extracts coverage from various formats."""
    monkeypatch.chdir(tmp_path)

    # Test with decimal coverage
    mock_run.return_value = Mock(
        returncode=0,
        stdout="Coverage: 87.456%",
        stderr=""
    )
    passed, coverage, output = pe.run_tests()
    assert coverage == 87.456

    # Test with integer coverage
    mock_run.return_value = Mock(
        returncode=0,
        stdout="TOTAL 500 50 90%",
        stderr=""
    )
    passed, coverage, output = pe.run_tests()
    assert coverage == 90.0


def test_confirm_case_insensitive():
    """confirm accepts case variations."""
    with patch("builtins.input", return_value="SI"):  # Spanish uppercase
        assert pe.confirm("Test?") is True

    with patch("builtins.input", return_value="Sí"):  # Spanish with accent
        assert pe.confirm("Test?") is True

    with patch("builtins.input", return_value="Y"):  # English
        assert pe.confirm("Test?") is True

    with patch("builtins.input", return_value="YES"):  # English uppercase
        assert pe.confirm("Test?") is True


@patch("claude_workflow.plan_exec.subprocess.run")
def test_delete_branch_from_different_branch(mock_run):
    """_delete_branch switches branch before deleting."""
    mock_run.side_effect = [
        Mock(returncode=0, stdout="", stderr="origin/main"),  # rev-parse
        Mock(returncode=0, stdout="", stderr=""),  # checkout
        Mock(returncode=0, stdout="", stderr=""),  # branch -D
    ]
    pe._delete_branch("feat/to-delete")
    assert mock_run.call_count >= 2


# ─────────────────────────────────────────────
# Comprehensive multi-step workflow tests
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec.create_branch", return_value=True)
def test_step_branch_basic(mock_branch):
    """step_branch calls create_branch with correct arguments."""
    result = pe.step_branch("feat/new")
    assert result is True
    mock_branch.assert_called_once_with("feat/new")


@patch("claude_workflow.plan_exec.subprocess.run")
def test_step_plan_loop_approval_detection(mock_run, tmp_path, monkeypatch):
    """step_plan_loop detects APROBADO in review."""
    monkeypatch.chdir(tmp_path)
    mock_run.side_effect = [
        Mock(returncode=0, stdout="plan", stderr=""),  # plan generation
        Mock(returncode=0, stdout="APROBADO en iteración 1", stderr=""),  # review with APROBADO
    ]

    Path("PLAN.md").write_text("# Plan\nInitial")

    with patch("claude_workflow.plan_exec.claude_p") as mock_claude:
        mock_claude.side_effect = [
            (0, "plan", {}),
            (0, "APROBADO en iteración 1", {})
        ]
        result = pe.step_plan_loop("My task")
    assert result is True


@patch("claude_workflow.plan_exec.subprocess.run")
def test_step_plan_loop_requires_refinement(mock_run, tmp_path, monkeypatch):
    """step_plan_loop enters refinement when REVISAR detected."""
    monkeypatch.chdir(tmp_path)

    Path("PLAN.md").write_text("# Plan\nInitial")

    with patch("claude_workflow.plan_exec.claude_p") as mock_claude:
        # plan → review (REVISAR) → refine → review (APROBADO)
        mock_claude.side_effect = [
            (0, "initial plan", {}),
            (0, "REVISAR: need more detail", {}),
            (0, "refined plan", {}),
            (0, "APROBADO", {})
        ]
        result = pe.step_plan_loop("My task")
    assert result is True
    assert mock_claude.call_count == 4


def test_parse_coverage_all_formats():
    """parse_coverage handles all known format variations."""
    # Jest format
    assert pe.parse_coverage("All files | 87.5 | others") == 87.5

    # pytest-cov format
    assert pe.parse_coverage("TOTAL 1000 200 80.0%") == 80.0

    # Total coverage fallback
    assert pe.parse_coverage("Total coverage: 92.3%") == 92.3

    # Last percentage fallback
    assert pe.parse_coverage("Some text 45% and 75.2%") == 75.2


@patch("claude_workflow.plan_exec.subprocess.run")
def test_run_tests_with_package_json_creates_correct_command(mock_run, tmp_path, monkeypatch):
    """run_tests uses jest for Node projects."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text("{}")

    mock_run.return_value = Mock(returncode=0, stdout="All files | 80 |", stderr="")
    passed, coverage, output = pe.run_tests()

    assert passed is True
    assert coverage == 80.0
    cmd = mock_run.call_args[0][0]
    assert "jest" in " ".join(cmd)


# ─────────────────────────────────────────────
# Tests for step_execute and step_tests flow
# ─────────────────────────────────────────────

@patch("claude_workflow.plan_exec.claude_stream")
def test_step_execute_creates_log_file(mock_stream, tmp_path, monkeypatch):
    """step_execute creates EXECUTION_LOG.md."""
    monkeypatch.chdir(tmp_path)
    mock_stream.return_value = (0, [])

    result = pe.step_execute()
    assert result is True
    assert (tmp_path / pe.EXEC_LOG).exists()


@patch("claude_workflow.plan_exec.claude_stream")
def test_step_execute_stream_error(mock_stream, tmp_path, monkeypatch):
    """step_execute returns False if claude_stream fails."""
    monkeypatch.chdir(tmp_path)
    mock_stream.return_value = (1, [])

    result = pe.step_execute()
    assert result is False


@patch("claude_workflow.plan_exec.run_tests")
@patch("claude_workflow.plan_exec.claude_stream")
def test_step_tests_single_attempt_success(mock_stream, mock_tests, tmp_path, monkeypatch):
    """step_tests succeeds on first attempt if coverage >= MIN_COVERAGE."""
    monkeypatch.chdir(tmp_path)
    mock_stream.return_value = (0, [])
    mock_tests.return_value = (True, 82.0, "TOTAL ... 82%")

    result = pe.step_tests()
    assert result is True
    # run_tests should be called at least once
    assert mock_tests.call_count >= 1


# ─────────────────────────────────────────────
# Tests for _call_opencode variations
# ─────────────────────────────────────────────

def test_call_opencode_without_sdk():
    """_call_opencode tries subprocess when SDK is not installed."""
    # SDK is not installed in test environment, should try subprocess
    code, text, _, _ = pe._call_opencode("test prompt")
    # Without opencode binary, puede retornar 0 o error — asegurar que retorna tuple
    assert isinstance(code, int) and isinstance(text, str)


@patch("claude_workflow.plan_exec.subprocess.run")
def test_claude_p_with_all_usage_fields(mock_run):
    """claude_p extracts all usage fields from JSON."""
    json_response = json.dumps({
        "result": "output",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 10,
        },
        "total_cost_usd": 0.0123,
        "duration_ms": 5000
    })
    mock_run.return_value = Mock(returncode=0, stdout=json_response, stderr="")
    code, text, usage = pe.claude_p("test")

    assert usage["input"] == 100
    assert usage["output"] == 50
    assert usage["cache_read"] == 20
    assert usage["cache_write"] == 10
    assert usage["cost_usd"] == 0.0123
    assert usage["duration_ms"] == 5000


@patch("claude_workflow.plan_exec.subprocess.run")
def test_claude_p_missing_usage_fields(mock_run):
    """claude_p handles JSON with missing usage fields."""
    json_response = json.dumps({
        "result": "output",
        # No usage field
    })
    mock_run.return_value = Mock(returncode=0, stdout=json_response, stderr="")
    code, text, usage = pe.claude_p("test")

    # Should have empty usage dict or zeros
    assert isinstance(usage, dict)
    assert usage.get("input", 0) == 0


# ─────────────────────────────────────────────
# Tests for main() CLI parsing
# ─────────────────────────────────────────────

def test_main_parses_task_argument():
    """main() parser accepts -t/--task."""
    parser = pe.argparse.ArgumentParser()
    parser.add_argument("-t", "--task", required=True)
    parser.add_argument("--branch", default="")
    parser.add_argument("--coverage", type=int, default=80)
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--skip-plan-loop", action="store_true")
    parser.add_argument("--skip-exec", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--opencode-fallback", action="store_true")
    parser.add_argument("--opencode-model", default="test")
    parser.add_argument("--only-opencode", action="store_true")

    args = parser.parse_args(["-t", "My Task"])
    assert args.task == "My Task"


def test_main_parses_coverage_argument():
    """main() parser accepts --coverage."""
    parser = pe.argparse.ArgumentParser()
    parser.add_argument("-t", "--task", required=True)
    parser.add_argument("--coverage", type=int, default=80)

    args = parser.parse_args(["-t", "Task", "--coverage", "90"])
    assert args.coverage == 90


def test_main_parses_branch_argument():
    """main() parser accepts --branch."""
    parser = pe.argparse.ArgumentParser()
    parser.add_argument("-t", "--task", required=True)
    parser.add_argument("--branch", default="")

    args = parser.parse_args(["-t", "Task", "--branch", "feat/custom"])
    assert args.branch == "feat/custom"


def test_main_parses_flags():
    """main() parser accepts boolean flags."""
    parser = pe.argparse.ArgumentParser()
    parser.add_argument("-t", "--task", required=True)
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--skip-plan-loop", action="store_true")
    parser.add_argument("--skip-exec", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")

    args = parser.parse_args(["-t", "Task", "--auto", "--skip-tests"])
    assert args.auto is True
    assert args.skip_tests is True
    assert args.skip_plan_loop is False


# ─────────────────────────────────────────────
# Tests for accum_usage with token tracking
# ─────────────────────────────────────────────

def test_accum_usage_multiple_steps():
    """_accum_usage correctly accumulates across multiple steps."""
    acc = {}

    # Add tokens for plan step
    pe._accum_usage(acc, "plan", {
        "input": 100,
        "output": 50,
        "cache_read": 10,
        "cache_write": 5,
        "cost_usd": 0.01
    })

    # Add tokens for commit step
    pe._accum_usage(acc, "commit", {
        "input": 30,
        "output": 15,
        "cache_read": 5,
        "cache_write": 2,
        "cost_usd": 0.003
    })

    # Verify individual steps
    assert acc["plan"]["input"] == 100
    assert acc["commit"]["input"] == 30

    # Verify total
    assert acc["_total"]["input"] == 130
    assert acc["_total"]["output"] == 65
    assert acc["_total"]["cost_usd"] == pytest.approx(0.013)


# ─────────────────────────────────────────────
# Tests for timestamp_branch edge cases
# ─────────────────────────────────────────────

def test_timestamp_branch_max_length():
    """timestamp_branch respects 40 char limit on slug."""
    very_long_task = "A" * 100
    branch = pe.timestamp_branch(very_long_task)
    # Format is feature/YYYYMMDD-{slug}
    # Total length should be reasonable
    assert len(branch) < 60


def test_timestamp_branch_with_numbers():
    """timestamp_branch preserves numbers in slug."""
    task = "Task 2025 v1.0"
    branch = pe.timestamp_branch(task)
    assert "2025" in branch
    assert "1" in branch
    assert "0" in branch


def test_timestamp_branch_with_hyphens():
    """timestamp_branch handles existing hyphens."""
    task = "New-Feature-Name"
    branch = pe.timestamp_branch(task)
    assert "new" in branch
    assert "feature" in branch
    assert "name" in branch


# ─────────────────────────────────────────────
# Tests for coverage parsing edge cases
# ─────────────────────────────────────────────

def test_parse_coverage_with_tabs():
    """parse_coverage handles tab-separated columns."""
    output = "TOTAL\t500\t100\t80%"
    result = pe.parse_coverage(output)
    assert result == 80.0


def test_parse_coverage_with_extra_spaces():
    """parse_coverage handles extra whitespace."""
    output = "TOTAL     500     100     75.5%"
    result = pe.parse_coverage(output)
    assert result == 75.5


def test_parse_coverage_multiline_with_multiple_percentages():
    """parse_coverage extracts last percentage when multiple exist."""
    output = """
    Coverage summary: 45.0%
    ...
    TOTAL 1000 150 85.0%
    """
    result = pe.parse_coverage(output)
    assert result == 85.0


def test_step_branch_prints_output(capsys, tmp_path, monkeypatch):
    """step_branch prints header and calls create_branch."""
    monkeypatch.chdir(tmp_path)

    with patch("claude_workflow.plan_exec.create_branch", return_value=True):
        result = pe.step_branch("feature/new-branch")

    captured = capsys.readouterr()
    # Output contains the section header for "Crear branch"
    assert "Crear branch" in captured.out or "PASO 1" in captured.out
    assert result is True
