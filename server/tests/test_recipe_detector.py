import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("recipe_detector", ROOT / "tools" / "recipe_detector.py")
detector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(detector)


def card(session_id, intencion, resultado="parcial", capital=(), pasos=()):
    return {
        "session_id": session_id,
        "trabajo": "documento-cliente",
        "intencion": intencion,
        "resultado": resultado,
        "capital_usado": list(capital),
        "pasos_repetidos": list(pasos),
        "podria_ayudar": [{"tipo": "skill", "que": "Skill de nota semanal", "por_que": "se repite"}],
    }


def fact(session_id, errors=0, start="2026-10-12T09:00:00"):
    return {
        "session_id": session_id,
        "start": start,
        "active_min": 30,
        "tool_calls": 40,
        "tool_errors": errors,
    }


WEEKLY = [
    card("s1", "escribir nota semanal de noticias de IA", pasos=["buscar noticias de la semana"]),
    card("s2", "armar la nota semanal de noticias IA", resultado="resuelto", capital=["skill:noticias-ia"]),
    card("s3", "escribir notas semanales con noticias de IA", pasos=["buscar noticias de la semana"]),
    card("s4", "depurar el forwarder de hooks"),
]
FACTS = {
    f["session_id"]: f
    for f in [fact("s1", 5), fact("s2", 1, "2026-10-19T09:00:00"), fact("s3", 8), fact("s4")]
}


def write(folder, cards, facts):
    (folder / "cards.jsonl").write_text("\n".join(json.dumps(c) for c in cards) + "\n", encoding="utf-8")
    (folder / "facts.jsonl").write_text("\n".join(json.dumps(f) for f in facts) + "\n", encoding="utf-8")


def test_same_intent_in_other_words_groups_together():
    found = [g for g in detector.groups(WEEKLY) if len(g) >= 3]
    assert len(found) == 1
    assert sorted(c["session_id"] for c in found[0]) == ["s1", "s2", "s3"]


def test_recipe_names_the_best_session_and_its_skill():
    group = [c for c in WEEKLY if c["session_id"] != "s4"]
    lesson = detector.recipe(group, FACTS, "beto")
    assert lesson["tipo"] == "receta" and lesson["fuente"] == "patrones de uso"
    assert lesson["skill"] == "noticias-ia"
    assert "beto repite esto en 3 sesiones (1.5 h activas" in lesson["texto"]
    # La resuelta manda aunque haya otras: es la forma que funcionó
    assert "La que mejor salió (2026-10-19, resuelto" in lesson["texto"]
    assert "buscar noticias de la semana" in lesson["texto"]
    assert "noticia" in lesson["aplica_cuando"] and "semanal" in lesson["aplica_cuando"]


def test_without_a_real_name_the_text_has_no_name():
    group = [c for c in WEEKLY if c["session_id"] != "s4"]
    assert detector.recipe(group, FACTS, "me")["texto"].startswith("Esto se repite en 3 sesiones")


def test_sent_group_is_not_sent_again_when_it_grows(tmp_path, monkeypatch):
    write(tmp_path, WEEKLY, FACTS.values())
    sent = []
    monkeypatch.setattr(detector, "post", lambda base, key, lesson: sent.append(lesson))
    environ = {"BUILD101_KEY": "k"}
    assert detector.run(str(tmp_path), "beto", environ, log=lambda _: None) == 1
    more = [*WEEKLY, card("s5", "escribir la nota semanal de IA")]
    write(tmp_path, more, [*FACTS.values(), fact("s5")])
    assert detector.run(str(tmp_path), "beto", environ, log=lambda _: None) == 0
    assert len(sent) == 1


def test_dry_run_sends_nothing(tmp_path, monkeypatch):
    write(tmp_path, WEEKLY, FACTS.values())
    monkeypatch.setattr(detector, "post", lambda *a: (_ for _ in ()).throw(AssertionError("mandó")))
    shown = []
    assert detector.run(str(tmp_path), "beto", {}, dry_run=True, log=shown.append) == 1
    assert json.loads(shown[0])["tipo"] == "receta"
    assert not (tmp_path / "recetas_enviadas.json").exists()
