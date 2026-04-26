#!/usr/bin/env python3
"""
claude_iterative.py
Flujo: humano dispara → N agentes Claude CLI trabajan en paralelo.
Fases: Branch → Análisis(3 agentes) → Síntesis → Implementación → Integración → Commit
"""
from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import inspect
import json
import logging
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from jinja2 import Template

# Importar utilidades de plan_exec
from claude_workflow import plan_exec as base
from claude_workflow.cli_backend import (
    CLIBackend,
    CLIResult,
    FallbackBackend,
    ClaudeCLI,
    CursorCLI,
    get_default_backend,
    reset_default_backend,
    set_default_backend,
)

# ─────────────────────────────────────────────
# Config global
# ─────────────────────────────────────────────

AGENTS_DIR = Path("agents")
MIN_COVERAGE = 80
DEFAULT_WORKERS = 3
DEFAULT_TIMEOUT = 600
DEFAULT_RETRIES = 2

# Modo automático (sin prompts interactivos)
_AUTO_MODE: bool = False


def _confirm(msg: str, default: bool = False) -> bool:
    """base.confirm() consciente de --auto: devuelve `default` sin preguntar."""
    if _AUTO_MODE:
        return default
    return base.confirm(msg)


def _collect_project_context() -> str:
    """
    Recolecta contexto del proyecto para inyectarlo en los prompts de los agentes.
    Incluye: versión Python, sys.modules patches en conftest.py existentes,
    pyproject.toml y requirements-dev.txt si existen.
    """
    lines: List[str] = []

    # Versión de Python
    v = sys.version_info
    lines.append(f"Python: {v.major}.{v.minor}.{v.micro}")
    if v < (3, 10):
        lines.append(
            f"ADVERTENCIA Python {v.major}.{v.minor}: NO usar `X | Y` en type hints. "
            "Usar `Optional[X]` de typing o anadir `from __future__ import annotations`."
        )

    # conftest.py existentes — extraer sys.modules patches
    try:
        conftest_files = sorted(Path(".").rglob("conftest.py"))
    except OSError:
        conftest_files = []

    if conftest_files:
        lines.append(f"\nconftest.py existentes ({len(conftest_files)}):")
        for cf in conftest_files:
            try:
                content = cf.read_text()
                patches = [
                    ln.strip() for ln in content.splitlines()
                    if "sys.modules" in ln and not ln.strip().startswith("#")
                ]
                if patches:
                    lines.append(f"  {cf}:")
                    for p in patches[:5]:
                        lines.append(f"    {p}")
            except OSError:
                pass

    # pyproject.toml
    if Path("pyproject.toml").exists():
        lines.append("\npyproject.toml: existe")

    # requirements-dev.txt
    reqs = Path("requirements-dev.txt")
    if reqs.exists():
        try:
            content = reqs.read_text().strip().replace("\n", "\n  ")
            lines.append(f"requirements-dev.txt:\n  {content}")
        except OSError:
            pass

    return "\n".join(lines)


# ─────────────────────────────────────────────
# Plugin / Hook System
# ─────────────────────────────────────────────

class HookRunner:
    """Discover and execute hooks from .claude-workflow-hooks.py file."""

    def __init__(self, repo_root: Path = None):
        """Initialize hook runner for a repository.

        Args:
            repo_root: Path to repository root. Defaults to current working directory.
        """
        self.repo_root = repo_root or Path.cwd()
        self.hooks_file = self.repo_root / ".claude-workflow-hooks.py"
        self.hooks: Dict[str, Callable] = {}
        self._logger = logging.getLogger(__name__)

    def load(self) -> bool:
        """Load hooks from .claude-workflow-hooks.py if it exists.

        Returns:
            True if hooks were loaded, False if file doesn't exist.
        """
        if not self.hooks_file.exists():
            return False

        try:
            spec = importlib.util.spec_from_file_location("hooks_module", self.hooks_file)
            if spec is None or spec.loader is None:
                return False

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Discover before_phase_N and after_phase_N functions
            for name in dir(module):
                if name.startswith(("before_phase_", "after_phase_")):
                    attr = getattr(module, name)
                    if callable(attr):
                        # Validate signature
                        sig = inspect.signature(attr)
                        if len(sig.parameters) in (0, 1, 2):  # ctx, ctx+result, or no args
                            self.hooks[name] = attr
                            self._logger.debug(f"Discovered hook: {name}")

            return len(self.hooks) > 0
        except Exception as e:
            self._logger.error(f"Failed to load hooks from {self.hooks_file}: {e}")
            return False

    def before(self, phase: int, ctx: Dict[str, Any]) -> bool:
        """Execute before_phase_N hook if it exists.

        Args:
            phase: Phase number (0-5)
            ctx: Context dict with task, branch, phase_name

        Returns:
            True if successful (or no hook exists), False if hook raised exception
        """
        hook_name = f"before_phase_{phase}"
        if hook_name not in self.hooks:
            return True

        try:
            hook = self.hooks[hook_name]
            sig = inspect.signature(hook)
            if len(sig.parameters) == 0:
                hook()
            else:
                hook(ctx)
            self._logger.debug(f"Hook {hook_name} executed successfully")
            return True
        except Exception as e:
            self._logger.error(f"Hook {hook_name} failed: {e}")
            return False

    def after(self, phase: int, ctx: Dict[str, Any], result: Any) -> bool:
        """Execute after_phase_N hook if it exists.

        Args:
            phase: Phase number (0-5)
            ctx: Context dict with task, branch, phase_name
            result: Result from the phase

        Returns:
            True if successful (or no hook exists), False if hook raised exception
        """
        hook_name = f"after_phase_{phase}"
        if hook_name not in self.hooks:
            return True

        try:
            hook = self.hooks[hook_name]
            sig = inspect.signature(hook)
            if len(sig.parameters) == 0:
                hook()
            elif len(sig.parameters) == 1:
                hook(ctx)
            else:
                hook(ctx, result)
            self._logger.debug(f"Hook {hook_name} executed successfully")
            return True
        except Exception as e:
            self._logger.error(f"Hook {hook_name} failed: {e}")
            return False


# ─────────────────────────────────────────────
# Prompt Loader System
# ─────────────────────────────────────────────

class PromptLoader:
    """Lee prompts personalizados desde .claude-workflow/prompts/*.md.
    Fallback a constantes del módulo si el directorio no existe o el archivo falta.
    """

    def __init__(self, repo_root: Path = None):
        self.repo_root = repo_root or Path.cwd()
        self.prompts_dir = self.repo_root / ".claude-workflow" / "prompts"
        self._custom: Dict[str, str] = {}
        self._logger = logging.getLogger(__name__)

    def load(self) -> int:
        """Carga todos los .md encontrados. Retorna cantidad de prompts cargados."""
        if not self.prompts_dir.exists():
            return 0
        loaded = 0
        # Se define después en _PROMPT_FILES
        try:
            for name, (filename, expected_placeholders) in _PROMPT_FILES.items():
                path = self.prompts_dir / filename
                if path.exists():
                    text = path.read_text(encoding="utf-8").strip()
                    if text:
                        self._validate(name, text, expected_placeholders)
                        self._custom[name] = text
                        loaded += 1
        except (NameError, TypeError):
            # _PROMPT_FILES no está definido aún (durante inicialización)
            return 0
        if loaded:
            self._logger.info(f"PromptLoader: {loaded} prompt(s) custom cargados desde {self.prompts_dir}")
        return loaded

    def _validate(self, name: str, template: str, expected: set) -> None:
        """Advierte si un prompt custom le faltan variables Jinja2 esperadas."""
        try:
            from jinja2 import Environment, meta
            env = Environment()
            ast = env.parse(template)
            actual = meta.find_undeclared_variables(ast)
        except Exception as e:
            self._logger.warning(f"PromptLoader: no se pudo parsear '{name}': {e}")
            return
        missing = expected - actual
        if missing:
            self._logger.warning(
                f"PromptLoader: '{name}' le faltan variables {missing} — "
                "puede causar TemplateError en ejecución"
            )

    def get(self, name: str) -> str:
        """Retorna prompt custom si existe, sino la constante del módulo."""
        if name in self._custom:
            return self._custom[name]
        # fallback a constante del módulo
        import claude_workflow.iterative as _m
        default = getattr(_m, name, None)
        if default is None:
            raise KeyError(f"PromptLoader: prompt desconocido '{name}'")
        return default


# ─────────────────────────────────────────────
# Enums y dataclasses
# ─────────────────────────────────────────────

class AgentRole(str, Enum):
    ANALYST    = "ANALYST"
    ARCHITECT  = "ARCHITECT"
    QA_PLANNER = "QA_PLANNER"
    SYNTHESIZER = "SYNTHESIZER"
    IMPLEMENTER = "IMPLEMENTER"
    TEST_WRITER = "TEST_WRITER"
    INTEGRATOR  = "INTEGRATOR"
    COMMITTER   = "COMMITTER"
    COORDINATOR = "COORDINATOR"


