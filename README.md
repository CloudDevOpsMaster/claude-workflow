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

## 📞 Soporte

Para reportar bugs o sugerencias:
```bash
# Ver logs completos
cat agents/*/[AGENT].md
cat agents/tokens.json
```

Luego reportar con contexto del plan y los logs.

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
