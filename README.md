# Claude Iterative Workflow

Orquestación automática de agentes Claude para implementación de features/fixes de forma completamente guiada.

## 🎯 Qué hace

`claude_iterative.py` automatiza un flujo de 6 fases donde múltiples agentes Claude trabajan en paralelo:

```
FASE 0: Crear branch
     ↓
FASE 1: Análisis paralelo (3 agentes: ANALYST, ARCHITECT, QA_PLANNER)
     ↓
FASE 2: Síntesis del plan unificado
     ↓
FASE 3: Implementación (1 o N DEV agents) + Tests
     ↓
FASE 4: Integración y cobertura
     ↓
FASE 5: Commit automático
```

Cada agente:
- Trabaja en su propio contexto de sesión Claude
- Escribe outputs a archivos markdown
- Si falla, se reintenta automáticamente (configurable)
- Sus tokens y tiempos se registran

## 🚀 Modos de Operación

| Modo | Flag | Comportamiento |
|------|------|---------------|
| **Interactivo** | sin flag | Pregunta **antes de cada FASE** y en operaciones destructivas (ej: "¿eliminar branch?") |
| **Automático** | `--auto` | Corre **sin pausas ni prompts** — todas las fases de inicio a fin sin input humano |

En modo automático, las preguntas sobre operaciones destructivas se saltan con respuesta por defecto `No` (no eliminar).

## 🚀 Getting Started

### 1. Requisitos previos

- **Python** ≥ 3.10
- **Claude CLI** instalado y con token válido en `~/.claude/config`
- **Git** con repositorio inicializado
- **uv** instalado:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

### 2. Clonar e instalar

```bash
git clone https://github.com/tu-usuario/claude-workflow
cd claude-workflow
bash install.sh
```

El script `install.sh` verifica los requisitos, instala los comandos globalmente y confirma que todo quedó en PATH.

### 3. Verificar

```bash
which claude-iterative   # → ~/.local/bin/claude-iterative
claude-iterative --help
```

### 4. Primer uso

```bash
# Ve a cualquier proyecto con git
cd ~/mi-proyecto

# Feature nueva (modo interactivo — pregunta antes de cada fase)
claude-iterative -t "Agregar autenticación OAuth2" --type feature

# Fix urgente (modo automático — corre de inicio a fin sin pausas)
claude-iterative -t "Fix NullPointerException en login" --type fix --auto

# Ver el plan sin ejecutar nada
claude-iterative -t "Refactor módulo de pagos" --type refactor --dry-run
```

> **Tip:** `--editable` en la instalación significa que cualquier cambio en `claude_workflow/` se refleja inmediatamente sin reinstalar.

---

## 💻 Uso Básico

### Interactivo (modo por defecto)
```bash
claude-iterative -t "Agregar autenticación OAuth2" --type feature
```
Preguntará antes de cada fase.

### Automático (sin prompts)
```bash
claude-iterative -t "Fix NullPointerException" --type fix --auto
```
Corre de inicio a fin sin pausas.

### Dry-run (ver plan sin ejecutar)
```bash
claude-iterative -t "Test suite para Auth" --type test --dry-run
```

### Reanudar sesión interrumpida
```bash
claude-iterative --resume sess_20260325-oauth2
```

### Inicializar prompts personalizables
```bash
claude-iterative --init
```
Crea `.claude-workflow/prompts/` con un archivo `.md` por agente. Edita los archivos para personalizar los prompts sin modificar código.

## 🚩 Flags CLI

```
GENERAL:
  -t, --task TEXT              Descripción de la tarea (requerido si no es --resume)
  --type {feature,fix,test,refactor}
                               Tipo de cambio (default: feature)
  --branch TEXT                Nombre del branch (auto si no se especifica)

CONTROL:
  --auto                       Modo automático: sin prompts ni checkpoints
  --dry-run                    Mostrar plan sin ejecutar agentes
  --resume SESSION_ID          Reanudar sesión pausada
  --init                       Inicializar .claude-workflow/prompts/ con archivos editables
  --prompts-dir PATH           Ruta alternativa a directorio de prompts (default: .claude-workflow/prompts/)
  --backend-dir PATH           Ruta al directorio de tests/backend (para monorepos). Sobreescribe auto-detección en Fase 4

RECURSOS:
  --workers N                  Workers paralelos en Fase 1 (default: 3)
  --timeout N                  Timeout por agente en segundos (default: 300)
  --retries N                  Reintentos si un agente falla (default: 2)
  --coverage N                 Coverage mínimo esperado en % (default: 80)

IMPLEMENTACIÓN:
  --parallel-impl              IMPLEMENTER + TEST_WRITER en paralelo (ignora si --dev-agents > 1)
  --dev-agents N               N agentes desarrollo en paralelo (default: 1 = IMPLEMENTER clásico)

SALTO DE FASES:
  --skip-phase N               Saltar fase N (repetible: --skip-phase 1 --skip-phase 2)
```