@dataclass
class AgentResult:
    role: Union[AgentRole, str]
    exit_code: int
    output: str
    session_id: Optional[str]
    duration_s: float
    error: Optional[str] = None
    tokens: dict = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and self.error is None


# ─────────────────────────────────────────────
# SessionStore
# ─────────────────────────────────────────────

class SessionStore:
    """Persiste session_ids de Claude CLI por rol en sessions.json."""

    def __init__(self, sessions_file: Path):
        self._file = sessions_file
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if self._file.exists():
            try:
                return json.loads(self._file.read_text())
            except json.JSONDecodeError:
                return {}
        return {}

    def save(self, role: AgentRole, session_id: str) -> None:
        with self._lock:
            data = self._read()
            data[role.value] = session_id
            data["_updated"] = datetime.now().isoformat()
            self._file.write_text(json.dumps(data, indent=2))

    def load(self, role: AgentRole) -> Optional[str]:
        return self._read().get(role.value)

    def save_dev(self, index: int, session_id: str) -> None:
        """Guarda session_id para DEV_N usando clave 'DEV_{index}'."""
        with self._lock:
            data = self._read()
            data[f"DEV_{index}"] = session_id
            data["_updated"] = datetime.now().isoformat()
            self._file.write_text(json.dumps(data, indent=2))

    def load_dev(self, index: int) -> Optional[str]:
        """Carga session_id de DEV_N."""
        return self._read().get(f"DEV_{index}")

    def load_all(self) -> dict:
        return self._read()

    def clear(self) -> None:
        with self._lock:
            self._file.write_text("{}")


# ─────────────────────────────────────────────
# TokenStore
# ─────────────────────────────────────────────

class TokenStore:
    """Persiste usage de tokens por rol en tokens.json (thread-safe)."""

    _KEYS = ("input", "output", "cache_read", "cache_write", "cost_usd")

    def __init__(self, tokens_file: Path):
        self._file = tokens_file
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if self._file.exists():
            try:
                return json.loads(self._file.read_text())
            except json.JSONDecodeError:
                return {}
        return {}

    def _zero(self) -> dict:
        return {k: 0 for k in self._KEYS}

    def add(self, role: str, usage: dict) -> None:
        """Acumula usage para un rol y actualiza _total."""
        if not usage:
            return
        with self._lock:
            data = self._read()
            entry = data.setdefault(role, self._zero())
            total = data.setdefault("_total", self._zero())
            for k in self._KEYS:
                v = usage.get(k, 0)
                entry[k] = entry.get(k, 0) + v
                total[k] = total.get(k, 0) + v
            data["_updated"] = datetime.now().isoformat()
            self._file.write_text(json.dumps(data, indent=2))

    def total(self) -> dict:
        return self._read().get("_total", self._zero())

    def load_all(self) -> dict:
        return self._read()


# ─────────────────────────────────────────────
# ParallelRunner
# ─────────────────────────────────────────────

class ParallelRunner:
    """Ejecuta tareas de agentes en paralelo con reintentos y timeout."""

    def __init__(self, max_workers: int = 3, timeout_s: int = 300, max_retries: int = 2):
        self.max_workers = max_workers
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    def run_parallel(
        self,
        tasks: List[Tuple[Union[AgentRole, str], Callable[[], AgentResult]]],
    ) -> Dict[Union[AgentRole, str], AgentResult]:
        results: Dict[Union[AgentRole, str], AgentResult] = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_role = {
                executor.submit(self._run_with_retry, role, fn): role
                for role, fn in tasks
            }
            try:
                for future in concurrent.futures.as_completed(
                    future_to_role, timeout=self.timeout_s
                ):
                    role = future_to_role[future]
                    try:
                        results[role] = future.result()
                    except Exception as exc:  # noqa: BLE001
                        results[role] = AgentResult(
                            role=role, exit_code=1, output="",
                            session_id=None, duration_s=0.0,
                            error=str(exc)
                        )
            except concurrent.futures.TimeoutError:
                for future, role in future_to_role.items():
                    if role in results:
                        continue
                    if future.done():
                        try:
                            results[role] = future.result()
                        except Exception as exc:  # noqa: BLE001
                            results[role] = AgentResult(
                                role=role, exit_code=1, output="",
                                session_id=None, duration_s=0.0,
                                error=str(exc)
                            )
                    else:
                        future.cancel()
                        results[role] = AgentResult(
                            role=role, exit_code=1, output="",
                            session_id=None, duration_s=float(self.timeout_s),
                            error="timeout"
                        )

        return results

    def _run_with_retry(
        self,
        role: Union[AgentRole, str],
        fn: Callable[[], AgentResult],
    ) -> AgentResult:
        last_result: Optional[AgentResult] = None
        for attempt in range(self.max_retries + 1):
            try:
                result = fn()
                if result.success:
                    return result
                last_result = result
            except Exception as exc:  # noqa: BLE001
                last_result = AgentResult(
                    role=role, exit_code=1, output="",
                    session_id=None, duration_s=0.0,
                    error=str(exc)
                )
            if attempt < self.max_retries:
                time.sleep(1)

        return last_result  # type: ignore[return-value]


# ─────────────────────────────────────────────
# CheckpointGate
# ─────────────────────────────────────────────

class CheckpointGate:
    """Pausa de revisión humana entre fases."""

    def __init__(self, auto_mode: bool = False):
        self.auto = auto_mode

    def wait(self, phase_name: str, summary: str) -> bool:
        if self.auto:
            return True
        print(f"\n{'─'*60}")
        print(f"  CHECKPOINT: {phase_name}")
        print(f"{'─'*60}")
        print(summary)
        return base.confirm(f"¿Continuar con '{phase_name}'?")


# ─────────────────────────────────────────────
# Claude helper con session_id (using cli_backend)
# ─────────────────────────────────────────────

# Global backend instance for iterative (lazy initialized)
_iterative_backend: Optional[CLIBackend] = None


def _get_iterative_backend() -> CLIBackend:
    """Obtiene el backend CLI para iterative (con fallback automático)."""
    global _iterative_backend
    if _iterative_backend is None:
        _iterative_backend = get_default_backend()
    return _iterative_backend


def claude_p_with_session(
    prompt: str,
    flags: Optional[List[str]] = None,
) -> Tuple[int, str, Optional[str], dict]:
    """
    Corre CLI con fallback automático.
    Retorna (exit_code, text_output, session_id | None, usage_dict).
    """
    backend = _get_iterative_backend()
    result = backend.execute(prompt, flags)

    return result.exit_code, result.text, result.session_id, result.usage


def make_session_id(task: str, ts: Optional[str] = None) -> str:
    """Genera un ID de sesión legible: sess_YYYYMMDD-{slug}."""
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")[:30]
    ts = ts or datetime.now().strftime("%Y%m%d")
    return f"sess_{ts}-{slug}"


# ─────────────────────────────────────────────
# Directorios y archivos compartidos
# ─────────────────────────────────────────────

def _init_agents_dir(agents_dir: Path, task: str) -> None:
    """Crea la estructura de directorios para los agentes."""
    for sub in ("analysis", "implementation", "integration", "commit"):
        (agents_dir / sub).mkdir(parents=True, exist_ok=True)
    task_file = agents_dir / "task.txt"
    if not task_file.exists():
        task_file.write_text(task)
    tokens_file = agents_dir / "tokens.json"
    if not tokens_file.exists():
        tokens_file.write_text("{}")


def init_prompts_dir(repo_root: Path = None, force: bool = False) -> None:
    """Crea o actualiza .claude-workflow/prompts/ con un .md por agente (prompts defaults).

    Args:
        repo_root: Raíz del repositorio (default: cwd)
        force: Si True, sobreescribe archivos existentes. Si False, los deja intactos.
    """
    root = repo_root or Path.cwd()
    prompts_dir = root / _PROMPTS_DIR
    prompts_dir.mkdir(parents=True, exist_ok=True)

    import claude_workflow.iterative as _m

    created = []
    skipped = []
    updated = []
    for const_name, (filename, placeholders) in _PROMPT_FILES.items():
        dest = prompts_dir / filename
        default_text = getattr(_m, const_name, "")

        if dest.exists() and not force:
            skipped.append(filename)
            continue

        dest.write_text(default_text.strip() + "\n", encoding="utf-8")
        if dest.exists() and force:
            updated.append(filename)
        else:
            created.append(filename)

    # README con tabla de variables (usar sintaxis Jinja2)
    readme = prompts_dir / "README.md"
    if not readme.exists() or force:
        lines = [
            "# Prompts de claude-workflow\n\n",
            "Edita cada archivo `.md` para personalizar el prompt del agente correspondiente.\n\n",
            "**Importante:** Conserva todas las variables Jinja2 `{{ variable }}` listadas a continuación. "
            "Si falta alguna, se emitirá una advertencia pero el prompt seguirá cargándose.\n\n",
            "Para actualizar los prompts a la última versión del módulo, ejecuta: `claude-iterative --update-prompts`\n\n",
            "## Variables por archivo\n\n",
            "| Archivo | Variables requeridas |\n",
            "|---|---|\n",
        ]
        for _, (filename, phs) in _PROMPT_FILES.items():
            ph_str = ", ".join(f"`{{{{{p}}}}}`" for p in sorted(phs))
            lines.append(f"| `{filename}` | {ph_str} |\n")
        readme.write_text("".join(lines), encoding="utf-8")
        if "README.md" not in created:
            created.append("README.md")

    print(f"✓ Directorio: {prompts_dir}")
    if created:
        print(f"  Creados: {', '.join(created)}")
    if updated:
        print(f"  Actualizados: {', '.join(updated)}")
    if skipped:
        print(f"  Existentes (no modificados): {', '.join(skipped)}")
    print("\nEdita los .md para personalizar los prompts. Los cambios aplican en el próximo run.\n")


