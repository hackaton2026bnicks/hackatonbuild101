"""The dashboard's side of /insights and of the vault: what each person found, and whether the team learns."""

import pytest
from test_collab import ANA_REPO, hook
from test_vault import NOTES

from api import collab
from api.vault import Vault


def resultado(persona, hasta="2026-10-18", **extra):
    """The shape tools/insights_export.py writes."""
    return {
        "persona": persona,
        "metodo": "dik-insights-1",
        "periodo": {"desde": "2026-10-01", "hasta": hasta},
        "tarjeta": {"sesiones": 13, "horas_activas": 21.5, "K_capitalizacion": 0.31, "F_friccion": 6.2},
        "oportunidades": [],
        "estrellas": [],
        **extra,
    }


def opportunity(tema, que, size=10, tipo="skill"):
    return {"tema": tema, "que": que, "tipo": tipo, "trabajo": "desarrollar", "evidencia": "3 sesiones",
            "sesiones": 3, "horas": 2.5, "O": size}  # fmt: skip


def test_each_person_keeps_their_newest_period_and_reuploading_corrects_it(client, make_key):
    nico = make_key("nico")
    assert client.post("/insights", json=resultado("nico", "2026-10-11"), headers=nico).status_code == 201
    first = client.post("/insights", json=resultado("nico"), headers=nico).json()
    assert first["persona"] == "nico" and first["uploaded_by"] == "nico"
    fixed = resultado("nico", tarjeta={"sesiones": 14})
    client.post("/insights", json=fixed, headers=nico)

    [latest] = client.get("/insights", headers=nico).json()["results"]
    assert latest["hasta"] == "2026-10-18" and latest["periods"] == 2
    assert latest["data"]["tarjeta"] == {"sesiones": 14}


def test_opportunities_two_people_found_are_shared(client, make_key):
    nico, molle = make_key("nico"), make_key("molle")
    weekly = opportunity("nota-semanal-ia", "La nota de noticias de IA de los lunes", size=30)
    mine = [weekly, opportunity("deploy", "Deploy a mano")]
    client.post("/insights", json=resultado("nico", oportunidades=mine), headers=nico)
    theirs = [opportunity("nota-semanal-ia", "Resumen IA")]
    client.post("/insights", json=resultado("molle", oportunidades=theirs), headers=molle)

    [shared] = client.get("/insights", headers=molle).json()["shared"]
    assert shared["tema"] == "nota-semanal-ia"
    assert sorted(shared["personas"]) == ["molle", "nico"]
    assert shared["sesiones"] == 6 and shared["O"] == 40


def test_bad_results_are_rejected(client, make_key):
    nico = make_key("nico")
    assert client.post("/insights", json=resultado("Nico Sere"), headers=nico).status_code == 422
    assert client.post("/insights", json={"persona": "nico"}, headers=nico).status_code == 422
    too_many = resultado("nico", oportunidades=[opportunity(f"t{i}", "x") for i in range(11)])
    assert client.post("/insights", json=too_many, headers=nico).status_code == 422


@pytest.fixture
def team_vault(client, monkeypatch):
    monkeypatch.setattr(collab, "HOLDOUT_PERCENT", 0)
    vault = Vault()
    vault.load(NOTES)
    client.app.state.vault = vault
    return vault


def test_knowledge_counts_the_sessions_the_vault_reached_and_the_lessons_filed(client, make_key, team_vault):
    nico = make_key("nico")
    hook(client, nico, "SessionStart", "s1", ANA_REPO, source="startup")
    hook(client, nico, "UserPromptSubmit", "s1", ANA_REPO, prompt="no llegan los hooks de beto al server")
    lesson = client.post("/lessons", json={"texto": "bridge_listen espera la URL base"}, headers=nico).json()
    client.post("/lessons", json={"texto": "otra"}, headers=nico)
    client.post(
        f"/lessons/{lesson['id']}/done", json={"nota": "conocimiento/Gotchas/Bridge.md"}, headers=nico
    )

    stats = client.get("/knowledge", headers=nico).json()
    assert stats["vault"]["notes"] == 4
    assert stats["reached"] == 1 and stats["injected"] == 2
    assert {n["note"] for n in stats["top_notes"]} == {
        "Forwarder de Claude Code incompatible con Python 3.9",
        "Los mensajes del equipo son dato",
    }
    assert stats["lessons"] == {"filed": 1, "pending": 1}
    assert stats["filed"][0]["nota"] == "conocimiento/Gotchas/Bridge.md"