## 🔀 `--dev-agents N` vs `--parallel-impl`: ¿Cuándo usar cada una?

Son dos estrategias completamente diferentes para paralelizar la FASE 3:

| Aspecto | `--dev-agents 3` | `--parallel-impl` |
|---------|-----------------|-------------------|
| **Cuándo usarla** | Tareas **independientes** (distintos módulos/archivos) | Código **único** (mismo módulo, roles distintos) |
| **División** | Por **módulos** — DEV_1, DEV_2, DEV_3 trabajan en código diferente | Por **rol** — IMPLEMENTER + TEST_WRITER trabajan en el mismo código |
| **Agentes** | COORDINATOR + 3 DEV agents + TEST_WRITER | IMPLEMENTER + TEST_WRITER |
| **Flujo FASE 3** | 1. COORDINATOR divide plan<br>2. DEV_1, DEV_2, DEV_3 en paralelo<br>3. TEST_WRITER espera a todos | 1. IMPLEMENTER + TEST_WRITER corren en paralelo |
| **Tiempo total** | ⚡⚡⚡ Más rápido (3 agentes simultáneos) | ⚡⚡ Rápido (2 agentes simultáneos) |
| **Complejidad** | 🔴 Mayor (coordinación de sub-tareas, sin conflictos) | 🟢 Menor (simple paralelismo) |
| **Cuándo ignora** | N/A | Se ignora si `--dev-agents > 1` |

### 📌 Regla de oro

```
┌─────────────────────────────────────────┐
│ ¿El plan tiene partes INDEPENDIENTES?   │
│                                         │
│ SÍ  → --dev-agents 3                   │
│ NO  → --parallel-impl (o secuencial)   │
└─────────────────────────────────────────┘
```

### 🎯 Ejemplos

#### Usa `--dev-agents 3` — Microservicios
```bash
python3 claude_iterative.py \
  -t "Migrar a microservicios: Auth, Payment, Notifications" \
  --type refactor \
  --dev-agents 3
```

**Resultado en FASE 3:**
```
COORDINATOR: divide en 3 sub-tareas
  ↓
DEV_1: módulo auth (secuencial)
DEV_2: módulo payment (paralelo a DEV_1)
DEV_3: módulo notifications (paralelo a ambos)
  ↓
TEST_WRITER: tests para toda la arquitectura
```

Tiempo total ≈ tiempo de 1 sub-tarea (las 3 corren juntas)

#### Usa `--parallel-impl` — Feature unitaria
```bash
python3 claude_iterative.py \
  -t "Agregar autenticación OAuth2" \
  --type feature \
  --parallel-impl
```

**Resultado en FASE 3:**
```
IMPLEMENTER: escribe código OAuth2
TEST_WRITER: escribe tests OAuth2   (paralelo a IMPLEMENTER)
```

Tiempo total ≈ max(tiempo implementar, tiempo tests) < tiempo secuencial

---

## 📋 Fases Detalladas

### FASE 0: Crear Branch
- Crea un branch git con nombre automático o manual
- Ejemplo: `feature/20260325-oauth2`

### FASE 1: Análisis Paralelo (3 agentes)
**Outputs:** `agents/analysis/{ANALYST,ARCHITECT,QA_PLANNER}.md`

- **ANALYST**: Lee el código, identifica módulos afectados, dependencias, riesgos
- **ARCHITECT**: Diseña la arquitectura, patrones, interfaces públicas
- **QA_PLANNER**: Planifica tests unitarios, integración, mocking

