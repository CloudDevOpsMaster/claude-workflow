#!/usr/bin/env python3
"""
claude_workflow.py
Branch → Plan (loop refinamiento) → Tests (coverage >80%) → Commit
"""
from __future__ import annotations

import subprocess
import sys
import json
import re
import argparse
from pathlib import Path
from datetime import datetime

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

PLAN_FILE       = "PLAN.md"
REVIEW_FILE     = "PLAN_REVIEW.md"
EXEC_LOG        = "EXECUTION_LOG.md"
MIN_COVERAGE    = 80
MAX_PLAN_LOOPS  = 5
MAX_TURNS       = 40

# Fallback a opencode cuando Claude supera el context window
_ONLY_OPENCODE: bool = False # Add this
_OPENCODE_FALLBACK: bool = False
_OPENCODE_MODEL: str = "anthropic/claude-sonnet-4-5-20251001"
_OPENCODE_BIN: str = "/home/dev/.opencode/bin/opencode"


# ─────────────────────────────────────────────
# Git helpers
# ─────────────────────────────────────────────

def git(*args) -> tuple[int, str]:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def current_branch() -> str:
    _, out = git("branch", "--show-current")
    return out.strip()


def create_branch(branch: str) -> bool:
    code, out = git("checkout", "-b", branch)
    if code != 0:
        print(f"⚠  Branch ya existe, haciendo checkout: {out}")
        code, out = git("checkout", branch)
    print(f"🌿  Branch: {branch}")
    return code == 0


def commit_all(message: str) -> bool:
    git("add", "-A")
    code, out = git("commit", "-m", message)
    print(f"{'✅' if code == 0 else '❌'}  Commit: {out}")
    return code == 0


def _delete_branch(branch: str) -> None:
    """Vuelve al branch por defecto y elimina el branch local."""
    _, default = git("rev-parse", "--abbrev-ref", "origin/HEAD")
    default = default.replace("origin/", "").strip() or "main"
    git("checkout", default)
    code, out = git("branch", "-D", branch)
    if code == 0:
        print(f"🗑   Branch '{branch}' eliminado.")
    else:
        print(f"⚠  No se pudo eliminar '{branch}': {out}")


# ─────────────────────────────────────────────
# Opencode fallback helpers
# ─────────────────────────────────────────────

def _is_token_exhausted(exit_code: int, output: str) -> bool:
    """Detecta si un fallo de Claude CLI se debe a context window exceeded."""
    if exit_code == 0:
        return False
    _PATTERNS = [
        "context length exceeded",
        "context_length_exceeded",
        "context window",
        "token limit",
        "too long",
        "max_tokens",
        "exceeds the",
        "prompt is too long",
    ]
    output_lower = output.lower()
    return any(p in output_lower for p in _PATTERNS)


def _call_opencode(prompt: str) -> tuple[int, str, None, dict]:
    """
    Llama a opencode via Python SDK (opencode-ai) si está disponible y
    opencode serve está corriendo en localhost:4096.
    Si no, hace fallback a 'opencode run' via subprocess.
    Retorna 4-tupla (exit_code, text, None, {}) para compatibilidad con claude_p_with_session.
    """
    try:
        from opencode_ai import Opencode  # type: ignore

        parts = _OPENCODE_MODEL.split("/", 1)
        provider_id = parts[0]
        model_id = parts[1] if len(parts) > 1 else parts[0]

        client = Opencode(base_url="http://localhost:4096", timeout=300.0)
        session = client.session.create()
        msg = client.session.chat(
            session.id,
            model_id=model_id,
            provider_id=provider_id,
            parts=[{"type": "text", "text": prompt}],
        )

        if getattr(msg, "error", None):
            return 1, str(msg.error), None, {}

        messages = client.session.messages(session.id)
        texts = [
            part.text
            for item in messages
            for part in item.parts
            if getattr(part, "type", None) == "text" and hasattr(part, "text")
        ]
        return 0, "\n".join(texts), None, {}

    except ImportError:
        pass  # SDK no instalado — usar subprocess
    except Exception as exc:
        return 1, str(exc), None, {}

    # Fallback: opencode run via subprocess
    if not Path(_OPENCODE_BIN).exists():
        return 1, "opencode no encontrado en " + _OPENCODE_BIN, None, {}
    cmd = [_OPENCODE_BIN, "run", "--model", _OPENCODE_MODEL, prompt]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout.strip() or r.stderr.strip(), None, {}