# ─────────────────────────────────────────────
# Fase 0: Branch
# ─────────────────────────────────────────────

def phase0_branch(branch: str) -> bool:
    print(f"\n{'═'*60}")
    print("  FASE 0: Crear branch")
    print(f"{'═'*60}")
    return base.create_branch(branch)


# ─────────────────────────────────────────────
# Fase 1: Análisis paralelo
# ─────────────────────────────────────────────

ANALYST_PROMPT = """
Eres un analista de software senior. Analiza el proyecto actual y la tarea:

TAREA: {{ task }}

Lee el código fuente, identifica los módulos relevantes y escribe tu análisis en {{ output }}.

Cubre:
1. Módulos afectados (rutas exactas)
2. Dependencias externas involucradas
3. Impacto estimado del cambio
4. Riesgos detectados

Solo análisis. No implementes nada.
"""

ARCHITECT_PROMPT = """
Eres un arquitecto de software. Analiza el proyecto y la tarea:

TAREA: {{ task }}

Lee el código, diseña la arquitectura del cambio y escribe en {{ output }}.

Incluye:
1. Patrón de diseño recomendado
2. Estructura de clases/módulos a crear/modificar
3. Interfaz pública (firmas de funciones/métodos)
4. Decisiones de diseño y trade-offs

Solo arquitectura. No implementes nada.
"""

QA_PLANNER_PROMPT = """
Eres un QA Engineer senior. Analiza los tests existentes y la tarea:

TAREA: {{ task }}

Lee los tests actuales y escribe tu plan de QA en {{ output }}.

Incluye:
1. Tests existentes relevantes
2. Nuevos casos de prueba necesarios (unitarios + integración)
3. Casos edge y errores esperados
4. Estrategia de mocking

Solo planificación. No escribas tests todavía.
"""


def _prepend_context(prompt: str, project_ctx: str) -> str:
    """Prepende el contexto del proyecto al prompt si no está vacío."""
    if not project_ctx:
        return prompt
    return f"## Contexto del Proyecto\n{project_ctx}\n\n---\n{prompt}"


def _get_prompt(name: str, loader: Optional["PromptLoader"]) -> str:
    """Retorna prompt del loader si existe, sino la constante del módulo."""
    if loader is not None:
        return loader.get(name)
    import claude_workflow.iterative as _m
    return getattr(_m, name)


def _render_prompt(template_str: str, **kwargs) -> str:
    """Renderiza una plantilla Jinja2 con parámetros seguros."""
    template = Template(template_str)
    return template.render(**kwargs)


def _run_analyst(task: str, agents_dir: Path, resume_id: Optional[str] = None,
                 project_ctx: str = "",
                 token_store: Optional["TokenStore"] = None,
                 prompt_loader: Optional["PromptLoader"] = None) -> AgentResult:
    output_file = agents_dir / "analysis" / "ANALYST.md"
    flags = [
        "--allowedTools", "Read,Grep,Glob,Write",
        "--max-turns", "15",
    ]
    if resume_id:
        flags += ["--resume", resume_id]
    t0 = time.monotonic()
    code, text, sid, usage = claude_p_with_session(
        _prepend_context(_render_prompt(_get_prompt("ANALYST_PROMPT", prompt_loader), task=task, output=output_file), project_ctx),
        flags=flags,
    )
    if token_store:
        token_store.add(AgentRole.ANALYST.value, usage)
    return AgentResult(
        role=AgentRole.ANALYST,
        exit_code=code,
        output=text,
        session_id=sid,
        duration_s=time.monotonic() - t0,
        tokens=usage,
    )


def _run_architect(task: str, agents_dir: Path, resume_id: Optional[str] = None,
                   project_ctx: str = "",
                   token_store: Optional["TokenStore"] = None,
                   prompt_loader: Optional["PromptLoader"] = None) -> AgentResult:
    output_file = agents_dir / "analysis" / "ARCHITECT.md"
    flags = [
        "--allowedTools", "Read,Grep,Glob,Write",
        "--max-turns", "15",
    ]
    if resume_id:
        flags += ["--resume", resume_id]
    t0 = time.monotonic()
    code, text, sid, usage = claude_p_with_session(
        _prepend_context(_render_prompt(_get_prompt("ARCHITECT_PROMPT", prompt_loader), task=task, output=output_file), project_ctx),
        flags=flags,
    )
    if token_store:
        token_store.add(AgentRole.ARCHITECT.value, usage)
    return AgentResult(
        role=AgentRole.ARCHITECT,
        exit_code=code,
        output=text,
        session_id=sid,
        duration_s=time.monotonic() - t0,
        tokens=usage,
    )


def _run_qa_planner(task: str, agents_dir: Path, resume_id: Optional[str] = None,
                    project_ctx: str = "",
                    token_store: Optional["TokenStore"] = None,
                    prompt_loader: Optional["PromptLoader"] = None) -> AgentResult:
    output_file = agents_dir / "analysis" / "QA_PLANNER.md"
    flags = [
        "--allowedTools", "Read,Grep,Glob,Write",
        "--max-turns", "12",
    ]
    if resume_id:
        flags += ["--resume", resume_id]
    t0 = time.monotonic()
    ctx_note = (
        project_ctx
        + "\nAl planificar mocks, considera los sys.modules patches existentes en conftest.py."
        if project_ctx else ""
    )
    code, text, sid, usage = claude_p_with_session(
        _prepend_context(_render_prompt(_get_prompt("QA_PLANNER_PROMPT", prompt_loader), task=task, output=output_file), ctx_note),
        flags=flags,
    )
    if token_store:
        token_store.add(AgentRole.QA_PLANNER.value, usage)
    return AgentResult(
        role=AgentRole.QA_PLANNER,
        exit_code=code,
        output=text,
        session_id=sid,
        duration_s=time.monotonic() - t0,
        tokens=usage,
    )


def phase1_analysis(
    task: str,
    agents_dir: Path,
    store: SessionStore,
    runner: ParallelRunner,
    skip_phases: List[int],
    resume: bool = False,
    token_store: Optional["TokenStore"] = None,
    prompt_loader: Optional["PromptLoader"] = None,
) -> Dict[AgentRole, AgentResult]:
    if 1 in skip_phases:
        print("\n⏭  Saltando Fase 1 (--skip-phase 1)")
        return {}

    print(f"\n{'═'*60}")
    print("  FASE 1: Análisis paralelo (3 agentes)")
    print(f"{'═'*60}")

    project_ctx = _collect_project_context()

    tasks = [
        (AgentRole.ANALYST,    lambda: _run_analyst(
            task, agents_dir,
            store.load(AgentRole.ANALYST) if resume else None,
            project_ctx=project_ctx,
            token_store=token_store,
            prompt_loader=prompt_loader,
        )),
        (AgentRole.ARCHITECT,  lambda: _run_architect(
            task, agents_dir,
            store.load(AgentRole.ARCHITECT) if resume else None,
            project_ctx=project_ctx,
            token_store=token_store,
            prompt_loader=prompt_loader,
        )),
        (AgentRole.QA_PLANNER, lambda: _run_qa_planner(
            task, agents_dir,
            store.load(AgentRole.QA_PLANNER) if resume else None,
            project_ctx=project_ctx,
            token_store=token_store,
            prompt_loader=prompt_loader,
        )),
    ]

    results = runner.run_parallel(tasks)

    for role, result in results.items():
        mark = "✅" if result.success else "❌"
        print(f"  {mark} {role.value} — {result.duration_s:.1f}s")
        if result.session_id:
            store.save(role, result.session_id)

    return results


# ─────────────────────────────────────────────
# Fase 2: Síntesis
# ─────────────────────────────────────────────

SYNTHESIZER_PROMPT = """
Eres un tech lead. Sintetiza los análisis de tus colegas y genera un plan unificado.

TAREA: {{ task }}

Lee los análisis en:
- {{ analyst_file }}
- {{ architect_file }}
- {{ qa_file }}

Genera un plan de implementación en {{ plan_file }} con este formato:

# Plan: {{ task }}

## Objetivo
<una línea clara>

## Módulos a modificar
- `ruta/archivo.py`: descripción del cambio

## Pasos de implementación
### Paso N: <nombre>
- **Qué hace**: ...
- **Archivos**: `ruta/archivo.py`
- **Validación**: `<comando shell>`

## Plan de tests
- Unitarios: ...
- Integración: ...
- Mocks: ...

## Riesgos
- ...

## Criterios de Aceptación
- [ ] Coverage > {{ coverage }}%
- [ ] Sin errores de lint
- [ ] <criterio de la tarea>
"""