Corren en paralelo (ajustable con `--workers`).

### FASE 2: Síntesis
**Output:** `agents/PLAN.md`

- SYNTHESIZER lee los 3 análisis
- Genera un plan unificado con pasos concretos, validaciones, criterios de aceptación

### FASE 3: Implementación
**Outputs:** `agents/implementation/{IMPLEMENTER,TEST_WRITER}.md` (o `DEV_*.md` si N > 1)

Dos ramas:
- **N=1** (default): IMPLEMENTER escribe código, TEST_WRITER escribe tests
  - Opcionalmente en paralelo (`--parallel-impl`)
- **N>1**: COORDINATOR divide el plan en N sub-tareas, N DEV agents implementan en paralelo

### FASE 4: Integración
**Output:** `agents/integration/INTEGRATOR.md`

- INTEGRATOR ejecuta tests, mide coverage
- Si coverage < objetivo, agrega más tests
- Repite hasta pasar o agotar intentos

### FASE 5: Commit
**Output:** `agents/commit/COMMIT_MSG.txt`

- COMMITTER genera mensaje Conventional Commits
- Agente integradorconfirma antes de hacer git commit

## 📁 Estructura de Archivos Generados

```
agents/
├── task.txt                    # Descripción de la tarea
├── sessions.json               # IDs de sesión Claude (para reanudación)
├── tokens.json                 # Consumo de tokens por agente
├── PLAN.md                     # Plan unificado (salida FASE 2)
├── analysis/
│   ├── ANALYST.md             # Análisis (FASE 1)
│   ├── ARCHITECT.md            # Arquitectura (FASE 1)
│   └── QA_PLANNER.md          # Plan QA (FASE 1)
├── implementation/
│   ├── IMPLEMENTER.md         # Log de implementación (FASE 3)
│   ├── TEST_WRITER.md         # Log de tests (FASE 3)
│   ├── COORDINATOR.md         # Coordinación (FASE 3, si N > 1)
│   ├── DEV_1.md, DEV_2.md... # Logs de dev agents (FASE 3, si N > 1)
│   └── tasks/
│       ├── DEV_1.md           # Sub-tarea 1
│       ├── DEV_2.md           # Sub-tarea 2
│       └── ...
├── integration/
│   └── INTEGRATOR.md          # Resultados de tests (FASE 4)
└── commit/
    └── COMMIT_MSG.txt         # Mensaje de commit (FASE 5)
```

## 🔄 Reanudación

Si la ejecución se interrumpe (Ctrl+C, timeout, error):

```bash
# Ver sesiones guardadas
cat agents/sessions.json

# Reanudar desde donde paró
python3 claude_iterative.py --resume sess_20260325-oauth2
```

Las sesiones Claude se persisten en `sessions.json` para reutilizar contexto.

## 📊 Monitoreo de Tokens y Costos

Después de cada ejecución, se genera `agents/tokens.json`:

```json
{
  "ANALYST": {"input": 5000, "output": 2000, "cost_usd": 0.15},
  "ARCHITECT": {"input": 4500, "output": 1800, "cost_usd": 0.13},
  "_total": {"input": 45000, "output": 18000, "cost_usd": 3.50}
}
```

## 🧪 Testing

```bash
cd /ruta/a/claude-workflow
uv run --group dev pytest
```

O con detalle:
```bash
uv run --group dev pytest tests/ -v --cov=claude_workflow --cov-report=term-missing
```

Coverage objetivo: 80%+

## 🎓 Ejemplos

### Feature completa (modo interactivo)
```bash
claude-iterative \
  -t "Implementar 2FA con TOTP" \
  --type feature \
  --coverage 85
```

### Fix urgente (modo automático, sin preguntas)
```bash
claude-iterative \
  -t "Fix SQL injection en login" \
  --type fix \
  --auto
```

### Refactor con múltiples dev agents
```bash
claude-iterative \
  -t "Refactor auth module a microservicios" \
  --type refactor \
  --dev-agents 3 \
  --coverage 90
```

### Pause y reanudar
```bash
# Empieza interactivo
claude-iterative -t "OAuth2" --type feature

# [Usuario pausa en FASE 2 con Ctrl+C]

# Reanudar más tarde
claude-iterative --resume sess_20260325-oauth2
```

