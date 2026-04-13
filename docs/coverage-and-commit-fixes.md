# Coverage detection en monorepos + sanitizacion de commit messages

## Contexto

Al ejecutar el workflow contra un monorepo (con backend en `apps/backend/`) aparecieron dos problemas:

1. **Coverage = 0%** aunque los tests existian. El workflow no encontraba el directorio de tests porque lo buscaba en la raiz del repo.
2. **Commit falla por commitlint**: el COMMITTER generaba headers de 369 chars (limite 100), con sentence-case y trailing period — todas reglas que commitlint rechaza.

Este documento describe los cambios que agregan auto-deteccion del directorio de tests, un mecanismo de re-run de coverage, y sanitizacion automatica del commit message.

---

## Fix 1: Coverage detection + re-run

### Cambios

**Auto-deteccion del directorio de tests**
- Nueva funcion `_detect_test_dir(base_dir)` en `claude_workflow/plan_exec.py`
- Busca `tests/` o `conftest.py` en orden: raiz → `apps/backend/` → `backend/` → `src/`
- Retorna el primer match, o la raiz como fallback

**`run_tests()` acepta `cwd`**
- Nuevo parametro opcional `cwd: Path | None = None`
- Si no se pasa, usa `Path.cwd()` (backward compatible)

**`phase4_integrate()` usa el directorio correcto**
- Nuevo parametro `backend_dir: Optional[Path] = None`
- Si no se pasa, invoca `_detect_test_dir(agents_dir.parent)` en vez del antiguo hardcode `agents_dir.parent / "backend"`
- Cuando coverage == 0%, imprime diagnostico (ultimas 30 lineas del output del test runner + el path usado) para facilitar debug

**Loop de re-run entre Fase 4 y Fase 5**
- Si coverage < objetivo y estas en modo interactivo, el workflow ofrece re-ejecutar coverage
- Max 2 reintentos, con opciones `[r]e-run` / `[d]irectorio diferente` / `[s]kip`
- Re-ejecuta solo `run_tests(cwd=...)`, sin disparar de nuevo al INTEGRATOR agent
- En `--auto` el loop se salta (preserva comportamiento actual)

**Flag CLI `--backend-dir`**
- Override manual cuando la auto-deteccion no sirve
- Se pasa de `main()` → `run()` → `phase4_integrate()` → `run_tests()`

### Uso

```bash
# Auto-deteccion (no requiere flag nuevo)
claude-iterative -t "Fix bug en anticipos"

# Override explicito para monorepos
claude-iterative -t "Fix bug en anticipos" --backend-dir apps/backend

# Modo auto salta el loop de re-run
claude-iterative -t "Fix bug" --auto
```

---

## Fix 2: Sanitizacion del commit message

### Cambios

**Regex para validar tipo de commit**
- Nueva constante `_COMMIT_TYPE_RE = re.compile(r'^(feat|fix|docs|test|refactor|chore)(\([^)]+\))?:\s*')`
- Soporta tanto `feat:` como `feat(scope):` (el `startswith` anterior rechazaba el scope como invalido)

**Nueva funcion `_sanitize_commit_header()`**
Aplica 4 reglas en orden, para cumplir commitlint:

1. **Lowercase subject**: primera letra del subject (despues de `type:` o `type(scope):`) se fuerza a minuscula
2. **Remover trailing period**: se quita `.` del final del header
3. **Truncar header a ≤100 chars**: si el header es mas largo, se trunca en word boundary (ultimo espacio antes de 100 chars), preservando el prefix `type(scope):`
4. **Limitar body a 10 lineas totales**: si hay mas de 10 lineas, se conservan las primeras 9 + la ultima si contiene `Tests:`

### Ejemplo

**Input del COMMITTER agent:**
```
feat: Fix Lambda infinite loop en anticipos-api — comparar OldImage vs NewImage en DDB stream handler para skip re-calculo cuando monto no cambio. Archivo: apps/backend/amplify/functions/anticipos-api/index.py.

Tests: coverage 0.0%
```

**Despues de sanitizar:**
```
feat: fix Lambda infinite loop en anticipos-api — comparar OldImage vs NewImage en DDB stream

Tests: coverage 0.0%
```

- 369 chars → 93 chars (truncado en word boundary)
- `Fix` → `fix` (lowercase)
- Trailing `.` removido
- Pasa commitlint: ✅ `header-max-length`, ✅ `subject-case`, ✅ `subject-full-stop`

---

## Archivos modificados

| Archivo | Cambios |
|---------|---------|
| `claude_workflow/plan_exec.py` | `run_tests(cwd=...)` + `_detect_test_dir()` |
| `claude_workflow/iterative.py` | `phase4_integrate(backend_dir=...)`, `_sanitize_commit_header()`, `_COMMIT_TYPE_RE`, loop de re-run, flag `--backend-dir` |
| `tests/test_iterative_phases.py` | Asserts actualizados (subject ahora lowercase) |
| `README.md` | Flag `--backend-dir` documentado |

## Verificacion

```bash
# Tests unitarios
pytest tests/ -v

# Sanity check del sanitizer
python3 -c "
from claude_workflow.iterative import _sanitize_commit_header
long = 'feat: Fix Lambda infinite loop ... ' + 'x' * 400
r = _sanitize_commit_header(long)
assert len(r.split(chr(10))[0]) <= 100
"

# Sanity check de auto-deteccion
python3 -c "
from claude_workflow.plan_exec import _detect_test_dir
from pathlib import Path
# Crear tmp dir con apps/backend/tests/ y verificar
"
```
