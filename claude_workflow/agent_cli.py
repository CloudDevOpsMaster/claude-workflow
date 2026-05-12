#!/usr/bin/env python3
"""
agent_cli.py
Comando CLI simple y composable para ejecutar prompts AI contra cualquier repo.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List

from claude_workflow.cli_backend import get_default_backend


def _build_flags(mode: str, force: bool) -> List[str]:
    """Traduce mode y force a flags del backend."""
    if mode == "ask":
        flags: List[str] = ["--max-turns", "5"]
    else:  # code
        flags = ["--max-turns", "20", "--dangerously-skip-permissions"]

    if force and "--dangerously-skip-permissions" not in flags:
        flags.append("--dangerously-skip-permissions")

    return flags


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ejecuta un prompt AI contra el repositorio actual.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  agent -p "Resume en una frase qué hace este repo." --mode=ask --output-format text
  agent -p "Responde solo: OK si puedes leer este mensaje." --mode=ask --output-format json
  agent -p "Describe package.json sin modificar nada." --mode=ask --force --output-format json
        """,
    )
    parser.add_argument("-p", "--prompt", required=True, help="Prompt a enviar al AI")
    parser.add_argument(
        "--mode",
        choices=["ask", "code"],
        default="ask",
        help="ask: solo lectura (--max-turns 5); code: agente completo (--max-turns 20 + permisos). Default: ask",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        dest="output_format",
        help="text: resultado plano; json: estructura con metadata. Default: text",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Omite prompts de permisos (--dangerously-skip-permissions)",
    )

    args = parser.parse_args()
    flags = _build_flags(args.mode, args.force)
    backend = get_default_backend()

    try:
        result = backend.execute(args.prompt, flags=flags, step="agent-cli")
    except Exception as exc:
        error_msg = f"Backend error: {exc}"
        if args.output_format == "json":
            print(json.dumps({"result": error_msg, "exit_code": 1, "session_id": None, "usage": {}}))
        else:
            print(error_msg, file=sys.stderr)
        sys.exit(1)

    if args.output_format == "json":
        print(json.dumps({
            "result": result.text,
            "exit_code": result.exit_code,
            "session_id": result.session_id,
            "usage": result.usage,
        }))
    else:
        print(result.text)

    sys.exit(result.exit_code)


if __name__ == "__main__":
    main()