## 🔧 Troubleshooting

| Problema | Solución |
|----------|----------|
| "Claude CLI no encontrado" | Verificar que `claude` está en PATH: `which claude` |
| Timeout en agentes | Aumentar timeout: `--timeout 600` |
| Coverage muy bajo | Incrementar objetivo: `--coverage 95` o agregar `--dev-agents 2` |
| Session ID no válido | Borrar `agents/sessions.json` y recomenzar |
| Output de agente truncado | Ver archivo completo en `agents/analysis/*.md` |

## 📝 Notas

- Los agentes usan Claude CLI con formato JSON (`--output-format json`)
- La sesión persiste para reutilizar contexto en reintentos
- En modo `--auto`, se asume "No" a todas las preguntas destructivas
- Los outputs son always markdown (`.md`) para fácil revisión
- El script es idempotente: puedes reanudar sin miedo a duplicar

## 🎨 Personalizar Prompts

Por defecto, cada agente usa prompts predefinidos. Puedes personalizar el tono, idioma o enfoque sin tocar código:

### 1. Inicializar estructura de prompts

```bash
claude-iterative --init
```

Crea `.claude-workflow/prompts/` con:
```
.claude-workflow/prompts/
├── README.md               ← tabla de placeholders
├── ANALYST.md
├── ARCHITECT.md
├── QA_PLANNER.md
├── SYNTHESIZER.md
├── IMPLEMENTER.md
├── TEST_WRITER.md
├── TEST_WRITER_MULTI.md
├── COORDINATOR.md
├── DEV_AGENT.md
├── INTEGRATOR.md
└── COMMITTER.md
```

### 2. Editar prompts

Abre cualquier archivo `.md` y personaliza el prompt manteniendo los `{placeholders}`:

**Ejemplo: Cambiar idioma a inglés**
```bash
nano .claude-workflow/prompts/ANALYST.md
```

**Archivo original:**
```
Eres un analista de software senior. Analiza el proyecto actual y la tarea:
TAREA: {task}
...
```

**Personalizado:**
```
You are a senior software analyst. Analyze the current project and the task:
TASK: {task}
...
```

### 3. Los cambios aplican automáticamente

En el próximo `claude-iterative` run, se usarán tus prompts custom. Si falta algún `{placeholder}`, se emitirá una advertencia pero el prompt seguirá cargándose.

### Ruta alternativa de prompts

Si prefieres guardar los prompts en otra ruta:

```bash
claude-iterative -t "Mi tarea" --prompts-dir /ruta/mis-prompts
```

---

## 🔌 Sistema de Plugins / Hooks

`claude-iterative` soporta hooks personalizados que se ejecutan **antes y después de cada fase** del workflow. Esto permite integración con sistemas externos, validación custom, o notificaciones sin modificar el código del tool.

### Diferencia con Claude Code Hooks

| Aspecto | claude-iterative Hooks | Claude Code Hooks |
|---------|---|---|
| **Nivel** | Fase del workflow (6 puntos: 0-5) | Herramienta individual |
| **Ejecución** | Python sincrónico (en-proceso) | Shell script (subproceso) |
| **Contexto** | Rich: task, branch, phase_name, phase result | Ninguno (solo env vars) |
| **Sintaxis** | `before_phase_N(ctx)`, `after_phase_N(ctx, result)` | Shell scripts en settings.json |
| **Scope** | Workflow multi-fase completo | Operaciones de herramientas puntuales |
| **Uso típico** | Validación pre-fase, notificaciones, reporting | Formateo de código, git hooks |

### Crear Hooks

Crear un archivo `.claude-workflow-hooks.py` en la raíz del proyecto:

```python
"""Custom hooks for claude-iterative workflow."""

def before_phase_0(ctx):
    """Pre-branch: validate repo state."""
    import subprocess
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if result.stdout.strip():
        raise RuntimeError("Repo has uncommitted changes. Commit or stash first.")
    print(f"✓ Pre-check passed for task: {ctx['task']}")

def after_phase_2(ctx, result):
    """Post-synthesis: notify about plan."""
    print(f"📋 Plan ready for {ctx['task']}")
    # Ejemplo: enviar a Slack
    # requests.post(WEBHOOK_URL, json={"text": f"Plan ready: {ctx['task']}"})

def after_phase_4(ctx, result):
    """Post-integration: check coverage."""
    ok, coverage = result
    print(f"📊 Coverage: {coverage:.1f}%")
    if coverage < 80:
        print(f"⚠️  Coverage below 80% threshold")
```