# ─────────────────────────────────────────────
# Claude helpers
# ─────────────────────────────────────────────

def claude_p(prompt: str, flags: list[str] = None) -> tuple[int, str, dict]:
    """Corre claude -p, captura output completo. Retorna (exit_code, text, usage)."""
    if _ONLY_OPENCODE:
        print(f"  🤖 Usando opencode (--only-opencode activo)...")
        oc, ot, _, _ = _call_opencode(prompt)
        return oc, ot, {} # Return empty usage dict for now

    cmd = ["claude", "-p", prompt, "--output-format", "json"] + (flags or [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    output = r.stdout.strip()
    usage: dict = {}
    try:
        data = json.loads(output)
        text = data.get("result", output)
        u = data.get("usage", {})
        usage = {
            "input":       u.get("input_tokens", 0),
            "output":      u.get("output_tokens", 0),
            "cache_read":  u.get("cache_read_input_tokens", 0),
            "cache_write": u.get("cache_creation_input_tokens", 0),
            "cost_usd":    data.get("total_cost_usd", 0.0),
            "duration_ms": data.get("duration_ms", 0),
        }
    except (json.JSONDecodeError, AttributeError):
        text = output

    if r.returncode != 0 and _OPENCODE_FALLBACK and _is_token_exhausted(r.returncode, text):
        print(f"  ⚠️  Token limit → opencode ({_OPENCODE_MODEL})...")
        oc, ot, _, _ = _call_opencode(prompt)
        return oc, ot, {}

    return r.returncode, text, usage


def claude_stream(prompt: str, flags: list[str] = None) -> tuple[int, list]:
    """Corre claude -p con stream-json, imprime en tiempo real."""
    if _ONLY_OPENCODE:
        print(f"  🤖 Usando opencode (--only-opencode activo, stream emulado)...")
        oc, ot, _, _ = _call_opencode(prompt)
        print(ot) # Print the whole output at once
        # For stream compatibility, return a single event with the output
        return oc, [{"type": "assistant", "message": {"content": [{"type": "text", "text": ot}]}}]


    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"] + (flags or [])
    events = []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        line = line.strip()
        if line:
            print(line)
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    proc.wait()
    return proc.returncode, events


# ─────────────────────────────────────────────
# Paso 1: Crear branch
# ─────────────────────────────────────────────

def step_branch(branch: str) -> bool:
    print(f"\n{'═'*60}")
    print("  PASO 1: Crear branch")
    print(f"{'═'*60}")
    return create_branch(branch)


# ─────────────────────────────────────────────
# Paso 2: Loop de refinamiento del plan
# ─────────────────────────────────────────────

PLAN_PROMPT = """
Eres un arquitecto de software senior. Analiza el proyecto actual y la tarea:

TAREA: {task}

Genera un plan de implementación en {plan_file} con este formato exacto:

# Plan: {task}

## Objetivo
<una línea clara>

## Pasos
### Paso N: <nombre>
- **Qué hace**: ...
- **Archivos**: `ruta/archivo.py`
- **Validación**: `<comando shell para verificar>`

## Riesgos
- ...

## Criterios de Aceptación
- [ ] Tests unitarios con coverage > {coverage}%
- [ ] Sin errores de lint
- [ ] <criterio específico de la tarea>

Solo genera el plan. No implementes nada todavía.
"""

REVIEW_PROMPT = """
Revisa el plan en {plan_file} con ojo crítico.

Evalúa:
1. ¿Es el plan completo para la tarea: "{task}"?
2. ¿Los pasos son atómicos y ejecutables?
3. ¿Falta manejo de errores o casos edge?
4. ¿Los criterios de aceptación son verificables?

Responde con:
- APROBADO si el plan es sólido (sin cambios necesarios)
- REVISAR: <lista de problemas específicos> si necesita mejoras

Sé exigente. Un plan malo genera código malo.
"""

REFINE_PROMPT = """
Mejora el plan en {plan_file} basándote en esta revisión:

{feedback}

Actualiza {plan_file} con las mejoras. Mantén el mismo formato.
"""


def step_plan_loop(task: str, token_acc: dict = None) -> bool:
    print(f"\n{'═'*60}")
    print("  PASO 2: Loop de refinamiento del plan")
    print(f"{'═'*60}")

    # Generar plan inicial
    print("\n📝  Generando plan inicial...")
    code, _, usage = claude_p(
        PLAN_PROMPT.format(task=task, plan_file=PLAN_FILE, coverage=MIN_COVERAGE),
        flags=["--allowedTools", "Read,Grep,Glob,Write", "--max-turns", "10"]
    )
    _accum_usage(token_acc, "plan", usage)
    if code != 0:
        print("❌  Error generando plan inicial")
        return False

    for iteration in range(1, MAX_PLAN_LOOPS + 1):
        print(f"\n🔍  Revisión del plan — iteración {iteration}/{MAX_PLAN_LOOPS}")

        code, review, usage = claude_p(
            REVIEW_PROMPT.format(plan_file=PLAN_FILE, task=task),
            flags=["--allowedTools", "Read", "--max-turns", "5"]
        )
        _accum_usage(token_acc, "plan", usage)
        if code != 0:
            print("❌  Error revisando plan")
            return False

        # Guardar review para trazabilidad
        Path(REVIEW_FILE).write_text(
            f"# Revisión iteración {iteration}\n\n{review}\n"
        )
        print(f"\n📋  Review:\n{review}\n")

        if "APROBADO" in review.upper():
            print(f"✅  Plan aprobado en iteración {iteration}")
            print(f"\n{Path(PLAN_FILE).read_text()}")
            return True

        # Extraer feedback y refinar
        feedback = review.replace("REVISAR:", "").strip()
        print(f"🔧  Refinando plan...")
        code, _, usage = claude_p(
            REFINE_PROMPT.format(plan_file=PLAN_FILE, feedback=feedback),
            flags=["--allowedTools", "Read,Write", "--max-turns", "8"]
        )
        _accum_usage(token_acc, "plan", usage)
        if code != 0:
            print("❌  Error refinando plan")
            return False

    print(f"⚠  Máximo de iteraciones ({MAX_PLAN_LOOPS}) alcanzado.")
    # Mostrar plan final de todas formas
    if Path(PLAN_FILE).exists():
        print(f"\n{Path(PLAN_FILE).read_text()}")

    return confirm("¿Continuar con el plan actual?")


# ─────────────────────────────────────────────
# Paso 3: Ejecutar el plan
# ─────────────────────────────────────────────

EXEC_PROMPT = """
Implementa el plan en {plan_file} paso a paso.

Reglas estrictas:
1. Sigue el orden exacto del plan.
2. Tras cada paso, ejecuta su comando de validación.
3. Si un paso falla, documenta en {log_file} y detente.
4. No agregues código fuera del alcance del plan.
5. Escribe código limpio, con docstrings y type hints.

Al terminar, escribe resumen en {log_file}.
"""


def step_execute() -> bool:
    print(f"\n{'═'*60}")
    print("  PASO 3: Ejecutar el plan")
    print(f"{'═'*60}")

    Path(EXEC_LOG).write_text(
        f"# Execution Log\n\n**Inicio**: {datetime.now().isoformat()}\n\n"
    )

    code, _ = claude_stream(
        EXEC_PROMPT.format(plan_file=PLAN_FILE, log_file=EXEC_LOG),
        flags=["--dangerously-skip-permissions", "--max-turns", str(MAX_TURNS)]
    )

    if code != 0:
        print(f"❌  Ejecución falló. Revisa {EXEC_LOG}")
        return False

    print(f"✅  Ejecución completada")
    return True


# ─────────────────────────────────────────────
# Paso 4: Tests con coverage
# ─────────────────────────────────────────────

TESTS_PROMPT = """
El plan está implementado. Ahora genera tests unitarios exhaustivos.

Requisitos:
1. Usa pytest + pytest-cov
2. Cubre todos los módulos nuevos/modificados
3. Incluye casos: happy path, edge cases, errores esperados
4. Los tests deben ser independientes entre sí
5. Alcanza coverage > {coverage}% en los módulos modificados

Pasos:
1. Identifica los módulos a testear
2. Crea/actualiza archivos test_*.py
3. Ejecuta: pytest --cov=. --cov-report=term-missing
4. Si coverage < {coverage}%, agrega más tests y repite
5. Ejecuta hasta pasar el umbral

Reporta el coverage final alcanzado.
"""

COVERAGE_BOOST_PROMPT = """
El coverage actual es {current}%, necesitamos > {target}%.

Módulos con bajo coverage según el reporte:
{report}

Agrega tests específicos para las líneas no cubiertas.
Luego vuelve a ejecutar pytest --cov=. --cov-report=term-missing
"""


def parse_coverage(output: str) -> float:
    """Extrae el % de coverage total del output de pytest-cov o Jest.

    Soporta:
    - pytest-cov: TOTAL seguido de N columnas numéricas y un porcentaje
    - Jest: "All files" seguido de porcentajes (ej: "All files | 79.22 |")
    """
    # Formato Jest: "All files | XX.XX | ..."
    match = re.search(r'^All files\s*\|\s*(\d+(?:\.\d+)?)\s*\|', output, re.MULTILINE)
    if match:
        return float(match.group(1))
    # Formato pytest: TOTAL seguido de N columnas numéricas y un porcentaje
    match = re.search(r'^TOTAL\s+[\d\s]+?(\d+(?:\.\d+)?)%', output, re.MULTILINE)
    if match:
        return float(match.group(1))
    # Fallback: "Total coverage: XX%"
    match2 = re.search(r'Total coverage:\s*(\d+(?:\.\d+)?)%', output)
    if match2:
        return float(match2.group(1))
    # Fallback final: último porcentaje con posibles decimales en el output
    matches = re.findall(r'(\d+(?:\.\d+)?)%', output)
    return float(matches[-1]) if matches else 0.0


def run_tests() -> tuple[bool, float, str]:
    """Corre tests con coverage. Detecta pytest o Jest según el proyecto."""
    _backend_dir = Path(__file__).resolve().parent.parent / "backend"

    # Detectar tipo de proyecto
    if (_backend_dir / "package.json").exists():
        # Node.js project — usar Jest
        r = subprocess.run(
            ["npx", "jest", "--coverage", "--coverageReporters=text", "--forceExit"],
            capture_output=True, text=True,
            cwd=str(_backend_dir)
        )
    else:
        # Python project — usar pytest
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "--cov=.", "--cov-report=term-missing", "-v"],
            capture_output=True, text=True,
            cwd=str(_backend_dir)
        )

    output = r.stdout + r.stderr
    coverage = parse_coverage(output)
    passed = r.returncode == 0
    return passed, coverage, output


