"""Bandeja del archivista: guarda en la bóveda las lecciones que dejan las sesiones al cerrar.

learn.py corre en la máquina de cada uno (Windows también) y no puede llamar a Hermes, que solo
corre donde hay login de ChatGPT y la bóveda clonada. Entonces learn.py deja cada lección en la
cola del server y este worker, en una máquina con Hermes, la levanta:

  GET  /api/lessons?status=pending&limit=20
  -> archivista (hermes -p archivista -z "Autor: ...")
  -> commit y push de conocimiento/ y privado/ a build101-vault
  -> POST /api/lessons/{id}/done  {"nota": "conocimiento/..."}  o  {"descartada": "motivo"}

Si el archivista falla, la lección queda pendiente y se reintenta en la próxima vuelta; a los
MAX_FAILURES intentos se descarta con el motivo. Solo biblioteca estándar, Python 3.9+.

Uso:       python3 agent/hermes/bandeja.py            (escucha, una vuelta cada INTERVAL segundos)
           python3 agent/hermes/bandeja.py --once     (una vuelta y sale)
Entorno:   BUILD101_KEY (obligatoria), BUILD101_URL, VAULT (default ../build101-vault),
           BANDEJA_INTERVAL (default 120), BANDEJA_ARCHIVISTA=claude (Claude Code en vez de Hermes)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_URL = "https://54-89-124-87.sslip.io/api/hooks/claude-code"
HOOK_PATH = "/api/hooks/claude-code"
STATE = Path(os.path.expanduser("~/.claude/build101/bandeja.json"))
TYPES = ("gotcha", "receta", "decision", "criterio", "recurso")
MAX_FAILURES = 3
HERMES_TIMEOUT = 600
HTTP_TIMEOUT = 15


def api_base(environ: dict) -> str:
    url = environ.get("BUILD101_URL", DEFAULT_URL).rstrip("/")
    return url[: -len(HOOK_PATH)] if url.endswith(HOOK_PATH) else url


class Api:
    def __init__(self, base: str, key: str, urlopen=urllib.request.urlopen):
        self.base, self.key, self.urlopen = base, key, urlopen

    def call(self, method: str, path: str, body: dict | None = None):
        request = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            method=method,
        )
        with self.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read() or b"{}")

    def pending(self, limit: int = 20) -> list[dict]:
        answer = self.call(
            "GET", "/api/lessons?" + urllib.parse.urlencode({"status": "pending", "limit": limit})
        )
        lessons = answer.get("lessons", answer) if isinstance(answer, dict) else answer
        return [lesson for lesson in lessons or [] if isinstance(lesson, dict) and "id" in lesson]

    def done(self, lesson_id, result: dict) -> None:
        self.call("POST", f"/api/lessons/{lesson_id}/done", result)


# El server puede mandar los nombres en español (como los propuso Nico) o en inglés
ALIASES = {"autor": "author", "archivos": "files"}


def normalize(lesson: dict) -> dict:
    out = dict(lesson)
    for alias, field in ALIASES.items():
        if alias in out and field not in out:
            out[field] = out.pop(alias)
    return out


def prompt_for(lesson: dict) -> str:
    """El mensaje que recibe el archivista: arranca con Autor, como pide su SOUL.md."""
    lesson = normalize(lesson)
    lines = [f"Autor: {lesson.get('author') or 'equipo'}"]
    lines.append(
        "Lección que dejó una sesión de Claude Code al cerrar. No hay a quién preguntar: "
        "si algo es ambiguo, decidí. Si no le sirve a nadie más del equipo, no la guardes y "
        "respondé solo DESCARTADA: <motivo>."
    )
    fuente = lesson.get("fuente") or "sesión de Claude Code"
    if lesson.get("repo"):
        fuente += f" en {lesson['repo']}"
    lines.append(f"fuente: {fuente}")
    if lesson.get("tipo") in TYPES:
        lines.append(f"tipo sugerido: {lesson['tipo']}")
    for field in ("titulo", "aplica_cuando", "skill"):
        if lesson.get(field):
            lines.append(f"{field}: {lesson[field]}")
    if lesson.get("files"):
        lines.append("archivos: " + ", ".join(str(f) for f in lesson["files"][:10]))
    lines += ["", str(lesson.get("texto") or "").strip()]
    return "\n".join(lines)


def git(vault: Path, *args: str, run=subprocess.run) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(vault), *args], capture_output=True, text=True)


def note_type(path: Path) -> str:
    try:
        head = path.read_text(encoding="utf-8")[:500]
    except OSError:
        return ""
    for line in head.splitlines()[1:]:
        if line.startswith("tipo:"):
            return line[5:].strip()
        if line.strip() == "---":
            break
    return ""


def changed_notes(vault: Path, run=subprocess.run) -> list[str]:
    """Notas tocadas en conocimiento/: las nuevas primero, y los componentes al final, porque el
    archivista suele crear el del componente al enlazar la nota. MEMORY.md y las personas no son la nota."""
    # -z: sin comillas ni escapes octales en los nombres con acentos o espacios
    out = git(vault, "status", "--porcelain", "-z", "--untracked-files=all", "--", "conocimiento", run=run)
    ranked = []
    for entry in out.stdout.split("\0"):
        status, path = entry[:2], entry[3:]
        if not path.endswith(".md") or path.endswith("MEMORY.md") or "/Equipo/" in path:
            continue
        is_new = "?" in status or "A" in status
        ranked.append((not is_new, note_type(vault / path) == "componente", path))
    return [path for *_, path in sorted(ranked)]


def git_changed(vault: Path, folder: str, run=subprocess.run) -> bool:
    """Algo staged en esa carpeta (MEMORY.md incluido), aunque no sea una nota nueva."""
    return git(vault, "diff", "--cached", "--quiet", "--", folder, run=run).returncode != 0


# Lo que el archivista necesita de Claude Code: leer y escribir notas, y `date +%F` para el registro
CLAUDE_TOOLS = "Read,Write,Edit,Grep,Glob,Bash(date:*)"


def archivist_call(prompt: str, vault: Path, environ=os.environ) -> tuple[list[str], dict]:
    """Hermes por defecto. Con BANDEJA_ARCHIVISTA=claude, Claude Code parado en la bóveda con el mismo
    SOUL.md: así la cola corre en cualquier máquina con `claude` (Windows también), no solo con Hermes."""
    if environ.get("BANDEJA_ARCHIVISTA") != "claude":
        return ["hermes", "-p", "archivista", "-z", prompt], {"text": True}
    soul = (HERE / "archivista" / "SOUL.md").read_text(encoding="utf-8")
    # Todo va por stdin: un argumento de varias líneas no sobrevive a claude.cmd en Windows
    message = f"{soul}\n\n---\n\n{prompt}"
    claude = shutil.which("claude") or "claude"
    command = [claude, "-p", "--allowedTools", CLAUDE_TOOLS, "--permission-mode", "acceptEdits"]
    return command, {"input": message, "cwd": str(vault), "text": True, "encoding": "utf-8"}


def file_one(lesson: dict, vault: Path, run=subprocess.run) -> dict | None:
    """Le pasa una lección al archivista y sube lo que escribió. None si falló y hay que reintentar."""
    lesson = normalize(lesson)
    author = lesson.get("author") or "equipo"
    try:
        command, options = archivist_call(prompt_for(lesson), vault)
        answer = run(command, capture_output=True, timeout=HERMES_TIMEOUT, **options)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if answer.returncode != 0:
        return None
    notes = changed_notes(vault, run=run)
    # Solo viajan las carpetas que escribe el archivista; git add falla entero si falta una
    folders = [name for name in ("conocimiento", "privado") if (vault / name).is_dir()]
    git(vault, "add", "--", *folders, run=run)
    if not notes and not git_changed(vault, "conocimiento", run=run):
        # No escribió ninguna nota (a lo sumo anotó en el registro): es un descarte
        reply = (answer.stdout or "").strip()
        motivo = reply.split("DESCARTADA:", 1)[1].strip() if "DESCARTADA:" in reply else reply
        result = {"descartada": (motivo or "el archivista no escribió ninguna nota")[:300]}
        if git(vault, "diff", "--cached", "--quiet", run=run).returncode != 0:
            git(vault, "-c", f"user.name={author}", "commit", "-q", "-m", f"{author}: registro", run=run)
            git(vault, "push", "-q", run=run)
        return result
    title = str(lesson.get("titulo") or lesson.get("texto") or "lección")
    message = f"{author}: {' '.join(title.split())[:60]}"
    git(vault, "-c", f"user.name={author}", "commit", "-q", "-m", message, run=run)
    if git(vault, "push", "-q", run=run).returncode != 0:
        # Alguien subió algo mientras tanto. Si tampoco sale, queda commiteado y sube con el próximo push
        git(vault, "pull", "--rebase", "-q", run=run)
        git(vault, "push", "-q", run=run)
    return {"nota": notes[0]} if notes else {"nota": "conocimiento/MEMORY.md"}


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state))
    except OSError:
        pass


def round_once(api: Api, vault: Path, run=subprocess.run, log=print) -> int:
    """Una vuelta: trae lo último de la bóveda y procesa las pendientes. Devuelve cuántas cerró."""
    pull = git(vault, "pull", "--rebase", "--autostash", "-q", run=run)
    if pull.returncode != 0:
        log(f"no pude traer la bóveda: {pull.stderr.strip()[:200]}")
    try:
        lessons = api.pending()
    except (urllib.error.URLError, OSError, ValueError) as error:
        log(f"no pude leer la cola: {error}")
        return 0
    state = load_state()
    closed = 0
    for lesson in lessons:
        key = str(lesson["id"])
        result = file_one(lesson, vault, run=run)
        if result is None:
            state[key] = state.get(key, 0) + 1
            if state[key] < MAX_FAILURES:
                log(f"lección {key}: falló el archivista ({state[key]}/{MAX_FAILURES}), reintento después")
                continue
            result = {"descartada": f"el archivista falló {MAX_FAILURES} veces"}
        try:
            api.done(lesson["id"], result)
        except (urllib.error.URLError, OSError, ValueError) as error:
            log(f"lección {key}: guardada pero no pude marcarla en el server: {error}")
            continue
        state.pop(key, None)
        closed += 1
        log(f"lección {key}: {result}")
    save_state(state)
    return closed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--once", action="store_true", help="una vuelta y sale")
    args = parser.parse_args(argv)
    key = os.environ.get("BUILD101_KEY")
    if not key:
        print("Falta BUILD101_KEY", file=sys.stderr)
        return 1
    vault = Path(os.environ.get("VAULT") or REPO.parent / "build101-vault")
    if not (vault / "reglas" / "AGENTS.md").is_file():
        print(f"No encuentro la bóveda en {vault}", file=sys.stderr)
        return 1
    api = Api(api_base(os.environ), key)
    interval = int(os.environ.get("BANDEJA_INTERVAL", "120"))
    while True:
        round_once(api, vault)
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