### Context Dict

Cada hook recibe un `ctx` dict con:
- `task` — descripción de la tarea
- `branch` — nombre del git branch
- `phase_name` — nombre legible de la fase (ej: "phase_0_branch")
- `phase_num` — número de fase (0-5)

### Result Types

Hooks `after_phase_N` reciben un `result` según la fase:
- Fases 0-3: `bool` (success/failure)
- Fase 4 (integrate): `(bool, float)` — (success, coverage%)
- Fase 5 (commit): `bool`

### Manejo de Errores

Si un hook genera excepción:
- Se logea el error
- El workflow **continúa** (no se aborta)
- Ideal para integración robusta con sistemas externos

---

## 🌍 Multi-Repositorio Support

`claude-multi` permite ejecutar `claude-iterative` en paralelo sobre múltiples repositorios con una sola configuración. Ideal para:
- Aplicar la misma feature/fix a varios repos
- Migrations sistémicas (ej: agregar type hints a 10 servicios)
- Refactorings coordinados

### Quick Start

**1. Crear config YAML:**
```yaml
# multi.yaml
task: "add type hints to all public functions"
repos:
  - path: /Users/you/projects/service-a
  - path: /Users/you/projects/service-b
    branch: feat/types-b        # override branch per repo
  - path: /Users/you/projects/lib-core
```

**2. Ejecutar:**
```bash
claude-multi --config multi.yaml --workers 3 --output REPORT.md
```

**3. Revisar resultados:**
```bash
cat REPORT.md
# Muestra status, duración, exit code por repo
```

### Diferencia vs Manual

| Aspecto | `claude-multi` | N × `claude-iterative` manual |
|---------|---|---|
| **Paralelismo** | ✅ Paralelo (N repos en paralelo) | ❌ Secuencial (esperar N veces) |
| **Reporte unificado** | ✅ `MULTI_REPORT.md` | ❌ N branches/reports distintos |
| **Duración total** | ~máximo(duración_por_repo) | ~suma(duraciones) |
| **Config único** | ✅ Sí | ❌ No |

### CLI Flags

```bash
claude-multi --help
  --config CONFIG     Path to YAML/JSON config (required)
  --workers N         Max parallel repos (default: 3)
  --output FILE       Report output file (default: MULTI_REPORT.md)
  --auto              Run in auto mode (default: True)
```

### Ejemplo de Output

```markdown
# Multi-Repository Report

Generated: 2026-03-27 15:30:00

## Summary

| Repository | Branch | Status | Duration (s) | Exit Code |
|---|---|---|---:|---:|
| /projects/service-a | feat/types | ✅ SUCCESS | 245.3s | 0 |
| /projects/service-b | feat/types-b | ✅ SUCCESS | 198.7s | 0 |
| /projects/lib-core | main | ❌ FAILURE | 120.1s | 1 |

## Totals

- **Total Repositories:** 3
- **Successful:** 2/3
- **Total Duration:** 564.1s (9.4m)

## Failures

### /projects/lib-core

**Status:** failure
**Exit Code:** 1
**Error:** [stderr output from claude-iterative]
```

---

## 📊 Reportes de Ejecución

Cada run de `claude-iterative` genera un **archivo de reporte** en `agents/REPORT.md` con un resumen completo de la ejecución.

### Ejemplo de REPORT.md

```markdown
# Reporte de Ejecución

**Rama:** feat/my-feature
**Fecha:** 2026-03-27 14:35:22

## Resumen por Fase

| Fase | Status | Duración (s) | Input | Output | Costo USD |
|------|--------|--------------|-------|--------|-----------|
| Fase 0: Branch | ✅ OK | 2.1s | - | - | - |
| Fase 1: Análisis | ✅ OK | 185.3s | 12,400 | 3,200 | $0.0420 |
| Fase 2: Síntesis | ✅ OK | 72.5s | 8,100 | 1,800 | $0.0215 |
| Fase 3: Implementación | ✅ OK | 241.2s | 15,800 | 4,500 | $0.0598 |
| Fase 4: Integración | ✅ OK | 117.5s | - | - | - |
| Fase 5: Commit | ✅ OK | 8.2s | 2,100 | 450 | $0.0081 |

## Resumen

- **Duración total:** 626.8s (10.4 min)
- **Tokens entrada:** 38,400
- **Tokens salida:** 9,950
- **Costo total:** $0.1314
- **Coverage:** 84.5%
```