def step_tests() -> bool:
    print(f"\n{'═'*60}")
    print(f"  PASO 4: Tests unitarios (objetivo: coverage > {MIN_COVERAGE}%)")
    print(f"{'═'*60}")

    # Generar tests
    print("\n🧪  Generando tests...")
    code, _ = claude_stream(
        TESTS_PROMPT.format(coverage=MIN_COVERAGE),
        flags=["--dangerously-skip-permissions", "--max-turns", "20"]
    )
    if code != 0:
        print("❌  Error generando tests")
        return False

    # Verificar coverage en loop
    for attempt in range(1, 4):
        print(f"\n📊  Midiendo coverage — intento {attempt}/3")
        passed, coverage, report = run_tests()

        print(f"\n{'✅' if passed else '❌'}  Tests: {'OK' if passed else 'FALLARON'}")
        print(f"📈  Coverage: {coverage:.1f}% (objetivo: {MIN_COVERAGE}%)")

        if not passed:
            print("❌  Tests fallando. Abortando.")
            print(report)
            return False

        if coverage >= MIN_COVERAGE:
            print(f"✅  Coverage alcanzado: {coverage:.1f}%")
            return True

        # Pedir boost
        print(f"⚠  Coverage insuficiente ({coverage:.1f}%). Mejorando...")
        claude_stream(
            COVERAGE_BOOST_PROMPT.format(
                current=coverage,
                target=MIN_COVERAGE,
                report=report[-3000:]  # últimas líneas del reporte
            ),
            flags=["--dangerously-skip-permissions", "--max-turns", "15"]
        )

    print(f"⚠  No se alcanzó {MIN_COVERAGE}% de coverage después de 3 intentos")
    return confirm("¿Hacer commit de todas formas?")


