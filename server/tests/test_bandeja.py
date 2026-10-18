import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("bandeja", ROOT / "agent" / "hermes" / "bandeja.py")
bandeja = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bandeja)


def sh(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Una bóveda con remoto propio, como build101-vault clonada."""
    monkeypatch.setattr(bandeja, "STATE", tmp_path / "state.json")
    remote = tmp_path / "remote.git"
    sh("git", "init", "-q", "--bare", "-b", "main", str(remote))
    work = tmp_path / "vault"
    sh("git", "clone", "-q", str(remote), str(work))
    (work / "reglas").mkdir()
    (work / "reglas" / "AGENTS.md").write_text("reglas")
    (work / "conocimiento").mkdir()
    (work / "conocimiento" / "MEMORY.md").write_text("# Índice\n")
    sh("git", "add", ".", cwd=work)
    sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=work)
    sh("git", "push", "-q", "origin", "HEAD:main", cwd=work)
    sh("git", "branch", "-q", "--set-upstream-to=origin/main", cwd=work)
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t")
    return work


def fake_hermes(vault, writes=True, returncode=0, stdout="listo"):
    """run() que corre git de verdad y reemplaza a hermes por algo que escribe una nota."""
    prompts = []

    def run(args, **kwargs):
        if args[0] != "hermes":
            return subprocess.run(args, **kwargs)
        prompts.append(args[-1])
        if writes:
            component = vault / "conocimiento" / "Componentes" / "Servidor API.md"
            component.parent.mkdir(exist_ok=True)
            component.write_text("---\ntipo: componente\n---\n")
            note = vault / "conocimiento" / "Gotchas" / "Python 3.9 en el forwarder.md"
            note.parent.mkdir(exist_ok=True)
            note.write_text("---\ntipo: gotcha\n---\n")
            with (vault / "conocimiento" / "MEMORY.md").open("a") as index:
                index.write("- [[Python 3.9 en el forwarder]]\n")
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    return run, prompts


class FakeApi:
    def __init__(self, lessons):
        self.lessons, self.closed = lessons, []

    def pending(self, limit=20):
        return [lesson for lesson in self.lessons if lesson["id"] not in {i for i, _ in self.closed}]

    def done(self, lesson_id, result):
        self.closed.append((lesson_id, result))


LESSON = {
    "id": 7,
    "author": "nico",
    "repo": "hackatonbuild101",
    "tipo": "gotcha",
    "titulo": "El forwarder falla en Python 3.9",
    "texto": "str | None rompe en 3.9; usar Optional.",
    "aplica_cuando": "al tocar agent/claude-code/forward.py",
    "files": ["agent/claude-code/forward.py"],
}


def test_prompt_starts_with_author_and_carries_the_fields():
    prompt = bandeja.prompt_for(LESSON)
    assert prompt.startswith("Autor: nico\n")
    assert "tipo sugerido: gotcha" in prompt
    assert "aplica_cuando: al tocar agent/claude-code/forward.py" in prompt
    assert prompt.rstrip().endswith("usar Optional.")


def test_spanish_field_names_are_accepted():
    lesson = {"id": 1, "autor": "nico", "archivos": ["server/api/vault.py"], "texto": "x"}
    prompt = bandeja.prompt_for(lesson)
    assert prompt.startswith("Autor: nico\n")
    assert "archivos: server/api/vault.py" in prompt


def test_unknown_type_is_not_suggested():
    assert "tipo sugerido" not in bandeja.prompt_for({**LESSON, "tipo": "dato"})


def test_filed_lesson_is_pushed_and_marked_with_its_note(vault):
    run, prompts = fake_hermes(vault)
    api = FakeApi([LESSON])
    assert bandeja.round_once(api, vault, run=run, log=lambda _: None) == 1
    assert api.closed == [(7, {"nota": "conocimiento/Gotchas/Python 3.9 en el forwarder.md"})]
    log = subprocess.run(
        ["git", "-C", str(vault), "log", "origin/main", "-1", "--format=%an|%s"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert log == "nico|nico: El forwarder falla en Python 3.9"


def test_accented_note_names_come_back_unescaped(vault):
    note = vault / "conocimiento" / "Línea de base.md"
    note.write_text("---\ntipo: criterio\n---\n", encoding="utf-8")
    assert bandeja.changed_notes(vault) == ["conocimiento/Línea de base.md"]


def test_lesson_the_archivist_drops_is_discarded_with_its_reason(vault):
    run, _ = fake_hermes(vault, writes=False, stdout="DESCARTADA: es progreso, no aprendizaje")
    api = FakeApi([LESSON])
    bandeja.round_once(api, vault, run=run, log=lambda _: None)
    assert api.closed == [(7, {"descartada": "es progreso, no aprendizaje"})]


def test_failed_archivist_retries_then_discards(vault):
    run, _ = fake_hermes(vault, writes=False, returncode=1)
    api = FakeApi([LESSON])
    for _ in range(bandeja.MAX_FAILURES - 1):
        assert bandeja.round_once(api, vault, run=run, log=lambda _: None) == 0
    assert api.closed == []
    bandeja.round_once(api, vault, run=run, log=lambda _: None)
    assert api.closed == [(7, {"descartada": f"el archivista falló {bandeja.MAX_FAILURES} veces"})]
    assert json.loads(bandeja.STATE.read_text()) == {}


def test_api_base_strips_the_hook_path():
    assert bandeja.api_base({}) == "https://54-89-124-87.sslip.io"
    assert bandeja.api_base({"BUILD101_URL": "http://localhost:8000/"}) == "http://localhost:8000"


def test_lesson_that_only_touches_the_log_is_discarded(vault):
    def run(args, **kwargs):
        if args[0] != "hermes":
            return subprocess.run(args, **kwargs)
        (vault / "privado").mkdir(exist_ok=True)
        (vault / "privado" / "registro.md").write_text("- 2026-10-18 · nico · mensaje de estado\n")
        return subprocess.CompletedProcess(args, 0, "Es un aviso de estado, no guardé nada.", "")

    api = FakeApi([LESSON])
    bandeja.round_once(api, vault, run=run, log=lambda _: None)
    assert api.closed == [(7, {"descartada": "Es un aviso de estado, no guardé nada."})]


def test_claude_archivist_gets_the_soul_on_stdin_inside_the_vault(vault):
    command, options = bandeja.archivist_call("Autor: nico\n\nhola", vault, {"BANDEJA_ARCHIVISTA": "claude"})
    assert "-p" in command and "hermes" not in command[0]
    assert options["cwd"] == str(vault)
    assert options["input"].startswith("# Archivista") and options["input"].endswith("Autor: nico\n\nhola")


def test_hermes_stays_the_default_archivist(vault):
    command, _ = bandeja.archivist_call("Autor: nico", vault, {})
    assert command[:3] == ["hermes", "-p", "archivista"]