def phase2_synthesize(
    task: str,
    agents_dir: Path,
    store: SessionStore,
    skip_phases: List[int],
    coverage: int,
    resume: bool = False,
    project_ctx: str = "",
    token_store: Optional["TokenStore"] = None,
    prompt_loader: Optional["PromptLoader"] = None,
) -> bool:
    if 2 in skip_phases:
        print("\n⏭  Saltando Fase 2 (--skip-phase 2)")
        return True

    print(f"\n{'═'*60}")
    print("  FASE 2: Síntesis del plan")
    print(f"{'═'*60}")

    plan_file = agents_dir / "PLAN.md"
    flags = [
        "--allowedTools", "Read,Write",
        "--max-turns", "12",
    ]
    resume_id = store.load(AgentRole.SYNTHESIZER) if resume else None
    if resume_id:
        flags += ["--resume", resume_id]

    t0 = time.monotonic()
    code, text, sid, usage = claude_p_with_session(
        _prepend_context(
            _render_prompt(_get_prompt("SYNTHESIZER_PROMPT", prompt_loader),
                task=task,
                analyst_file=agents_dir / "analysis" / "ANALYST.md",
                architect_file=agents_dir / "analysis" / "ARCHITECT.md",
                qa_file=agents_dir / "analysis" / "QA_PLANNER.md",
                plan_file=plan_file,
                coverage=coverage,
            ),
            project_ctx,
        ),
        flags=flags,
    )
    duration = time.monotonic() - t0

    if sid:
        store.save(AgentRole.SYNTHESIZER, sid)
    if token_store:
        token_store.add(AgentRole.SYNTHESIZER.value, usage)

    mark = "✅" if code == 0 else "❌"
    print(f"  {mark} SYNTHESIZER — {duration:.1f}s")

    if code != 0:
        print("❌  Error generando plan unificado")
        return False

    if plan_file.exists():
        print(f"\n{plan_file.read_text()}\n")

    return True


# ─────────────────────────────────────────────
# Fase 3: Implementación
# ─────────────────────────────────────────────

IMPLEMENTER_PROMPT = """
Eres un desarrollador senior. Implementa el plan en {{ plan_file }}.

TAREA: {{ task }}

Lee también la arquitectura en {{ architect_file }}.

Reglas:
1. Sigue el orden exacto del plan
2. Implementa SOLO el código fuente (no tests)
3. Tras cada paso, ejecuta su validación
4. Escribe código limpio con type hints
5. Documenta en {{ log_file }} los cambios realizados
6. Si el entorno usa Python < 3.10, usa `from __future__ import annotations` en lugar
   de `X | Y` en type hints (o usa `Optional[X]` de typing)

No implementes los tests — ese es el rol de TEST_WRITER.
"""

TEST_WRITER_PROMPT = """
Eres un QA Engineer senior. Escribe los tests del plan.

TAREA: {{ task }}

Lee:
- Plan: {{ plan_file }}
- Plan QA: {{ qa_file }}
- Log del implementador: {{ impl_log }}

Reglas:
1. Detecta el tipo de proyecto:
   - Si existe package.json: escribe tests Jest (archivos *.test.js en tests/)
   - Si no: escribe tests pytest (archivos test_*.py en tests/)
2. Cubre happy path, edge cases y errores esperados
3. Sigue el plan de QA exactamente
4. Documenta en {{ log_file }} los tests creados
5. Alcanza coverage > {{ coverage }}%
6. Verifica ejecutando:
   - Node.js: npx jest --coverage --coverageReporters=text --forceExit
   - Python: pytest tests/ --cov=. --cov-report=term-missing -v
"""


def _run_implementer(
    task: str,
    agents_dir: Path,
    coverage: int,
    resume_id: Optional[str] = None,
    project_ctx: str = "",
    token_store: Optional["TokenStore"] = None,
    prompt_loader: Optional["PromptLoader"] = None,
) -> AgentResult:
    log_file = agents_dir / "implementation" / "IMPLEMENTER.md"
    flags = ["--dangerously-skip-permissions", "--max-turns", "40"]
    if resume_id:
        flags += ["--resume", resume_id]
    t0 = time.monotonic()
    code, text, sid, usage = claude_p_with_session(
        _prepend_context(
            _render_prompt(_get_prompt("IMPLEMENTER_PROMPT", prompt_loader),
                task=task,
                plan_file=agents_dir / "PLAN.md",
                architect_file=agents_dir / "analysis" / "ARCHITECT.md",
                log_file=log_file,
            ),
            project_ctx,
        ),
        flags=flags,
    )
    if token_store:
        token_store.add(AgentRole.IMPLEMENTER.value, usage)
    return AgentResult(
        role=AgentRole.IMPLEMENTER,
        exit_code=code,
        output=text,
        session_id=sid,
        duration_s=time.monotonic() - t0,
        tokens=usage,
    )


TEST_WRITER_PROMPT_MULTI = """
Eres un QA Engineer senior. Escribe los tests del plan.

TAREA: {{ task }}

Lee:
- Plan: {{ plan_file }}
- Plan QA: {{ qa_file }}
- Logs de implementadores:
{{ impl_logs }}

Reglas:
1. Detecta el tipo de proyecto:
   - Si existe package.json: escribe tests Jest (archivos *.test.js en tests/)
   - Si no: escribe tests pytest (archivos test_*.py en tests/)
2. Cubre happy path, edge cases y errores esperados
3. Sigue el plan de QA exactamente
4. Documenta en {{ log_file }} los tests creados
5. Alcanza coverage > {{ coverage }}%
6. Verifica ejecutando:
   - Node.js: npx jest --coverage --coverageReporters=text --forceExit
   - Python: pytest tests/ --cov=. --cov-report=term-missing -v
"""

COORDINATOR_PROMPT = """
Eres un Tech Lead coordinador. Divide el plan de implementación en {{ n_agents }} sub-tareas independientes.

TAREA: {{ task }}

Lee el plan en {{ plan_file }}.

Para cada sub-tarea i (1..{{ n_agents }}), escribe el archivo {{ tasks_dir }}/DEV_{i}.md con:

## Sub-tarea {i}/{{ n_agents }}

### Módulos a implementar
- `ruta/archivo.py`: descripción exacta del cambio

### Pasos
1. ...

### Validación
`<comando>`

### No tocar (reservado para otros Dev agents)
- `ruta/otro_archivo.py`

Reglas de división:
1. Cada sub-tarea DEBE ser completamente independiente (sin conflictos de archivo)
2. Cubre TODO el plan sin superposición
3. Especifica explícitamente qué archivos NO tocar en cada sub-tarea

Escribe tu log de coordinación en {{ log_file }}.
"""

DEV_AGENT_PROMPT = """
Eres un desarrollador senior. Implementa EXCLUSIVAMENTE la sub-tarea asignada.

TAREA GLOBAL: {{ task }}
TU SUB-TAREA: {{ task_file }} (Dev agent {{ index }}/{{ n_agents }})

Lee también la arquitectura en {{ architect_file }}.

Reglas:
1. Implementa SOLO los módulos listados en tu sub-tarea
2. NO toques los archivos marcados como "No tocar" en tu sub-tarea
3. Implementa SOLO código fuente (no tests)
4. Tras cada paso ejecuta su validación
5. Escribe type hints y docstrings
6. Documenta en {{ log_file }} los cambios realizados con rutas exactas
7. Si el entorno usa Python < 3.10, usa `from __future__ import annotations` en lugar
   de `X | Y` en type hints (o usa `Optional[X]` de typing)
"""


def _run_test_writer(
    task: str,
    agents_dir: Path,
    coverage: int,
    resume_id: Optional[str] = None,
    dev_log_files: Optional[List[Path]] = None,
    project_ctx: str = "",
    token_store: Optional["TokenStore"] = None,
    prompt_loader: Optional["PromptLoader"] = None,
) -> AgentResult:
    log_file = agents_dir / "implementation" / "TEST_WRITER.md"
    flags = ["--dangerously-skip-permissions", "--max-turns", "30"]
    if resume_id:
        flags += ["--resume", resume_id]
    t0 = time.monotonic()

    if dev_log_files:
        impl_logs_str = "\n".join(f"  - {p}" for p in dev_log_files)
        prompt = _render_prompt(_get_prompt("TEST_WRITER_PROMPT_MULTI", prompt_loader),
            task=task,
            plan_file=agents_dir / "PLAN.md",
            qa_file=agents_dir / "analysis" / "QA_PLANNER.md",
            impl_logs=impl_logs_str,
            log_file=log_file,
            coverage=coverage,
        )
    else:
        prompt = _render_prompt(_get_prompt("TEST_WRITER_PROMPT", prompt_loader),
            task=task,
            plan_file=agents_dir / "PLAN.md",
            qa_file=agents_dir / "analysis" / "QA_PLANNER.md",
            impl_log=agents_dir / "implementation" / "IMPLEMENTER.md",
            log_file=log_file,
            coverage=coverage,
        )

    code, text, sid, usage = claude_p_with_session(_prepend_context(prompt, project_ctx), flags=flags)
    if token_store:
        token_store.add(AgentRole.TEST_WRITER.value, usage)
    return AgentResult(
        role=AgentRole.TEST_WRITER,
        exit_code=code,
        output=text,
        session_id=sid,
        duration_s=time.monotonic() - t0,
        tokens=usage,
    )