### Diferencia vs Output Console

| Aspecto | Console Output | `agents/REPORT.md` |
|---------|---|---|
| **Persistencia** | Solo mientras corre | Archivo permanente |
| **Formato** | ASCII box | Markdown table |
| **Reutilizable** | No | Sí (para CI/CD, análisis) |
| **Granularidad** | Por agente | Por fase |
| **Tokens** | Solo total | Por fase |

### Cómo usar el reporte

**En CI/CD:**
```bash
claude-iterative -t "my task" --auto
# Verificar el reporte después
grep "Coverage" agents/REPORT.md || exit 1
```

**Análisis de costos:**
```bash
# Extraer costo total
cat agents/REPORT.md | grep "Costo total"
```

**Debugging:**
```bash
# Ver duración de cada fase para identificar cuellos de botella
cat agents/REPORT.md | grep "Duración"
```

---

## 📞 Soporte

Para reportar bugs o sugerencias:
```bash
# Ver logs completos
cat agents/*/[AGENT].md
cat agents/tokens.json
```

Luego reportar con contexto del plan y los logs.

---

## 🧪 Testing & Coverage

### Ejecutar Tests

```bash
# Correr todos los tests
uv run --group dev pytest tests/ -v

# Correr tests con coverage
uv run --group dev pytest tests/ --cov=claude_workflow --cov-report=term-missing -q

# Correr un archivo específico de tests
uv run --group dev pytest tests/test_iterative.py -v

# Correr un test específico
uv run --group dev pytest tests/test_plan_exec.py::test_parse_coverage_pytest_format -v
```

### Cobertura Actual

| Módulo | Cobertura | Tests | Estado |
|--------|-----------|-------|--------|
| iterative.py | 60% | 188 | ✅ Core phases cubiertas |
| multi.py | 77% | 48 | ✅ Multi-repo flow cubierto |
| plan_exec.py | 67% | 134 | ✅ Plan execution cubierto |
| **TOTAL** | **64%** | **248** | ✅ Baseline sólido |

### Estructura de Tests

```
tests/
├── test_iterative.py      # Tests para phase functions, agents, prompts
│   ├── _confirm() function
│   ├── _collect_project_context()
│   ├── AgentRole enum
│   ├── AgentResult class
│   ├── TokenStore operations
│   ├── SessionStore operations
│   ├── PromptLoader (custom prompts)
│   ├── Phase functions (phase0-5)
│   └── Agent execution (_run_analyst, _run_architect, etc.)
│
├── test_multi.py          # Tests para multi-repo orchestration
│   ├── RepoConfig parsing
│   ├── MultiConfig creation
│   ├── parse_config() with JSON/YAML
│   ├── run_repo_task() success/failure/timeout
│   ├── run_parallel() execution
│   └── generate_report() formatting
│
└── test_plan_exec.py      # Tests para plan execution workflow (NUEVO)
    ├── Pure logic functions (parse_coverage, accum_usage, timestamp_branch)
    ├── Git helpers (create_branch, commit_all, delete_branch)
    ├── Claude subprocess integration (claude_p, claude_stream)
    ├── step_* functions (branch, execute, tests, commit)
    └── Main CLI parsing
```

### Tests Agregados Recientemente

**test_plan_exec.py** (NEW - 134 tests)
- ✅ `_is_token_exhausted()`: Detecta context window exceeded
- ✅ `parse_coverage()`: Extrae % de pytest/jest output
- ✅ `_accum_usage()`: Acumula tokens por step
- ✅ `timestamp_branch()`: Genera branch name con timestamp
- ✅ `git()` helpers: create_branch, commit_all, delete_branch
- ✅ `claude_p()` & `claude_stream()`: Ejecución de Claude CLI
- ✅ `run_tests()`: Detección de pytest vs jest
- ✅ `step_*()` functions: Toda la fase workflow

