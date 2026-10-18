import psycopg
import pytest
from conftest import TEST_DATABASE_URL

from api.admin import run, unmangle


def test_created_key_works_and_can_be_revoked(client):
    output = run(["create-key", "ana", "build101"], TEST_DATABASE_URL)
    key = next(line[4:] for line in output.splitlines() if line.startswith("key="))
    headers = {"Authorization": f"Bearer {key}"}

    assert client.get("/me", headers=headers).json() == {"user": "ana", "team": "build101"}
    assert "ana\tbuild101" in run(["list-keys"], TEST_DATABASE_URL)

    key_id = output.split()[0].removeprefix("id=")
    assert run(["revoke-key", key_id], TEST_DATABASE_URL) == f"Revoked key {key_id}."
    assert client.get("/me", headers=headers).status_code == 401


def test_invalid_names_and_commands_are_rejected(client):
    with pytest.raises(SystemExit):
        run(["create-key", "ana; DROP TABLE api_keys"], TEST_DATABASE_URL)
    with pytest.raises(SystemExit):
        run(["delete-everything"], TEST_DATABASE_URL)


def test_text_garbled_by_a_cp1252_stdin_is_repaired_only_on_apply(client, make_key):
    ana = make_key("ana")
    garbled = "anÃ¡lisis de sesiÃ³n Â¿listo? itâ€™s"
    client.post("/messages", json={"kind": "note", "body": garbled}, headers=ana)
    client.post("/messages", json={"kind": "note", "body": "Ya está bien: ñandú"}, headers=ana)

    dry = run(["repair-text"], TEST_DATABASE_URL)
    assert "messages.body: 1" in dry and "Would repair" in dry
    assert run(["repair-text", "--apply"], TEST_DATABASE_URL).endswith("Repaired: 1 values.")

    bodies = {m["body"] for m in client.get("/messages", headers=ana).json()["messages"]}
    assert bodies == {"análisis de sesión ¿listo? it’s", "Ya está bien: ñandú"}
    assert "Repaired: 0 values." in run(["repair-text", "--apply"], TEST_DATABASE_URL)


def test_correct_text_is_never_touched():
    for text in ("Árbol y café", "plain ascii", "Café con una Ã suelta", "3Â piso"):
        assert unmangle(text) == text


def test_session_tasks_are_recomputed_from_the_first_real_request(client, make_key):
    from test_collab import ANA_REPO, hook

    ana = make_key("ana")
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        conn.execute(
            "INSERT INTO sessions (team, session_id, user_name, agent, started_at, last_event_at, "
            "last_event_type, last_summary, intent, event_count) VALUES ('default', 's-old', 'ana', "
            "'claude-code', now(), now(), 'intent', '', 'No por ahora', 1)"
        )
    for prompt in ("<system-reminder>worktree</system-reminder>", "migrate the ledger to postgres", "dale"):
        hook(client, ana, "UserPromptSubmit", "s-old", ANA_REPO, prompt=prompt)
        with psycopg.connect(TEST_DATABASE_URL) as conn:
            conn.execute("UPDATE sessions SET intent = 'No por ahora' WHERE session_id = 's-old'")

    assert "Would repair (run again with --apply): 1 of 1" in run(["repair-tasks"], TEST_DATABASE_URL)
    run(["repair-tasks", "--apply"], TEST_DATABASE_URL)
    session = client.get("/sessions", headers=ana).json()["sessions"][0]
    assert session["intent"] == "migrate the ledger to postgres"