def _run_coordinator(
    task: str,
    agents_dir: Path,
    n_agents: int,
    resume_id: Optional[str] = None,
    token_store: Optional["TokenStore"] = None,
    prompt_loader: Optional["PromptLoader"] = None,
) -> AgentResult:
    tasks_dir = agents_dir / "implementation" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    log_file = agents_dir / "implementation" / "COORDINATOR.md"
    flags = ["--dangerously-skip-permissions", "--max-turns", "20"]
    if resume_id:
        flags += ["--resume", resume_id]
    t0 = time.monotonic()
    code, text, sid, usage = claude_p_with_session(
        _render_prompt(_get_prompt("COORDINATOR_PROMPT", prompt_loader),
            task=task,
            n_agents=n_agents,
            plan_file=agents_dir / "PLAN.md",
            tasks_dir=tasks_dir,
            log_file=log_file,
        ),
        flags=flags,
    )
    if token_store:
        token_store.add(AgentRole.COORDINATOR.value, usage)
    return AgentResult(
        role=AgentRole.COORDINATOR,
        exit_code=code,
        output=text,
        session_id=sid,
        duration_s=time.monotonic() - t0,
        tokens=usage,
    )


def _run_dev_agent(
    task: str,
    agents_dir: Path,
    index: int,
    n_agents: int,
    resume_id: Optional[str] = None,
    project_ctx: str = "",
    token_store: Optional["TokenStore"] = None,
    prompt_loader: Optional["PromptLoader"] = None,
) -> AgentResult:
    task_file = agents_dir / "implementation" / "tasks" / f"DEV_{index}.md"
    log_file = agents_dir / "implementation" / f"DEV_{index}.md"
    flags = ["--dangerously-skip-permissions", "--max-turns", "40"]
    if resume_id:
        flags += ["--resume", resume_id]
    t0 = time.monotonic()
    code, text, sid, usage = claude_p_with_session(
        _prepend_context(
            _render_prompt(_get_prompt("DEV_AGENT_PROMPT", prompt_loader),
                task=task,
                task_file=task_file,
                index=index,
                n_agents=n_agents,
                architect_file=agents_dir / "analysis" / "ARCHITECT.md",
                log_file=log_file,
            ),
            project_ctx,
        ),
        flags=flags,
    )
    if token_store:
        token_store.add(f"DEV_{index}", usage)
    return AgentResult(
        role=f"DEV_{index}",
        exit_code=code,
        output=text,
        session_id=sid,
        duration_s=time.monotonic() - t0,
        tokens=usage,
    )


def phase3_implement(
    task: str,
    agents_dir: Path,
    store: SessionStore,
    runner: ParallelRunner,
    skip_phases: List[int],
    coverage: int,
    parallel_impl: bool = False,
    resume: bool = False,
    dev_agents: int = 1,
    project_ctx: str = "",
    token_store: Optional["TokenStore"] = None,
    prompt_loader: Optional["PromptLoader"] = None,
) -> bool:
    if 3 in skip_phases:
        print("\n⏭  Saltando Fase 3 (--skip-phase 3)")
        return True

    print(f"\n{'═'*60}")
    if dev_agents > 1:
        print(f"  FASE 3: Implementación ({dev_agents} Dev agents en paralelo)")
    else:
        mode = "paralela" if parallel_impl else "secuencial"
        print(f"  FASE 3: Implementación ({mode})")
    print(f"{'═'*60}")

    if dev_agents > 1:
        # ── Rama N Dev agents ──
        # 3a. COORDINATOR (secuencial)
        coord_resume = store.load(AgentRole.COORDINATOR) if resume else None
        coord_result = _run_coordinator(task, agents_dir, dev_agents, coord_resume, token_store, prompt_loader)
        mark = "✅" if coord_result.success else "❌"
        print(f"  {mark} COORDINATOR — {coord_result.duration_s:.1f}s")
        if coord_result.session_id:
            store.save(AgentRole.COORDINATOR, coord_result.session_id)
        if not coord_result.success:
            return False

        # 3b. N DEV agents en paralelo
        dev_tasks = []
        for i in range(1, dev_agents + 1):
            dev_tasks.append((
                f"DEV_{i}",
                lambda i=i: _run_dev_agent(
                    task, agents_dir, i, dev_agents,
                    store.load_dev(i) if resume else None,
                    project_ctx=project_ctx,
                    token_store=token_store,
                    prompt_loader=prompt_loader,
                ),
            ))
        dev_results = runner.run_parallel(dev_tasks)

        all_ok = True
        dev_log_files: List[Path] = []
        for role_str, result in dev_results.items():
            mark = "✅" if result.success else "❌"
            print(f"  {mark} {role_str} — {result.duration_s:.1f}s")
            if result.session_id:
                idx = int(str(role_str).split("_")[1])
                store.save_dev(idx, result.session_id)
            if not result.success:
                all_ok = False
            dev_log_files.append(agents_dir / "implementation" / f"{role_str}.md")

        if not all_ok:
            return False

        # 3c. TEST_WRITER (espera a todos los DEVs)
        tw_resume = store.load(AgentRole.TEST_WRITER) if resume else None
        tw_result = _run_test_writer(task, agents_dir, coverage, tw_resume, dev_log_files, project_ctx, token_store, prompt_loader)
        mark = "✅" if tw_result.success else "❌"
        print(f"  {mark} TEST_WRITER — {tw_result.duration_s:.1f}s")
        if tw_result.session_id:
            store.save(AgentRole.TEST_WRITER, tw_result.session_id)
        return tw_result.success

    else:
        # ── Rama original N=1 ──
        impl_resume = store.load(AgentRole.IMPLEMENTER) if resume else None
        tw_resume = store.load(AgentRole.TEST_WRITER) if resume else None

        if parallel_impl:
            tasks = [
                (AgentRole.IMPLEMENTER, lambda: _run_implementer(
                    task, agents_dir, coverage, impl_resume, project_ctx, token_store, prompt_loader)),
                (AgentRole.TEST_WRITER, lambda: _run_test_writer(
                    task, agents_dir, coverage, tw_resume, None, project_ctx, token_store, prompt_loader)),
            ]
            results = runner.run_parallel(tasks)
        else:
            impl_result = _run_implementer(task, agents_dir, coverage, impl_resume, project_ctx, token_store, prompt_loader)
            tw_result = _run_test_writer(task, agents_dir, coverage, tw_resume, None, project_ctx, token_store, prompt_loader)
            results = {
                AgentRole.IMPLEMENTER: impl_result,
                AgentRole.TEST_WRITER: tw_result,
            }

        implementer_ok = True
        for role, result in results.items():
            mark = "✅" if result.success else "❌"
            role_name = role.value if hasattr(role, "value") else str(role)
            print(f"  {mark} {role_name} — {result.duration_s:.1f}s")
            if result.session_id:
                store.save(role, result.session_id)  # type: ignore[arg-type]
            # Only IMPLEMENTER failure blocks Phase 4 — TEST_WRITER failure is
            # recoverable: INTEGRATOR runs tests and fills coverage gaps.
            if role == AgentRole.IMPLEMENTER and not result.success:
                implementer_ok = False
            elif role != AgentRole.IMPLEMENTER and not result.success:
                print(f"  ⚠  {role_name} falló — INTEGRATOR cubrirá los tests faltantes")

        return implementer_ok


# ─────────────────────────────────────────────
# Fase 4: Integración
# ─────────────────────────────────────────────

INTEGRATOR_PROMPT = """
Eres un integration engineer. Asegura que todo funcione junto.

TAREA: {{ task }}

Lee todos los logs en {{ agents_dir }}/ y el código implementado.

DIRECTORIO DE TRABAJO: {{ backend_dir }}
Tests deben estar en: tests/

Pasos:
1. Navega a {{ backend_dir }}
2. Ejecuta los tests con cobertura:
   - Si Node.js (package.json existe): npx jest --coverage --coverageReporters=text --forceExit
   - Si Python: pytest tests/ --cov=. --cov-report=term-missing -v
3. Si hay fallos, corrígelos
4. Si coverage < {{ coverage }}%, agrega más tests en tests/
5. Repite hasta pasar o agotar {{ max_attempts }} intentos
6. Documenta resultados en {{ log_file }}

Objetivo: Tests pasan con coverage >= {{ coverage }}%
"""


