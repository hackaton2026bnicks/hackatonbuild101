"""Team knowledge base: lessons kept, found, and offered to a session when its situation matches."""

import pytest
import test_mcp_server
from test_collab import ANA_REPO, BOB_REPO, bash, edit, hook, output
from test_mcp_server import call, env

from api import collab, kb

via_client = test_mcp_server.via_client


@pytest.fixture(autouse=True)
def no_holdout(monkeypatch):
    monkeypatch.setattr(collab, "HOLDOUT_PERCENT", 0)
    monkeypatch.setattr(kb, "HOLDOUT_PERCENT", 0)


def remember(client, headers, **unit):
    response = client.post("/kb", json={"repo": "payments", **unit}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


TOKEN_GOTCHA = {
    "type": "gotcha",
    "title": "Sandbox token endpoint wants form data, not JSON",
    "body": "POST /oauth/token on sandbox.bankx.com answers 415 Unsupported Media Type to JSON. Send it as "
    "application/x-www-form-urlencoded.",
    "dead_ends": ["Adding Accept: application/json changes nothing"],
    "hosts": ["sandbox.bankx.com"],
    "files": ["api/client.py"],
    "keywords": ["415", "oauth"],
}


def test_a_kept_lesson_reaches_whoever_touches_its_file_once(client, make_key):
    ana, bob = make_key("ana"), make_key("bob")
    remember(client, ana, **TOKEN_GOTCHA)
    hook(client, bob, "UserPromptSubmit", "s-bob", BOB_REPO, prompt="retry failed payouts")

    context = output(edit(client, bob, "s-bob", BOB_REPO, "api/client.py"))["additionalContext"]
    assert "team knowledge, about api/client.py" in context
    assert "form data, not JSON" in context and "Dead ends: Adding Accept" in context
    assert "form data" not in str(output(edit(client, bob, "s-bob", BOB_REPO, "api/client.py")))


def test_an_error_that_matches_a_gotcha_gets_it(client, make_key):
    ana, bob = make_key("ana"), make_key("bob")
    remember(client, ana, **{**TOKEN_GOTCHA, "files": [], "hosts": []})
    hook(client, bob, "UserPromptSubmit", "s-bob", BOB_REPO, prompt="connect to the bank sandbox")
    failed = hook(
        client, bob, "PostToolUseFailure", "s-bob", BOB_REPO, tool_name="Bash",
        tool_input={"command": "python pay.py"}, error="HTTPError: 415 Unsupported Media Type (oauth token)",
    )  # fmt: skip
    assert "matches the error you just hit" in output(failed)["additionalContext"]

    unrelated = hook(
        client, bob, "PostToolUseFailure", "s-bob2", BOB_REPO, tool_name="Bash",
        tool_input={"command": "pytest"}, error="AssertionError: expected 3 payouts",
    )  # fmt: skip
    assert "team knowledge" not in str(output(unrelated))


def test_a_new_session_gets_the_repo_best_and_the_author_never_gets_their_own(client, make_key):
    ana, bob = make_key("ana"), make_key("bob")
    hook(client, ana, "UserPromptSubmit", "s-ana", ANA_REPO, prompt="integrate the bank sandbox")
    remember(client, ana, **TOKEN_GOTCHA)

    started = output(hook(client, bob, "SessionStart", "s-bob", BOB_REPO))["additionalContext"]
    assert "team knowledge, best for payments" in started
    assert "team knowledge" not in str(output(edit(client, ana, "s-ana", ANA_REPO, "api/client.py")))


def test_a_discovery_is_kept_and_not_delivered_twice(client, make_key):
    ana, bob, cai = make_key("ana"), make_key("bob"), make_key("cai")
    hook(client, ana, "UserPromptSubmit", "s-ana", ANA_REPO, prompt="integrate the bank sandbox")
    hook(client, bob, "UserPromptSubmit", "s-bob", BOB_REPO, prompt="retry failed payouts")
    client.post(
        "/messages",
        json={"body": "The ledger API caps pages at 100. Ask for more and it silently returns 100.",
              "topics": ["file:api/ledger.py"], "repo": "payments"},
        headers=ana,
    )  # fmt: skip
    # Bob gets the hot discovery as a message, and not a second time as its knowledge unit
    first = output(edit(client, bob, "s-bob", BOB_REPO, "api/ledger.py"))["additionalContext"]
    assert first.count("caps pages at 100") == 1
    assert "caps pages" not in str(output(edit(client, bob, "s-bob", BOB_REPO, "api/ledger.py")))
    assert "caps pages" not in str(output(bash(client, bob, "s-bob", BOB_REPO, "ls")))

    # Long after the message expires, someone else still finds it
    units = client.get("/kb", params={"q": "ledger pages limit"}, headers=cai).json()["units"]
    assert [u["title"] for u in units] == ["The ledger API caps pages at 100"]


def test_candidates_wait_for_their_author_and_a_second_session_confirms_them(client, make_key):
    ana, bob = make_key("ana"), make_key("bob")
    inferred = {**TOKEN_GOTCHA, "confidence": "observado", "session_id": "s-ana"}
    unit = remember(client, ana, **inferred)["unit"]
    assert unit["status"] == "candidata"
    assert client.get("/kb", params={"q": "oauth token"}, headers=bob).json()["units"] == []

    started = hook(client, ana, "SessionStart", "s-ana2", ANA_REPO)
    assert "1 lesson(s) from your sessions" in started["systemMessage"]
    assert f"ku#{unit['id']}" in output(started)["additionalContext"]

    again = remember(client, bob, **{**inferred, "session_id": "s-bob"})
    assert not again["created"]
    assert again["unit"]["status"] == "activa" and again["unit"]["confirmations"] == 2


def test_only_the_author_approves_and_two_people_saying_wrong_archive(client, make_key):
    ana, bob, cai = make_key("ana"), make_key("bob"), make_key("cai")
    unit = remember(client, ana, **{**TOKEN_GOTCHA, "confidence": "observado"})["unit"]
    feedback = f"/kb/{unit['id']}/feedback"
    assert (
        client.post(feedback, json={"verdict": "approve"}, headers=bob).status_code == 404
    )  # not visible yet
    assert client.post(feedback, json={"verdict": "approve"}, headers=ana).json()["status"] == "activa"
    assert client.post(feedback, json={"verdict": "approve"}, headers=bob).status_code == 403

    assert client.post(feedback, json={"verdict": "wrong"}, headers=bob).json()["status"] == "activa"
    assert client.post(feedback, json={"verdict": "wrong"}, headers=cai).json()["status"] == "archivada"


def test_agents_keep_search_and_rate_lessons_through_the_tools(client, make_key, via_client):
    ana, bob = make_key("ana"), make_key("bob")
    ana_env, bob_env = env(ana, ANA_REPO), env(bob, BOB_REPO)

    kept, failed = call(
        "kb_remember", ana_env, type="receta", title="Run the payments suite against sandbox",
        body="Export BANKX_ENV=sandbox and run make test-sandbox; plain pytest hits production mocks.",
        files=[ANA_REPO + "/Makefile"],
    )  # fmt: skip
    assert not failed and kept.startswith("Kept as ku#")
    ku_id = int(kept.split("ku#")[1].split(";")[0])

    found, _ = call("kb_search", bob_env, query="how to run the sandbox tests")
    assert f"ku#{ku_id} (receta, from ana)" in found
    assert client.get(f"/kb/{ku_id}", headers=bob).json()["files"] == ["Makefile"]

    rated, _ = call("kb_feedback", bob_env, ku_id=ku_id, verdict="useful")
    assert rated == f"Recorded useful for ku#{ku_id}; it is now activa."
    nothing, _ = call("kb_search", bob_env, query="kubernetes ingress")
    assert nothing.startswith("The team has nothing on that yet")
