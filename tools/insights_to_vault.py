"""Manda a la bóveda del equipo lo que /insights encontró que le sirve a otro: oportunidades,
sesiones estrella y lo compartible.

Lee resultado.json (el que armó insights_export.py, ya sin tokens, IPs, mails ni rutas) y deja
cada hallazgo en la cola de lecciones del server (POST /api/lessons, fuente "insights"). El
archivista (agent/hermes/bandeja.py) decide si lo guarda, dónde, y lo enlaza con lo que ya hay.
Los números no van a la bóveda: esos son del tablero.

Uso:      python3 tools/insights_to_vault.py --resultado ~/.claude/session-insights/resultado.json [--dry-run]
Entorno:  BUILD101_KEY (obligatoria), BUILD101_URL
Solo biblioteca estándar, Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from insights_paths import local_path

DEFAULT_URL = "https://54-89-124-87.sslip.io/api/hooks/claude-code"
HOOK_PATH = "/api/hooks/claude-code"
FUENTE = "insights"
# Lo que se construye con cada tipo de oportunidad, para que el archivista lo nombre bien
CONSTRUIR = {
    "skill": "una skill",
    "tool": "un script o herramienta",
    "subagente": "un subagente",
    "mcp": "un MCP",
    "hook": "un hook",
    "regla": "una regla en CLAUDE.md",
    "app": "una app",
    "adoptar": "adoptar algo que ya existe",
}


def api_base(environ: dict) -> str:
    url = environ.get("BUILD101_URL", DEFAULT_URL).rstrip("/")
    return url[: -len(HOOK_PATH)] if url.endswith(HOOK_PATH) else url


def lessons(resultado: dict) -> list[dict]:
    """Una lección por hallazgo. tipo y titulo son sugerencias: decide el archivista."""
    persona = resultado["persona"]
    periodo = resultado.get("periodo") or {}
    cuando = f"{periodo.get('desde', '?')} a {periodo.get('hasta', '?')}"
    out = []
    for o in resultado.get("oportunidades", []):
        out.append(
            {
                "titulo": o["que"],
                "texto": (
                    f"Oportunidad que salió del análisis de uso de Claude de {persona} ({cuando}): "
                    f"{o['que']}. Conviene {CONSTRUIR.get(o.get('tipo'), o.get('tipo', ''))}. "
                    f"Tema {o['tema']}, trabajo {o['trabajo']}: {o.get('sesiones')} sesiones, "
                    f"{o.get('horas')} h activas, prioridad O = {o.get('O')}. Evidencia: {o['evidencia']}. "
                    "Si otra persona ya tiene una nota con el mismo tema, sumalo ahí: que se repita "
                    "entre personas es lo que la hace prioritaria."
                ),
            }
        )
    for e in resultado.get("estrellas", []):
        out.append(
            {
                "tipo": "receta",
                "titulo": e["titulo"],
                "texto": (
                    f"Sesión estrella de {persona} en {e['trabajo']} ({cuando}): {e['como']} "
                    f"Evidencia: {e['evidencia']}. Guardala como la forma que funciona, para copiarla."
                ),
            }
        )
    for c in resultado.get("compartible", []):
        out.append(
            {
                "tipo": "recurso",
                "titulo": c["que"],
                "texto": f"{persona} tiene {c['que']} ({c['tipo']}), en {c['donde']}. Le sirve al resto.",
            }
        )
    for lesson in out:
        lesson["fuente"] = FUENTE
        lesson["texto"] = lesson["texto"][:4000]
        lesson["titulo"] = lesson["titulo"][:200]
    return out


def post(base: str, key: str, lesson: dict, urlopen=urllib.request.urlopen) -> dict:
    request = urllib.request.Request(
        base + "/api/lessons",
        data=json.dumps(lesson, ensure_ascii=False).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read() or b"{}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--resultado", required=True)
    ap.add_argument("--dry-run", action="store_true", help="muestra lo que mandaría, sin mandar")
    a = ap.parse_args(argv)
    with open(local_path(a.resultado), encoding="utf-8") as fh:
        todo = lessons(json.load(fh))
    if a.dry_run:
        print(json.dumps(todo, ensure_ascii=False, indent=1))
        return 0
    key = os.environ.get("BUILD101_KEY")
    if not key:
        print("Falta BUILD101_KEY", file=sys.stderr)
        return 1
    base, sent = api_base(os.environ), 0
    for lesson in todo:
        try:
            post(base, key, lesson)
            sent += 1
        except (urllib.error.URLError, OSError, ValueError) as error:
            print(f"no pude mandar «{lesson['titulo']}»: {error}", file=sys.stderr)
    print(f"{sent} de {len(todo)} hallazgos en la cola del archivista")
    return 0 if sent == len(todo) else 1


if __name__ == "__main__":
    sys.exit(main())