def phase4_integrate(
    task: str,
    agents_dir: Path,
    store: SessionStore,
    skip_phases: List[int],
    coverage: int,
    resume: bool = False,
    token_store: Optional["TokenStore"] = None,
    prompt_loader: Optional["PromptLoader"] = None,
    backend_dir: Optional[Path] = None,
) -> Tuple[bool, float]:
    if 4 in skip_phases:
        print("\n⏭  Saltando Fase 4 (--skip-phase 4)")
        return True, 0.0

    print(f"\n{'═'*60}")
    print(f"  FASE 4: Integración (coverage objetivo: {coverage}%)")
    print(f"{'═'*60}")

    log_file = agents_dir / "integration" / "INTEGRATOR.md"
    flags = ["--dangerously-skip-permissions", "--max-turns", "30"]
    resume_id = store.load(AgentRole.INTEGRATOR) if resume else None
    if resume_id:
        flags += ["--resume", resume_id]

    if backend_dir is None:
        backend_dir = base._detect_test_dir(agents_dir.parent)
    t0 = time.monotonic()
    code, text, sid, usage = claude_p_with_session(
        _render_prompt(_get_prompt("INTEGRATOR_PROMPT", prompt_loader),
            task=task,
            agents_dir=agents_dir,
            backend_dir=backend_dir,
            coverage=coverage,
            max_attempts=3,
            log_file=log_file,
        ),
        flags=flags,
    )
    duration = time.monotonic() - t0

    if sid:
        store.save(AgentRole.INTEGRATOR, sid)
    if token_store:
        token_store.add(AgentRole.INTEGRATOR.value, usage)

    mark = "✅" if code == 0 else "❌"
    print(f"  {mark} INTEGRATOR — {duration:.1f}s")

    # Medir coverage real
    _, cov_pct, test_output = base.run_tests(cwd=backend_dir)
    print(f"  📈 Coverage: {cov_pct:.1f}%")

    if cov_pct == 0.0:
        print(f"  ⚠  Coverage 0% — diagnostico del test runner:")
        print(f"  Test dir: {backend_dir}")
        # Mostrar ultimas lineas del output para diagnostico
        for line in test_output.splitlines()[-30:]:
            print(f"    {line}")

    return code == 0, cov_pct


# ─────────────────────────────────────────────
# Fase 5: Commit
# ─────────────────────────────────────────────

COMMITTER_PROMPT = """
Genera un mensaje de commit Conventional Commits resumido (máximo 150 palabras).

TAREA: {{ task }}
BRANCH: {{ branch }}
COVERAGE: {{ coverage }}%

Lee el plan en {{ plan_file }} y el reporte de integración en {{ integrator_log }}.
Usa esa información SOLO para contexto interno. NO la incluyas en la respuesta.

Formato (EXACTO):
<type>(<scope>): <descripción corta>

<máximo 3-4 líneas de cambios relevantes>

Tests: coverage {{ coverage }}%

⚠️  IMPORTANTE: Responde SOLO el mensaje de commit completo.
SIN: explicaciones, comillas, backticks, o cualquier texto extra.
SIN: incluir contenido de archivos en la respuesta.
"""


# ─────────────────────────────────────────────
# Mapeo de prompts a archivos .md
# ─────────────────────────────────────────────

_PROMPT_FILES: Dict[str, tuple] = {
    "ANALYST_PROMPT":           ("ANALYST.md",          {"task", "output"}),
    "ARCHITECT_PROMPT":         ("ARCHITECT.md",        {"task", "output"}),
    "QA_PLANNER_PROMPT":        ("QA_PLANNER.md",       {"task", "output"}),
    "SYNTHESIZER_PROMPT":       ("SYNTHESIZER.md",      {"task", "analyst_file", "architect_file", "qa_file", "plan_file", "coverage"}),
    "IMPLEMENTER_PROMPT":       ("IMPLEMENTER.md",      {"task", "plan_file", "architect_file", "log_file"}),
    "TEST_WRITER_PROMPT":       ("TEST_WRITER.md",      {"task", "plan_file", "qa_file", "impl_log", "log_file", "coverage"}),
    "TEST_WRITER_PROMPT_MULTI": ("TEST_WRITER_MULTI.md", {"task", "plan_file", "qa_file", "impl_logs", "log_file", "coverage"}),
    "COORDINATOR_PROMPT":       ("COORDINATOR.md",      {"task", "n_agents", "plan_file", "tasks_dir", "log_file"}),
    "DEV_AGENT_PROMPT":         ("DEV_AGENT.md",        {"task", "task_file", "index", "n_agents", "architect_file", "log_file"}),
    "INTEGRATOR_PROMPT":        ("INTEGRATOR.md",       {"task", "agents_dir", "backend_dir", "coverage", "max_attempts", "log_file"}),
    "COMMITTER_PROMPT":         ("COMMITTER.md",        {"task", "branch", "coverage", "plan_file", "integrator_log"}),
}

_PROMPTS_DIR = Path(".claude-workflow") / "prompts"

_COMMIT_TYPE_RE = re.compile(
    r'^(feat|fix|docs|test|refactor|chore)(\([^)]+\))?:\s*'
)


def _sanitize_commit_header(text: str) -> str:
    """Sanitiza mensaje de commit para cumplir commitlint.

    1. Header (primera linea) truncado a <=100 chars en word boundary
    2. Subject en lowercase (despues de type: o type(scope):)
    3. Remueve trailing period del header
    4. Body limitado a 10 lineas totales
    """
    lines = text.split("\n")
    header = lines[0]
    body = lines[1:] if len(lines) > 1 else []

    # Lowercase subject
    m = _COMMIT_TYPE_RE.match(header)
    if m:
        prefix = m.group(0)
        rest = header[len(prefix):]
        if rest and rest[0].isupper():
            rest = rest[0].lower() + rest[1:]
        header = prefix + rest

    # Remover trailing period
    header = header.rstrip(".")

    # Truncar a 100 chars en word boundary
    if len(header) > 100:
        prefix = m.group(0) if m else ""
        rest = header[len(prefix):]
        max_rest = 100 - len(prefix)
        if len(rest) > max_rest:
            truncated = rest[:max_rest]
            last_space = truncated.rfind(" ")
            if last_space > 0:
                truncated = truncated[:last_space]
            header = prefix + truncated

    # Rearmar con body (max 10 lineas totales)
    all_lines = [header] + body
    if len(all_lines) > 10:
        if "Tests:" in all_lines[-1]:
            all_lines = all_lines[:9] + [all_lines[-1]]
        else:
            all_lines = all_lines[:10]

    return "\n".join(all_lines)


def phase5_commit(
    task: str,
    branch: str,
    agents_dir: Path,
    store: SessionStore,
    skip_phases: List[int],
    coverage: float,
    token_store: Optional["TokenStore"] = None,
    prompt_loader: Optional["PromptLoader"] = None,
) -> bool:
    if 5 in skip_phases:
        print("\n⏭  Saltando Fase 5 (--skip-phase 5)")
        return True

    print(f"\n{'═'*60}")
    print("  FASE 5: Commit")
    print(f"{'═'*60}")

    commit_file = agents_dir / "commit" / "COMMIT_MSG.txt"
    flags = ["--allowedTools", "Read,Write", "--max-turns", "5"]
    resume_id = store.load(AgentRole.COMMITTER)
    if resume_id:
        flags += ["--resume", resume_id]

    code, text, sid, usage = claude_p_with_session(
        _render_prompt(_get_prompt("COMMITTER_PROMPT", prompt_loader),
            task=task,
            branch=branch,
            plan_file=agents_dir / "PLAN.md",
            integrator_log=agents_dir / "integration" / "INTEGRATOR.md",
            coverage=f"{coverage:.1f}",
        ),
        flags=flags,
    )

    if sid:
        store.save(AgentRole.COMMITTER, sid)
    if token_store:
        token_store.add(AgentRole.COMMITTER.value, usage)

    if code != 0 or not text.strip():
        text = f"feat: {task}\n\nTests: coverage {coverage:.1f}%"

    # Limpiar respuesta: remover JSON, backticks, comillas extra
    text = text.strip()

    # Si empieza con { o [, probablemente es JSON — usar fallback
    if text.startswith("{") or text.startswith("["):
        text = f"feat: {task}\n\nTests: coverage {coverage:.1f}%"
    # Remover backticks si están presentes
    elif text.startswith("```") or text.startswith("`"):
        lines = text.split("\n")
        text = "\n".join(l.strip("`") for l in lines if l.strip("`"))

    # Validar que empiece con un tipo de commit válido (soporta scope)
    if not _COMMIT_TYPE_RE.match(text):
        text = f"feat: {task}\n\nTests: coverage {coverage:.1f}%"

    # Sanitizar: truncar header, lowercase, remover period, limitar lineas
    text = _sanitize_commit_header(text)

    commit_file.write_text(text.strip())
    print(f"\n📝  Mensaje de commit:\n{text.strip()}\n")

    return base.commit_all(text.strip())


# ─────────────────────────────────────────────
# Flujo principal
# ─────────────────────────────────────────────

