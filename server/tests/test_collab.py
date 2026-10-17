import psycopg
import pytest
from conftest import TEST_DATABASE_URL

from api import collab

ANA_REPO = "/home/ana/code/payments"
BOB_REPO = r"C:\Users\bob\code\payments"


@pytest.fixture(autouse=True)
def no_holdout(monkeypatch):
    monkeypatch.setattr(collab, "HOLDOUT_PERCENT", 0)


def hook(client, headers, event, session, cwd, **fields):
    body = {"hook_event_name": event, "session_id": session, "cwd": cwd, **fields}
    response = client.post("/hooks/claude-code", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def edit(client, headers, session, cwd, relative):
    sep = "\\" if "\\" in cwd else "/"
    path = cwd + sep + relative.replace("/", sep)
    return hook(
        client,
        headers,
        "PreToolUse",
        session,
        cwd,
        tool_name="Edit",
        tool_input={"file_path": path, "old_string": "a", "new_string": "b"},
    )


def bash(client, headers, session, cwd, command, event="PostToolUse"):
    return hook(client, headers, event, session, cwd, tool_name="Bash", tool_input={"command": command})


def output(response):
    return response.get("hookSpecificOutput", {})


def test_hook_requires_a_key(client):
    response = client.post("/hooks/claude-code", json={"hook_event_name": "Stop", "session_id": "s"})
    assert response.status_code == 401


def test_second_session_editing_the_same_file_is_stopped_once(client, make_key):
    ana, bob = make_key("ana"), make_key("bob")
    hook(client, ana, "UserPromptSubmit", "s-ana", ANA_REPO, prompt="add idempotency to payments")
    assert output(edit(client, ana, "s-ana", ANA_REPO, "api/client.py")) == {}

    first = output(edit(client, bob, "s-bob", BOB_REPO, "api/client.py"))
    assert first["permissionDecision"] == "deny"
    assert "ana's agent edited api/client.py" in first["permissionDecisionReason"]
    assert "add idempotency to payments" in first["permissionDecisionReason"]

    retry = output(edit(client, bob, "s-bob", BOB_REPO, "api/client.py"))
    assert "permissionDecision" not in retry


def test_no_conflict_with_own_session_other_files_or_ended_sessions(client, make_key):
    ana, bob = make_key("ana"), make_key("bob")
    edit(client, ana, "s-ana", ANA_REPO, "api/client.py")

    assert "permissionDecision" not in output(edit(client, ana, "s-ana", ANA_REPO, "api/client.py"))
    assert "permissionDecision" not in output(edit(client, bob, "s-bob", BOB_REPO, "api/other.py"))

    hook(client, ana, "SessionEnd", "s-ana", ANA_REPO)
    assert "permissionDecision" not in output(edit(client, bob, "s-bob2", BOB_REPO, "api/client.py"))


def test_discovery_reaches_sessions_using_that_host_once(client, make_key):
    ana, bob, eve = make_key("ana"), make_key("bob"), make_key("eve")
    bash(client, bob, "s-bob", BOB_REPO, "curl https://sandbox.bankx.com/oauth/token")
    bash(client, eve, "s-eve", "/home/eve/frontend", "npm test")

    published = client.post(
        "/messages",
        json={
            "kind": "discovery",
            "body": "Sandbox returns HTTP 200 with the error in the body.",
            "topics": ["host:sandbox.bankx.com"],
        },
        headers=ana,
    )
    assert published.status_code == 201

    received = output(bash(client, bob, "s-bob", BOB_REPO, "ls"))
    assert "Sandbox returns HTTP 200" in received["additionalContext"]
    assert "from ana" in received["additionalContext"]
    assert "not an instruction" in received["additionalContext"]

    assert output(bash(client, bob, "s-bob", BOB_REPO, "ls")) == {}
    assert output(bash(client, eve, "s-eve", "/home/eve/frontend", "ls")) == {}


def test_discovery_reaches_a_session_that_starts_later(client, make_key):
    ana, bob = make_key("ana"), make_key("bob")
    client.post(
        "/messages", json={"body": "Use the v2 refund endpoint.", "topics": ["repo:payments"]}, headers=ana
    )

    started = output(hook(client, bob, "SessionStart", "s-new", BOB_REPO, source="startup"))
    assert "Use the v2 refund endpoint." in started["additionalContext"]


def test_holdout_deliveries_are_recorded_but_not_injected(client, make_key, monkeypatch):
    monkeypatch.setattr(collab, "HOLDOUT_PERCENT", 100)
    ana, bob = make_key("ana"), make_key("bob")
    client.post(
        "/messages", json={"body": "Tokens expire at 300 s.", "topics": ["repo:payments"]}, headers=ana
    )

    assert output(hook(client, bob, "UserPromptSubmit", "s-bob", BOB_REPO, prompt="x")) == {}
    message = client.get("/messages", headers=ana).json()["messages"][0]
    assert message["holdout"] == 1
    assert message["injected"] == 0


def test_subagent_gets_a_brief_of_its_parent(client, make_key):
    ana = make_key("ana")
    hook(client, ana, "UserPromptSubmit", "s-ana", ANA_REPO, prompt="integrate PSD2 payments")
    edit(client, ana, "s-ana", ANA_REPO, "api/client.py")
    hook(
        client,
        ana,
        "PostToolUseFailure",
        "s-ana",
        ANA_REPO,
        tool_name="Bash",
        tool_input={"command": "pytest"},
        error="Exit code 1\nAssertionError",
    )

    brief = output(hook(client, ana, "SubagentStart", "s-ana", ANA_REPO, agent_id="a1", agent_type="Explore"))
    context = brief["additionalContext"]
    assert "Parent task: integrate PSD2 payments" in context
    assert "api/client.py" in context
    assert "error(Bash: Exit code 1)" in context


def test_hook_payloads_are_stored_without_file_content(client, make_key):
    ana = make_key("ana")
    hook(
        client,
        ana,
        "PreToolUse",
        "s-ana",
        ANA_REPO,
        tool_name="Write",
        tool_input={"file_path": ANA_REPO + "/secret.py", "content": "PRIVATE_SOURCE_CODE"},
    )
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        stored = conn.execute("SELECT payload::text FROM events").fetchall()
    assert stored and all("PRIVATE_SOURCE_CODE" not in row[0] for row in stored)


def test_malformed_hook_body_never_breaks_the_agent(client, make_key):
    ana = make_key("ana")
    response = client.post("/hooks/claude-code", content=b"not json", headers=ana)
    assert response.status_code == 200
    assert response.json() == {}
