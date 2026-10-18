import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


paths = load("insights_paths")
export = load("insights_export")
to_vault = load("insights_to_vault")

RESULTADO = {
    "persona": "molle",
    "metodo": "dik-insights-1",
    "periodo": {"desde": "2026-09-01", "hasta": "2026-10-18"},
    "tarjeta": {"sesiones": 40, "horas_activas": 31.5, "K_capitalizacion": 0.21, "F_friccion": 6.3},
    "tesis": ["El deploy se lleva 9 h con K 0,03."],
    "oportunidades": [
        {
            "tema": "deploy-vps",
            "que": "Skill de deploy con chequeo de estado",
            "tipo": "skill",
            "trabajo": "desplegar-infra",
            "evidencia": "K 0,03 en 7 deploys",
            "sesiones": 7,
            "horas": 9.0,
            "O": 120,
        }
    ],
    "estrellas": [
        {
            "titulo": "Revisión con subagente",
            "trabajo": "desarrollar",
            "como": "Pide un plan a un subagente y después edita.",
            "evidencia": "S 2,1",
        }
    ],
    "riesgos": [{"que": "Push a main | sin PR", "accion": "Hook que lo frene"}],
    "compartible": [{"que": "Script de backfill", "tipo": "tool", "donde": "tools/backfill"}],
}


def test_git_bash_paths_become_windows_paths():
    assert paths.local_path("/c/Users/nico/.claude/x.jsonl", windows=True) == "C:/Users/nico/.claude/x.jsonl"
    assert paths.local_path("/d", windows=True) == "D:"


def test_posix_paths_are_left_alone():
    assert paths.local_path("/c/Users/x", windows=False) == "/c/Users/x"
    assert paths.local_path("/cards/x", windows=True) == "/cards/x"


def test_markdown_has_the_card_and_the_findings():
    md = export.to_markdown(RESULTADO)
    assert md.startswith("# Uso de Claude: molle\n")
    assert "| K · capitalización | 0.21 |" in md
    assert "| R · resueltas | — |" in md
    assert "| deploy-vps | Skill de deploy con chequeo de estado | skill | 7 | 9.0 | 120 |" in md
    assert "**Revisión con subagente** (desarrollar)" in md
    # Una barra en el texto no rompe la tabla ni la lista
    assert "Push a main / sin PR" in md


def test_every_finding_becomes_one_lesson_from_insights():
    lessons = to_vault.lessons(RESULTADO)
    assert [lesson.get("tipo") for lesson in lessons] == [None, "receta", "recurso"]
    assert all(lesson["fuente"] == "insights" for lesson in lessons)
    assert "deploy-vps" in lessons[0]["texto"] and "una skill" in lessons[0]["texto"]
    # Lo que el server valida: el texto entra en 4000 caracteres
    assert all(len(json.dumps(lesson)) < 4000 for lesson in lessons)


def test_dry_run_sends_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("BUILD101_KEY", raising=False)
    path = tmp_path / "resultado.json"
    path.write_text(json.dumps(RESULTADO), encoding="utf-8")
    assert to_vault.main(["--resultado", str(path), "--dry-run"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 3


digest = load("session_digest")


def test_rerun_with_skip_numbers_batches_after_the_existing_cards(tmp_path, monkeypatch):
    def fact(session_id):
        return {
            "session_id": session_id,
            "start": "2026-10-18T10:00:00",
            "prompts": ["hola"],
            "human_prompts": 1,
            "tool_calls": 0,
        }

    (tmp_path / "cards_0.jsonl").write_text(json.dumps({"session_id": "vieja"}) + "\n", encoding="utf-8")
    (tmp_path / "batch_0.jsonl").write_text("ya clasificado\n", encoding="utf-8")
    facts = tmp_path / "facts.jsonl"
    facts.write_text("\n".join(json.dumps(fact(s)) for s in ("vieja", "nueva")) + "\n", encoding="utf-8")
    monkeypatch.setattr(digest, "digest", lambda r: {"session_id": r["session_id"]})
    monkeypatch.setattr(
        sys,
        "argv",
        ["x", "--facts", str(facts), "--out-dir", str(tmp_path), "--skip", str(tmp_path / "cards_*.jsonl")],
    )
    digest.main()
    assert sorted(p.name for p in tmp_path.glob("batch_*.jsonl")) == ["batch_1.jsonl"]
    assert json.loads((tmp_path / "batch_1.jsonl").read_text())["session_id"] == "nueva"