def run(
    task: str,
    task_type: str = "feature",
    branch: str = "",
    coverage: int = MIN_COVERAGE,
    workers: int = DEFAULT_WORKERS,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    auto: bool = False,
    parallel_impl: bool = False,
    resume_session: str = "",
    skip_phases: Optional[List[int]] = None,
    dry_run: bool = False,
    agents_dir: Optional[Path] = None,
    dev_agents: int = 1,
    prompt_loader: Optional["PromptLoader"] = None,
    backend_dir: Optional[Path] = None,
) -> int:
    global MIN_COVERAGE
    MIN_COVERAGE = coverage

    skip = skip_phases or []
    _agents_dir = agents_dir or AGENTS_DIR

    # Resolver branch
    if not branch:
        branch = base.timestamp_branch(task)

    auto_label = "✅ SÍ (sin prompts)" if auto else "❌ Interactivo"
    print(f"""
╔{'═'*58}╗
║  CLAUDE ITERATIVE WORKFLOW                               ║
║  Tarea    : {task[:46]:<46}  ║
║  Tipo     : {task_type:<46}  ║
║  Branch   : {branch[:46]:<46}  ║
║  Coverage : {str(coverage) + '%':<46}  ║
║  Auto     : {auto_label:<46}  ║
╚{'═'*58}╝
""")

    if dry_run:
        print("🔍  DRY-RUN: Plan sin ejecutar agentes")
        print(f"  Branch    : {branch}")
        print(f"  Fases     : 0→1→2→3→4→5 (paralelo fase1={workers} workers)")
        dev_mode = f"{dev_agents} Dev agents en paralelo" if dev_agents > 1 else "IMPLEMENTER clásico"
        print(f"  Dev agents: {dev_agents} ({dev_mode})")
        print(f"  Skip      : {skip or 'ninguno'}")
        print(f"  agents/   : {_agents_dir}")
        return 0

    # Inicializar
    _init_agents_dir(_agents_dir, task)

    sessions_file = _agents_dir / "sessions.json"
    store = SessionStore(sessions_file)
    token_store = TokenStore(_agents_dir / "tokens.json")
    runner = ParallelRunner(max_workers=workers, timeout_s=timeout, max_retries=retries)
    gate = CheckpointGate(auto_mode=auto)

    # Inicializar PromptLoader si no se proporcionó
    if prompt_loader is None:
        prompt_loader = PromptLoader()
        prompt_loader.load()

    is_resume = bool(resume_session)
    results: Dict[str, object] = {}
    durations: Dict[str, float] = {}

    # ── FASE 0 ──
    ok = phase0_branch(branch)
    results["branch"] = ok
    if not ok:
        print("❌  No se pudo crear el branch. Abortando.")
        return 1

    project_ctx = _collect_project_context()

    # ── FASE 1 ──
    analysis_results = phase1_analysis(
        task, _agents_dir, store, runner, skip, resume=is_resume,
        token_store=token_store, prompt_loader=prompt_loader,
    )
    if analysis_results:
        all_ok = all(r.success for r in analysis_results.values())
        results["analysis"] = all_ok
        if not all_ok:
            failed = [r.role.value for r in analysis_results.values() if not r.success]
            summary = f"Agentes fallidos: {failed}"
            if not gate.wait("continuar con análisis parcial", summary):
                if _confirm("¿Eliminar el branch creado?"):
                    _delete_branch(branch)
                return 1
            results["analysis"] = True

    if not gate.wait("Fase 2: Síntesis", "Análisis completado. ¿Sintetizar plan?"):
        _save_pause(store, sessions_file, "phase1", branch)
        return 0

    # ── FASE 2 ──
    ok = phase2_synthesize(
        task, _agents_dir, store, skip, coverage, resume=is_resume,
        project_ctx=project_ctx, token_store=token_store, prompt_loader=prompt_loader,
    )
    results["synthesize"] = ok
    if not ok:
        return 1

    if not gate.wait("Fase 3: Implementación", f"Plan listo en {_agents_dir}/PLAN.md. ¿Implementar?"):
        _save_pause(store, sessions_file, "phase2", branch)
        return 0

    # ── FASE 3 ──
    ok = phase3_implement(
        task, _agents_dir, store, runner, skip, coverage,
        parallel_impl=parallel_impl, resume=is_resume,
        dev_agents=dev_agents, project_ctx=project_ctx, token_store=token_store,
        prompt_loader=prompt_loader,
    )
    results["implement"] = ok
    if not ok:
        return 1

    if not gate.wait("Fase 4: Integración", "Implementación lista. ¿Integrar y correr tests?"):
        _save_pause(store, sessions_file, "phase3", branch)
        return 0

    # ── FASE 4 ──
    ok, cov = phase4_integrate(
        task, _agents_dir, store, skip, coverage, resume=is_resume,
        token_store=token_store, prompt_loader=prompt_loader,
        backend_dir=backend_dir,
    )
    results["integrate"] = ok
    results["coverage"] = cov

    # Re-run de coverage si esta bajo el objetivo (modo interactivo)
    _MAX_COV_RETRIES = 2
    _cov_retry = 0
    _cov_dir = backend_dir or base._detect_test_dir(_agents_dir.parent)
    while (cov < coverage) and _cov_retry < _MAX_COV_RETRIES and not gate.auto:
        print(f"\n{'─'*60}")
        print(f"  COVERAGE RE-RUN ({_cov_retry + 1}/{_MAX_COV_RETRIES})")
        print(f"{'─'*60}")
        print(f"  Coverage actual: {cov:.1f}% (objetivo: {coverage}%)")
        if cov == 0.0:
            print(f"  ⚠  0% probablemente significa que no se encontraron tests")
            print(f"  Directorio actual de tests: {_cov_dir}")
        resp = input("\n  [r]e-run coverage / [d]irectorio diferente / [s]kip: ").strip().lower()
        if resp == "s" or resp == "":
            break
        if resp == "d":
            new_dir = input("  Ruta al directorio de tests: ").strip()
            if new_dir:
                _cov_dir = Path(new_dir).resolve()
        _, cov, _ = base.run_tests(cwd=_cov_dir)
        print(f"  📈 Coverage: {cov:.1f}%")
        results["coverage"] = cov
        _cov_retry += 1

    if not ok or cov < coverage:
        msg = f"Coverage: {cov:.1f}% < objetivo {coverage}%"
        if not gate.wait("commitear de todas formas", msg):
            if _confirm("¿Eliminar el branch creado?"):
                _delete_branch(branch)
            return 1

    if not gate.wait("Fase 5: Commit", f"Coverage: {cov:.1f}%. ¿Hacer commit?"):
        _save_pause(store, sessions_file, "phase4", branch)
        return 0

    # ── FASE 5 ──
    ok = phase5_commit(
        task, branch, _agents_dir, store, skip, cov,
        token_store=token_store, prompt_loader=prompt_loader,
    )
    results["commit"] = ok

    # Resumen
    _print_summary(results, token_store, durations=durations, branch=branch, agents_dir=_agents_dir)

    non_coverage = {k: v for k, v in results.items() if k != "coverage"}
    return 0 if all(non_coverage.values()) else 1


def _delete_branch(branch: str) -> None:
    """Vuelve al branch por defecto y elimina el branch local."""
    try:
        default = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
            text=True, stderr=subprocess.DEVNULL
        ).replace("origin/", "").strip() or "main"
    except subprocess.CalledProcessError:
        default = "main"
    subprocess.run(["git", "checkout", default], check=False)
    result = subprocess.run(["git", "branch", "-D", branch], capture_output=True, text=True)
    if result.returncode == 0:
        print(f"🗑   Branch '{branch}' eliminado.")
    else:
        print(f"⚠  No se pudo eliminar '{branch}': {result.stderr.strip()}")


def _save_pause(store: SessionStore, sessions_file: Path, paused_at: str, branch: str = "") -> None:
    data = store.load_all()
    data["paused_at"] = paused_at
    sessions_file.write_text(json.dumps(data, indent=2))
    print(f"\n💾  Sesión guardada en {sessions_file}")
    print(f"   Para reanudar: --resume <session_id>")
    if branch:
        if _confirm("¿Eliminar el branch creado?"):
            _delete_branch(branch)