# ─────────────────────────────────────────────
# Paso 5: Commit
# ─────────────────────────────────────────────

COMMIT_MSG_PROMPT = """
Genera un mensaje de commit siguiendo Conventional Commits para:

Tarea: {task}
Branch: {branch}
Plan ejecutado: {plan_summary}

Formato requerido:
<type>(<scope>): <descripción corta>

<cuerpo opcional con detalles>

Tests: coverage {coverage}%

Responde SOLO con el mensaje de commit, sin comillas ni explicaciones.
"""


def step_commit(task: str, branch: str, coverage: float, token_acc: dict = None) -> bool:
    print(f"\n{'═'*60}")
    print("  PASO 5: Commit")
    print(f"{'═'*60}")

    plan_summary = ""
    if Path(PLAN_FILE).exists():
        lines = Path(PLAN_FILE).read_text().split('\n')
        plan_summary = '\n'.join(lines[:20])  # primeras 20 líneas

    code, commit_msg, usage = claude_p(
        COMMIT_MSG_PROMPT.format(
            task=task,
            branch=branch,
            plan_summary=plan_summary,
            coverage=f"{coverage:.1f}"
        ),
        flags=["--allowedTools", "Read", "--max-turns", "3"]
    )
    _accum_usage(token_acc, "commit", usage)

    if code != 0 or not commit_msg.strip():
        # Fallback a mensaje genérico
        commit_msg = f"feat: {task}\n\nTests: coverage {coverage:.1f}%"

    print(f"\n📝  Mensaje de commit:\n{commit_msg}\n")
    return commit_all(commit_msg.strip())


