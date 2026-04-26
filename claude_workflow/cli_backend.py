#!/usr/bin/env python3
"""
cli_backend.py
Abstracción de backends CLI (Claude CLI, Cursor CLI) con fallback automático.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol, runtime_checkable


# ─────────────────────────────────────────────
# Token exhaustion detection
# ─────────────────────────────────────────────

_TOKEN_EXHAUSTION_PATTERNS = [
    "context length exceeded",
    "context_length_exceeded",
    "context window",
    "token limit",
    "too long",
    "max_tokens",
    "exceeds the",
    "prompt is too long",
]


def is_token_exhausted(exit_code: int, output: str) -> bool:
    """Detecta si un fallo de CLI se debe a context window exceeded."""
    if exit_code == 0:
        return False
    output_lower = output.lower()
    return any(p in output_lower for p in _TOKEN_EXHAUSTION_PATTERNS)


# ─────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────

@dataclass
class CLIResult:
    """Resultado de una ejecución de CLI."""
    exit_code: int
    text: str
    usage: Dict = field(default_factory=dict)
    session_id: Optional[str] = None
    backend: str = "unknown"

    @property
    def success(self) -> bool:
        return self.exit_code == 0

    @property
    def token_exhausted(self) -> bool:
        return is_token_exhausted(self.exit_code, self.text)


# ─────────────────────────────────────────────
# Backend Protocol
# ─────────────────────────────────────────────

@runtime_checkable
class CLIBackend(Protocol):
    """Protocol para backends de CLI."""

    @property
    def name(self) -> str:
        """Nombre del backend (ej: 'claude', 'cursor')."""
        ...

    def is_available(self) -> bool:
        """Verifica si el backend está instalado y configurado."""
        ...

    def execute(
        self,
        prompt: str,
        flags: Optional[List[str]] = None,
        step: str = "unknown",
    ) -> CLIResult:
        """Ejecuta un prompt y retorna el resultado."""
        ...

    def execute_stream(
        self,
        prompt: str,
        flags: Optional[List[str]] = None,
        step: str = "unknown",
        on_line: Optional[Callable[[str], None]] = None,
    ) -> CLIResult:
        """Ejecuta un prompt con streaming de output."""
        ...


# ─────────────────────────────────────────────
# Claude CLI Backend
# ─────────────────────────────────────────────

class ClaudeCLI:
    """Backend para Claude CLI (comando 'claude')."""

    @property
    def name(self) -> str:
        return "claude"

    def is_available(self) -> bool:
        """Verifica si Claude CLI está instalado."""
        try:
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def execute(
        self,
        prompt: str,
        flags: Optional[List[str]] = None,
        step: str = "unknown",
        timeout: int = 1200,
    ) -> CLIResult:
        """Ejecuta claude -p con output JSON."""
        cmd = ["claude", "-p", prompt, "--output-format", "json"] + (flags or [])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return CLIResult(
                exit_code=1,
                text=f"timeout: claude CLI no respondió en {timeout}s",
                usage={},
                session_id=None,
                backend=self.name,
            )

        output = r.stdout.strip()
        session_id: Optional[str] = None
        usage: Dict = {}
        text = output

        try:
            data = json.loads(output)
            text = data.get("result", output)
            session_id = data.get("session_id") or data.get("sessionId")
            u = data.get("usage", {})
            usage = {
                "input": u.get("input_tokens", 0),
                "output": u.get("output_tokens", 0),
                "cache_read": u.get("cache_read_input_tokens", 0),
                "cache_write": u.get("cache_creation_input_tokens", 0),
                "cost_usd": data.get("total_cost_usd", 0.0),
                "duration_ms": data.get("duration_ms", 0),
            }
        except (json.JSONDecodeError, AttributeError):
            pass

        return CLIResult(
            exit_code=r.returncode,
            text=text,
            usage=usage,
            session_id=session_id,
            backend=self.name,
        )

    def execute_stream(
        self,
        prompt: str,
        flags: Optional[List[str]] = None,
        step: str = "unknown",
        on_line: Optional[Callable[[str], None]] = None,
    ) -> CLIResult:
        """Ejecuta claude -p con stream-json."""
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"] + (flags or [])
        raw_lines: List[str] = []

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            line = line.strip()
            if line:
                raw_lines.append(line)
                if on_line:
                    on_line(line)
        proc.wait()

        return CLIResult(
            exit_code=proc.returncode,
            text="\n".join(raw_lines),
            usage={},
            backend=self.name,
        )


# ─────────────────────────────────────────────
# Cursor CLI Backend
# ─────────────────────────────────────────────

class CursorCLI:
    """Backend para Cursor CLI (comando 'agent')."""

    @property
    def name(self) -> str:
        return "cursor"

    def is_available(self) -> bool:
        """Verifica si Cursor CLI está instalado y tiene API key."""
        if not os.environ.get("CURSOR_API_KEY"):
            return False
        try:
            result = subprocess.run(
                ["agent", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _map_flags(self, flags: Optional[List[str]]) -> List[str]:
        """Mapea flags de Claude CLI a Cursor CLI."""
        cursor_flags: List[str] = []

        if not flags:
            return cursor_flags

        i = 0
        while i < len(flags):
            flag = flags[i]

            if flag == "--dangerously-skip-permissions":
                cursor_flags.append("--force")
            elif flag == "--output-format":
                if i + 1 < len(flags):
                    cursor_flags.extend(["--output-format", flags[i + 1]])
                    i += 1
            elif flag == "--allowedTools":
                i += 1  # skip value
            elif flag == "--max-turns":
                i += 1  # skip value
            elif flag == "--resume":
                i += 1  # skip value
            elif flag == "--verbose":
                pass
            else:
                cursor_flags.append(flag)

            i += 1

        return cursor_flags

    def execute(
        self,
        prompt: str,
        flags: Optional[List[str]] = None,
        step: str = "unknown",
    ) -> CLIResult:
        """Ejecuta agent -p --force con output JSON."""
        cursor_flags = self._map_flags(flags)

        if "--force" not in cursor_flags:
            cursor_flags.insert(0, "--force")
        if "--output-format" not in cursor_flags:
            cursor_flags.extend(["--output-format", "json"])

        cmd = ["agent", "-p", *cursor_flags, prompt]
        r = subprocess.run(cmd, capture_output=True, text=True)

        output = r.stdout.strip()
        usage: Dict = {}
        text = output

        try:
            data = json.loads(output)
            text = data.get("result", output)
            usage = {
                "duration_ms": data.get("duration_ms", 0),
                "input": 0,
                "output": 0,
                "cost_usd": 0.0,
            }
        except (json.JSONDecodeError, AttributeError):
            pass

        return CLIResult(
            exit_code=r.returncode,
            text=text,
            usage=usage,
            session_id=None,
            backend=self.name,
        )

    def execute_stream(
        self,
        prompt: str,
        flags: Optional[List[str]] = None,
        step: str = "unknown",
        on_line: Optional[Callable[[str], None]] = None,
    ) -> CLIResult:
        """Ejecuta agent -p con stream-json."""
        cursor_flags = self._map_flags(flags)

        if "--force" not in cursor_flags:
            cursor_flags.insert(0, "--force")
        cursor_flags = [f for f in cursor_flags if f not in ("--output-format", "json")]
        cursor_flags.extend(["--output-format", "stream-json"])

        cmd = ["agent", "-p", *cursor_flags, prompt]
        raw_lines: List[str] = []

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            line = line.strip()
            if line:
                raw_lines.append(line)
                if on_line:
                    on_line(line)
        proc.wait()

        return CLIResult(
            exit_code=proc.returncode,
            text="\n".join(raw_lines),
            usage={},
            backend=self.name,
        )


# ─────────────────────────────────────────────
# Fallback Backend (auto-switch)
# ─────────────────────────────────────────────

BACKEND_SWITCH_LOG = "BACKEND_SWITCH_LOG.json"
_log_lock = threading.Lock()


def _append_switch_log(entry: Dict) -> None:
    """Registra un switch de backend en el log."""
    with _log_lock:
        log_data: List = []
        log_path = Path(BACKEND_SWITCH_LOG)
        try:
            if log_path.exists():
                log_data = json.loads(log_path.read_text())
                if not isinstance(log_data, list):
                    log_data = []
        except (json.JSONDecodeError, ValueError):
            log_data = []

        log_data.append(entry)
        log_path.write_text(json.dumps(log_data, indent=2))


class FallbackBackend:
    """Backend con fallback automático de Claude CLI a Cursor CLI."""

    def __init__(
        self,
        primary: Optional[CLIBackend] = None,
        fallback: Optional[CLIBackend] = None,
        auto_switch: bool = True,
    ):
        self._primary = primary or ClaudeCLI()
        self._fallback = fallback or CursorCLI()
        self._auto_switch = auto_switch
        self._current: CLIBackend = self._primary
        self._switched = False

    @property
    def name(self) -> str:
        return f"fallback({self._current.name})"

    @property
    def current_backend(self) -> CLIBackend:
        return self._current

    @property
    def has_switched(self) -> bool:
        return self._switched

    def is_available(self) -> bool:
        """Retorna True si al menos un backend está disponible."""
        return self._primary.is_available() or self._fallback.is_available()

    def _switch_to_fallback(self, reason: str, step: str) -> bool:
        """Intenta cambiar al backend de fallback."""
        if self._switched:
            return False

        if not self._fallback.is_available():
            return False

        entry = {
            "event": "backend_switch",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "from": self._primary.name,
            "to": self._fallback.name,
            "reason": reason,
            "step": step,
        }
        _append_switch_log(entry)

        self._current = self._fallback
        self._switched = True
        print(f"  🔄 Switched to {self._fallback.name} backend ({reason})")
        return True

    def execute(
        self,
        prompt: str,
        flags: Optional[List[str]] = None,
        step: str = "unknown",
    ) -> CLIResult:
        """Ejecuta con fallback automático si hay token exhaustion."""
        result = self._current.execute(prompt, flags, step)

        if self._auto_switch and result.token_exhausted and not self._switched:
            if self._switch_to_fallback("token_exhausted", step):
                result = self._current.execute(prompt, flags, step)

        return result

    def execute_stream(
        self,
        prompt: str,
        flags: Optional[List[str]] = None,
        step: str = "unknown",
        on_line: Optional[Callable[[str], None]] = None,
    ) -> CLIResult:
        """Ejecuta streaming con fallback automático."""
        result = self._current.execute_stream(prompt, flags, step, on_line)

        if self._auto_switch and result.token_exhausted and not self._switched:
            if self._switch_to_fallback("token_exhausted", step):
                result = self._current.execute_stream(prompt, flags, step, on_line)

        return result


# ─────────────────────────────────────────────
# Factory functions
# ─────────────────────────────────────────────

_default_backend: Optional[CLIBackend] = None


def get_default_backend(
    prefer_cursor: bool = False,
    enable_fallback: bool = True,
) -> CLIBackend:
    """Obtiene el backend por defecto (singleton con lazy init)."""
    global _default_backend

    if _default_backend is not None:
        return _default_backend

    if prefer_cursor:
        primary = CursorCLI()
        fallback = ClaudeCLI()
    else:
        primary = ClaudeCLI()
        fallback = CursorCLI()

    if enable_fallback:
        _default_backend = FallbackBackend(primary, fallback, auto_switch=True)
    else:
        _default_backend = primary

    return _default_backend


def reset_default_backend() -> None:
    """Resetea el backend por defecto (útil para tests)."""
    global _default_backend
    _default_backend = None


def set_default_backend(backend: CLIBackend) -> None:
    """Establece un backend por defecto específico."""
    global _default_backend
    _default_backend = backend
