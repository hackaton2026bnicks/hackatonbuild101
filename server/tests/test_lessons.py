"""The queue between sessions and the archivista (agent/hermes/bandeja.py), which files them in the vault."""


def lesson(**fields):
    return {"texto": "El forwarder falla en Python 3.9 por str | None", "repo": "hackatonbuild101", **fields}


def test_a_lesson_waits_for_the_archivista_with_the_key_owner_as_author(client, make_key):
    nico, molle = make_key("nico"), make_key("molle")
    created = client.post(
        "/lessons", json=lesson(tipo="gotcha", archivos=["agent/claude-code/forward.py"]), headers=nico
    )
    assert created.status_code == 201, created.text
    assert created.json()["autor"] == "nico"

    pending = client.get("/lessons", params={"status": "pending", "limit": 20}, headers=molle).json()
    [queued] = pending["lessons"]
    assert queued["texto"].startswith("El forwarder") and queued["tipo"] == "gotcha"
    assert (
        queued["archivos"] == ["agent/claude-code/forward.py"] and queued["fuente"] == "sesión de Claude Code"
    )

    nota = "conocimiento/Gotchas/Forwarder de Claude Code incompatible con Python 3.9.md"
    done = client.post(f"/lessons/{queued['id']}/done", json={"nota": nota}, headers=molle).json()
    assert done["status"] == "filed" and done["nota"] == nota and done["done_by"] == "molle"
    assert client.get("/lessons", headers=molle).json()["lessons"] == []


def test_discarded_lessons_keep_the_reason_and_bad_verdicts_are_rejected(client, make_key):
    nico = make_key("nico")
    lesson_id = client.post("/lessons", json=lesson(), headers=nico).json()["id"]
    assert client.post(f"/lessons/{lesson_id}/done", json={}, headers=nico).status_code == 422
    both = {"nota": "conocimiento/x.md", "descartada": "ya estaba"}
    assert client.post(f"/lessons/{lesson_id}/done", json=both, headers=nico).status_code == 422
    assert client.post("/lessons/999/done", json={"descartada": "x"}, headers=nico).status_code == 404

    client.post(f"/lessons/{lesson_id}/done", json={"descartada": "ya estaba en la bóveda"}, headers=nico)
    [discarded] = client.get("/lessons", params={"status": "discarded"}, headers=nico).json()["lessons"]
    assert discarded["motivo"] == "ya estaba en la bóveda"


def test_lessons_are_scrubbed_and_stay_in_their_team(client, make_key):
    nico, other = make_key("nico"), make_key("eve", team="otro")
    client.post(
        "/lessons", json=lesson(texto="usá Bearer sk-live1234567890abcdef1234 para el sandbox"), headers=nico
    )
    [queued] = client.get("/lessons", headers=nico).json()["lessons"]
    assert "sk-live1234567890abcdef1234" not in queued["texto"]
    assert client.get("/lessons", headers=other).json()["lessons"] == []


def test_a_shared_discovery_also_goes_to_the_vault(client, make_key):
    nico = make_key("nico")
    client.post(
        "/messages",
        json={"kind": "discovery", "body": "bridge_listen espera la URL base, no la del hook",
              "topics": ["file:tools/bridge_listen.py"], "repo": "hackatonbuild101"},
        headers=nico,
    )  # fmt: skip
    client.post("/messages", json={"kind": "note", "body": "salgo a almorzar"}, headers=nico)
    [queued] = client.get("/lessons", headers=nico).json()["lessons"]
    assert queued["fuente"] == "share_discovery" and queued["autor"] == "nico"
    assert queued["archivos"] == ["tools/bridge_listen.py"]