# ─────────────────────────────────────────────
# Utils
# ─────────────────────────────────────────────

def confirm(message: str) -> bool:
    resp = input(f"\n{message} [s/N]: ").strip().lower()
    return resp in ("s", "si", "sí", "y", "yes")


def _accum_usage(acc: dict, step: str, usage: dict) -> None:
    """Acumula tokens de un paso en el dict acumulador."""
    if acc is None or not usage:
        return
    entry = acc.setdefault(step, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost_usd": 0.0})
    for k in ("input", "output", "cache_read", "cache_write"):
        entry[k] += usage.get(k, 0)
    entry["cost_usd"] += usage.get("cost_usd", 0.0)
    # Actualizar total
    total = acc.setdefault("_total", {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost_usd": 0.0})
    for k in ("input", "output", "cache_read", "cache_write"):
        total[k] += usage.get(k, 0)
    total["cost_usd"] += usage.get("cost_usd", 0.0)


def _write_tokens_report(token_acc: dict) -> None:
    """Escribe PLAN_TOKENS.md con el resumen de uso de tokens."""
    if not token_acc:
        return
    lines = ["# Token Usage Report\n"]
    for step, data in token_acc.items():
        if step == "_total":
            continue
        lines.append(f"## {step.capitalize()}")
        lines.append(f"- Entrada   : {data['input']:,}")
        lines.append(f"- Salida    : {data['output']:,}")
        lines.append(f"- Caché R   : {data['cache_read']:,}")
        lines.append(f"- Caché W   : {data['cache_write']:,}")
        lines.append(f"- Costo     : ${data['cost_usd']:.4f}\n")
    total = token_acc.get("_total", {})
    if total:
        lines.append("## TOTAL")
        lines.append(f"- Entrada   : {total['input']:,}")
        lines.append(f"- Salida    : {total['output']:,}")
        lines.append(f"- Caché R   : {total['cache_read']:,}")
        lines.append(f"- Caché W   : {total['cache_write']:,}")
        lines.append(f"- Costo     : ${total['cost_usd']:.4f}")
    Path("PLAN_TOKENS.md").write_text("\n".join(lines) + "\n")


def timestamp_branch(task: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', task.lower()).strip('-')[:40]
    ts = datetime.now().strftime("%Y%m%d")
    return f"feature/{ts}-{slug}"


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    global MIN_COVERAGE, _OPENCODE_FALLBACK, _OPENCODE_MODEL, _ONLY_OPENCODE
    parser = argparse.ArgumentParser(
        description="Branch → Plan (loop) → Implement → Tests >80% → Commit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python claude_workflow.py -t "Agregar autenticación JWT"
  python claude_workflow.py -t "Migrar ORM a SQLAlchemy 2.0" --branch feature/sqlalchemy
  python claude_workflow.py -t "Refactor módulo de pagos" --coverage 90 --auto
  python claude_workflow.py -t "Fix bug en cálculo de impuestos" --skip-plan-loop
        """
    )
    parser.add_argument("-t", "--task",       required=True,  help="Descripción de la tarea")
    parser.add_argument("--branch",           default="",     help="Nombre del branch (auto-generado si no se da)")
    parser.add_argument("--coverage",         type=int,       default=MIN_COVERAGE, help=f"Coverage mínimo %% (default: {MIN_COVERAGE})")
    parser.add_argument("--auto",             action="store_true", help="Sin confirmaciones manuales")
    parser.add_argument("--skip-plan-loop",   action="store_true", help="Genera plan sin loop de revisión")
    parser.add_argument("--skip-exec",        action="store_true", help="Saltar ejecución del plan")
    parser.add_argument("--skip-tests",       action="store_true", help="Saltar generación de tests")
    parser.add_argument("--opencode-fallback", action="store_true", default=False,
                        help="Usar opencode como fallback si Claude supera el context window.")
    parser.add_argument("--opencode-model",   default=_OPENCODE_MODEL, metavar="MODEL",
                        help="Modelo a usar en opencode (formato provider/model).")
    parser.add_argument("--only-opencode", action="store_true", default=False,
                        help="Usar solo opencode, sin intentar con Claude.")
    args = parser.parse_args()

    # Ajustar config global
    MIN_COVERAGE = args.coverage
    _OPENCODE_FALLBACK = args.opencode_fallback
    if args.opencode_model != _OPENCODE_MODEL:
        _OPENCODE_MODEL = args.opencode_model
    _ONLY_OPENCODE = args.only_opencode

    branch = args.branch or timestamp_branch(args.task)

    print(f"""
╔{'═'*58}╗
║  CLAUDE WORKFLOW                                         ║
║  Tarea    : {args.task[:46]:<46}  ║
║  Branch   : {branch[:46]:<46}  ║
║  Coverage : {str(args.coverage) + '%':<46}  ║
╚{'═'*58}╝
""")

    coverage_achieved = 0.0
    results = {}

    try:
        _main_steps(args, branch, coverage_achieved, results)
    except KeyboardInterrupt:
        print("\n⚠  Interrumpido por el usuario.")
        if confirm("¿Eliminar el branch creado?"):
            _delete_branch(branch)
        sys.exit(130)


def _main_steps(args, branch, coverage_achieved, results):
    token_acc: dict = {}

    # 1. Branch
    ok = step_branch(branch)
    results["branch"] = ok
    if not ok:
        print("❌  No se pudo crear el branch. Abortando.")
        sys.exit(1)

    # 2. Plan loop
    if args.skip_plan_loop:
        print("\n⏭  Saltando loop de refinamiento (--skip-plan-loop)")
        ok = True
        # Generar plan simple sin revisión
        _, _, usage = claude_p(
            PLAN_PROMPT.format(task=args.task, plan_file=PLAN_FILE, coverage=MIN_COVERAGE),
            flags=["--allowedTools", "Read,Grep,Glob,Write", "--max-turns", "10"]
        )
        _accum_usage(token_acc, "plan", usage)
    else:
        ok = step_plan_loop(args.task, token_acc=token_acc)

    results["plan"] = ok
    if not ok:
        print("❌  Plan no aprobado. Abortando.")
        if not args.auto and confirm("¿Eliminar el branch creado?"):
            _delete_branch(branch)
        sys.exit(1)

    if not args.auto:
        if not confirm("¿Proceder con la implementación?"):
            print(f"Pausado. El plan está en {PLAN_FILE}. Ejecuta con --skip-plan-loop cuando estés listo.")
            if confirm("¿Eliminar el branch creado?"):
                _delete_branch(branch)
            sys.exit(0)

    # 3. Ejecutar
    if not args.skip_exec:
        ok = step_execute()
        results["execute"] = ok
        if not ok:
            print("❌  Ejecución fallida. Revisa el log.")
            sys.exit(1)

    # 4. Tests
    if not args.skip_tests:
        ok = step_tests()
        results["tests"] = ok

        # Medir coverage final
        _, coverage_achieved, _ = run_tests()
        results["coverage"] = coverage_achieved

        if not ok:
            print("❌  Tests no superaron el umbral. Abortando commit.")
            if not args.auto and confirm("¿Eliminar el branch creado?"):
                _delete_branch(branch)
            sys.exit(1)

    # 5. Commit
    ok = step_commit(args.task, branch, coverage_achieved, token_acc=token_acc)
    results["commit"] = ok

    # Guardar reporte de tokens
    _write_tokens_report(token_acc)

    # Resumen final
    print(f"""
╔{'═'*58}╗
║  RESUMEN FINAL                                           ║""")
    icons = {"branch": "🌿", "plan": "📋", "execute": "⚙️ ", "tests": "🧪", "commit": "💾"}
    for step, status in results.items():
        if step == "coverage":
            print(f"║    📈 Coverage : {str(results['coverage']) + '%':<40}  ║")
        else:
            icon = icons.get(step, "•")
            label = f"{icon} {step.capitalize()}"
            mark = "✅" if status else "❌"
            print(f"║    {mark} {label:<50}  ║")
    total = token_acc.get("_total", {})
    if total:
        print(f"║  {'─'*56}  ║")
        print(f"║    💬 Tokens entrada : {total['input']:>10,}{'':>26}║")
        print(f"║    📤 Tokens salida  : {total['output']:>10,}{'':>26}║")
        print(f"║    ⚡ Caché (read)   : {total['cache_read']:>10,}{'':>26}║")
        print(f"║    💰 Costo total    : ${total['cost_usd']:>10.4f}{'':>25}║")
    print(f"╚{'═'*58}╝\n")

    sys.exit(0 if all(v for k, v in results.items() if k != "coverage") else 1)


if __name__ == "__main__":
    main()