def _generate_report_md(
    results: dict,
    durations: Dict[str, float],
    token_store: "TokenStore | None",
    branch: str,
    agents_dir: Path,
) -> None:
    """Generate agents/REPORT.md with per-phase status, duration, tokens, and cost."""
    report_file = agents_dir / "REPORT.md"

    lines = [
        "# Reporte de Ejecución\n",
        f"**Rama:** {branch}\n",
        f"**Fecha:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n",
        "## Resumen por Fase\n",
        "| Fase | Status | Duración (s) | Input | Output | Costo USD |",
        "|------|--------|--------------|-------|--------|-----------|",
    ]

    phase_names = {
        "branch": "Fase 0: Branch",
        "analysis": "Fase 1: Análisis",
        "synthesize": "Fase 2: Síntesis",
        "implement": "Fase 3: Implementación",
        "integrate": "Fase 4: Integración",
        "commit": "Fase 5: Commit",
    }

    total_duration = 0.0
    total_input = 0
    total_output = 0
    total_cost = 0.0

    for phase, name in phase_names.items():
        if phase in results:
            status = "✅ OK" if results[phase] else "❌ FAIL"
            duration = durations.get(phase, 0.0)
            total_duration += duration

            # Get token info from token_store if available
            input_tokens = 0
            output_tokens = 0
            cost = 0.0
            if token_store:
                role_map = {
                    "analysis": "ANALYST",
                    "synthesize": "SYNTHESIZER",
                    "implement": "IMPLEMENTER",
                    "integrate": "INTEGRATOR",
                    "commit": "COMMITTER",
                }
                role = role_map.get(phase)
                if role:
                    data = token_store._read()
                    if role in data:
                        input_tokens = data[role].get("input", 0)
                        output_tokens = data[role].get("output", 0)
                        cost = data[role].get("cost_usd", 0.0)
                        total_input += input_tokens
                        total_output += output_tokens
                        total_cost += cost

            lines.append(
                f"| {name} | {status} | {duration:>6.1f}s | {input_tokens:>5,} | {output_tokens:>6,} | ${cost:>8.4f} |"
            )

    # Summary section
    lines.extend([
        "\n## Resumen",
        f"- **Duración total:** {total_duration:.1f}s",
        f"- **Tokens entrada:** {total_input:,}",
        f"- **Tokens salida:** {total_output:,}",
        f"- **Costo total:** ${total_cost:.4f}",
    ])

    if "coverage" in results:
        lines.append(f"- **Coverage:** {results['coverage']}%")

    report_file.write_text("\n".join(lines))
    print(f"📊 Reporte generado: {report_file}")


def _print_summary(
    results: dict,
    token_store: "TokenStore | None" = None,
    durations: Dict[str, float] | None = None,
    branch: str = "",
    agents_dir: Path | None = None,
) -> None:
    print(f"\n╔{'═'*58}╗")
    print("║  RESUMEN FINAL                                           ║")
    icons = {
        "branch": "🌿", "analysis": "🔍", "synthesize": "📋",
        "implement": "⚙️ ", "integrate": "🔗", "commit": "💾",
    }
    for step, status in results.items():
        if step == "coverage":
            print(f"║    📈 Coverage : {str(results['coverage']) + '%':<40}  ║")
        else:
            icon = icons.get(step, "•")
            label = f"{icon} {step.capitalize()}"
            mark = "✅" if status else "❌"
            print(f"║    {mark} {label:<50}  ║")
    if token_store is not None:
        totals = token_store.total()
        if any(totals.get(k, 0) for k in ("input", "output", "cost_usd")):
            print(f"║  {'─'*56}  ║")
            inp  = totals.get("input",      0)
            out  = totals.get("output",     0)
            cr   = totals.get("cache_read", 0)
            cost = totals.get("cost_usd",   0.0)
            print(f"║    💬 Tokens entrada : {inp:>10,}                       ║")
            print(f"║    📤 Tokens salida  : {out:>10,}                       ║")
            print(f"║    ⚡ Caché (read)   : {cr:>10,}                       ║")
            print(f"║    💰 Costo total    : ${cost:>10.4f}                   ║")
    print(f"╚{'═'*58}╝\n")

    # Generar REPORT.md si tenemos durations y agents_dir
    if durations and agents_dir:
        _generate_report_md(results, durations, token_store, branch, agents_dir)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main() -> None:
    global MIN_COVERAGE, _AUTO_MODE
    parser = argparse.ArgumentParser(
        description="Flujo iterativo: humano + N agentes Claude CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python claude_iterative.py -t "Agregar OAuth2" --type feature
  python claude_iterative.py -t "Fix NullPointer" --type fix --auto
  python claude_iterative.py -t "Coverage AuthModule 90%" --type test --coverage 90
  python claude_iterative.py --resume sess_20260321-oauth2
        """,
    )
    parser.add_argument("-t", "--task",     default="",  help="Descripción de la tarea")
    parser.add_argument("--type",           default="feature",
                        choices=["feature", "fix", "test", "refactor"],
                        help="Tipo de tarea (default: feature)")
    parser.add_argument("--branch",         default="", help="Nombre del branch (auto si no se especifica)")
    parser.add_argument("--coverage",       type=int, default=MIN_COVERAGE,
                        help=f"Coverage mínimo %% (default: {MIN_COVERAGE})")
    parser.add_argument("--workers",        type=int, default=DEFAULT_WORKERS,
                        help=f"Workers paralelos en Fase 1 (default: {DEFAULT_WORKERS})")
    parser.add_argument("--timeout",        type=int, default=DEFAULT_TIMEOUT,
                        help=f"Timeout por agente en segundos (default: {DEFAULT_TIMEOUT})")
    parser.add_argument("--retries",        type=int, default=DEFAULT_RETRIES,
                        help=f"Reintentos por agente (default: {DEFAULT_RETRIES})")
    parser.add_argument("--auto",           action="store_true", help="Sin checkpoints")
    parser.add_argument("--parallel-impl",  action="store_true", help="IMPLEMENTER + TEST_WRITER en paralelo")
    parser.add_argument("--resume",         default="", metavar="SESSION_ID",
                        help="Reanudar sesión interrumpida")
    parser.add_argument("--skip-phase",     type=int, action="append", default=[],
                        metavar="N", dest="skip_phases",
                        help="Saltar fase N (se puede repetir, ej: --skip-phase 1 --skip-phase 2)")
    parser.add_argument("--dry-run",        action="store_true", help="Muestra plan sin ejecutar agentes")
    parser.add_argument("--dev-agents",     type=int, default=1, dest="dev_agents",
                        metavar="N",
                        help="Dev agents en paralelo en Fase 3 (default: 1 = IMPLEMENTER clásico)")
    parser.add_argument("--init",           action="store_true",
                        help="Inicializar .claude-workflow/prompts/ con prompts editables")
    parser.add_argument("--update-prompts", action="store_true", dest="update_prompts",
                        help="Actualizar prompts en .claude-workflow/prompts/ con los defaults del módulo (sobreescribe existentes)")
    parser.add_argument("--prompts-dir",    default="", metavar="PATH",
                        help="Ruta a directorio con .md de prompts (default: .claude-workflow/prompts/ en cwd)")
    parser.add_argument("--backend-dir",   default="", metavar="PATH",
                        help="Ruta al directorio de tests/backend (para monorepos). Sobreescribe auto-detección en fase 4")
    parser.add_argument("--cursor-fallback", action="store_true", default=True, dest="cursor_fallback",
                        help="Habilitar fallback a Cursor CLI cuando Claude agota tokens (default: True)")
    parser.add_argument("--no-cursor-fallback", action="store_false", dest="cursor_fallback",
                        help="Deshabilitar fallback a Cursor CLI")
    parser.add_argument("--prefer-cursor", action="store_true", default=False,
                        help="Usar Cursor CLI como backend primario (fallback a Claude CLI)")
    args = parser.parse_args()

    # ── Manejar --init y --update-prompts ──
    if args.init:
        init_prompts_dir()
        return

    if args.update_prompts:
        init_prompts_dir(force=True)
        return

    if not args.task and not args.resume:
        parser.error("Se requiere -t/--task o --resume SESSION_ID")

    task = args.task
    if not task and args.resume:
        # Intentar leer tarea del directorio de agentes
        task_file = AGENTS_DIR / "task.txt"
        if task_file.exists():
            task = task_file.read_text().strip()
        else:
            task = f"Resume: {args.resume}"

    MIN_COVERAGE = args.coverage
    _AUTO_MODE = args.auto

    # Configurar backend CLI
    if args.prefer_cursor:
        primary = CursorCLI()
        fallback = ClaudeCLI()
    else:
        primary = ClaudeCLI()
        fallback = CursorCLI()

    if args.cursor_fallback:
        backend = FallbackBackend(primary, fallback, auto_switch=True)
    else:
        backend = primary

    set_default_backend(backend)

    if args.dev_agents > 1 and args.parallel_impl:
        print("Nota: --parallel-impl es ignorado cuando --dev-agents > 1")

    # Construir PromptLoader si se especificó ruta alternativa
    prompt_loader = None
    if args.prompts_dir:
        prompt_loader = PromptLoader(repo_root=Path(args.prompts_dir).parent)
        prompt_loader.load()

    try:
        exit_code = run(
            task=task,
            task_type=args.type,
            branch=args.branch,
            coverage=args.coverage,
            workers=args.workers,
            timeout=args.timeout,
            retries=args.retries,
            auto=args.auto,
            parallel_impl=args.parallel_impl,
            resume_session=args.resume,
            skip_phases=args.skip_phases,
            dry_run=args.dry_run,
            dev_agents=args.dev_agents,
            prompt_loader=prompt_loader,
            backend_dir=Path(args.backend_dir).resolve() if args.backend_dir else None,
        )
    except KeyboardInterrupt:
        print("\n⚠  Interrumpido por el usuario.")
        computed_branch = args.branch or base.timestamp_branch(task)
        if _confirm("¿Eliminar el branch creado?"):
            _delete_branch(computed_branch)
        sys.exit(130)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