**test_multi.py** (28 new tests)
- ✅ `run_repo_task()`: Success, failure, timeout, exceptions
- ✅ `run_parallel()`: Multi-repo execution con sorting
- ✅ `parse_config()`: JSON/YAML validation
- ✅ `generate_report()`: Markdown report generation

**test_iterative.py** (93 new tests)
- ✅ `_run_analyst/architect/qa_planner/etc()`: Agent execution
- ✅ `phase1_analysis/phase2_synthesize/etc()`: Phase functions
- ✅ `TokenStore`: Token accounting across agents
- ✅ `SessionStore`: Session persistence per role
- ✅ `PromptLoader`: Custom prompt loading & validation

### Cómo Contribuir Tests

1. **Identificar líneas no cubiertas:**
   ```bash
   uv run --group dev pytest tests/ --cov=claude_workflow --cov-report=html
   # Abre htmlcov/index.html para ver coverage visual
   ```

2. **Agregar test para función:**
   ```python
   # En tests/test_iterative.py, tests/test_multi.py, o tests/test_plan_exec.py

   @patch("claude_workflow.iterative.claude_p_with_session")
   def test_my_feature(mock_cps, tmp_path, monkeypatch):
       """Descripción clara del test."""
       monkeypatch.chdir(tmp_path)
       mock_cps.return_value = (0, "output", "sess-id", {"input": 100})

       result = ci.my_function("args")

       assert result.success is True
   ```

3. **Correr tu test:**
   ```bash
   uv run --group dev pytest tests/test_iterative.py::test_my_feature -v
   ```

4. **Verificar coverage local:**
   ```bash
   uv run --group dev pytest tests/ --cov=claude_workflow -q
   ```

### Mocking Strategy

Usamos `unittest.mock.patch` para aislar funciones:

```python
# Mock subprocess.run para git commands
@patch("claude_workflow.plan_exec.subprocess.run")
def test_git_operation(mock_run):
    mock_run.return_value = Mock(returncode=0, stdout="output", stderr="")
    result = pe.create_branch("feat/test")
    assert result is True

# Mock claude_p_with_session para agent execution
@patch("claude_workflow.iterative.claude_p_with_session")
def test_agent_runs(mock_cps):
    mock_cps.return_value = (0, "output", "session-id", {"input": 100})
    result = ci._run_analyst("task", analysis_dir, None)
    assert result.success is True
```

### Coverage Goals

Actual: **64%**
Target: **>80%** (requiere ~213 statements más)

Prioridad para mejorar:
1. 🔴 Phase functions con múltiples ramificaciones (phase1-5)
2. 🟡 Error handling paths en opencode fallback
3. 🟡 Edge cases en subprocess integration

---

## 🗺️ Próximos Pasos

### 1. Publicar en GitHub

Crear el repo remoto y hacer push para tener respaldo e instalación remota:

```bash
cd /ruta/a/claude-workflow
gh repo create claude-workflow --public --source=. --push
```

Una vez publicado, cualquiera puede instalar sin clonar:
```bash
uv tool install "claude-workflow @ git+https://github.com/tu-usuario/claude-workflow"
```

### 2. Correr los tests

Verificar que todo pasa tras cualquier cambio:

```bash
cd /ruta/a/claude-workflow
uv run --group dev pytest
```

Resultado esperado: coverage ≥ 80% en `claude_workflow/`.

### 3. Mejorar el tool de forma independiente

Al estar instalado con `--editable`, cualquier cambio en `claude_workflow/iterative.py` o `claude_workflow/plan_exec.py` aplica inmediatamente en el siguiente invocación de `claude-iterative` — sin reinstalar.

Flujo de trabajo recomendado para mejoras:
```bash
# 1. Editar el código
code /ruta/a/claude-workflow/claude_workflow/iterative.py

# 2. Correr tests para verificar
uv run --group dev pytest

# 3. Probar globalmente desde cualquier proyecto
cd ~/mi-proyecto && claude-iterative -t "nueva feature" --dry-run

# 4. Commit y push
cd /ruta/a/claude-workflow && git add -p && git commit -m "feat: ..."
```
