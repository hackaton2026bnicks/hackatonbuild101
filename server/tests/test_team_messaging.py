"""Agents talking to each other: discoveries and questions published from a session, answers routed back."""

import pytest
from test_collab import ANA_REPO, BOB_REPO, bash, edit, hook, output

from api import collab


@pytest.fixture(autouse=True)
def no_holdout(monkeypatch):
    monkeypatch.setattr(collab, "HOLDOUT_PERCENT", 0)


def publish(client, headers, **body):
    response = client.post("/messages", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_agent_publishes_from_its_live_session_without_knowing_its_id(client, make_key):
    ana, bob = make_key("ana"), make_key("bob")
    hook(client, ana, "UserPromptSubmit", "s-ana", ANA_REPO, prompt="refunds")
    hook(client, bob, "UserPromptSubmit", "s-bob", BOB_REPO, prompt="payouts")

    message = publish(client, ana, body="The refund endpoint needs an Idempotency-Key.", repo="payments")
    assert message["from_session"] == "s-ana"
    assert message["topics"] == ["repo:payments"]

    assert "Idempotency-Key" in output(bash(client, bob, "s-bob", BOB_REPO, "ls"))["additionalContext"]
    # Never echoed back to the session that published it
    assert output(bash(client, ana, "s-ana", ANA_REPO, "ls")) == {}


def test_question_reaches_the_team_and_the_answer_reaches_only_the_asker(client, make_key):
    ana, bob, eve = make_key("ana"), make_key("bob"), make_key("eve")
    hook(client, ana, "UserPromptSubmit", "s-ana", ANA_REPO, prompt="refunds")
    hook(client, bob, "UserPromptSubmit", "s-bob", BOB_REPO, prompt="payouts")
    hook(client, eve, "UserPromptSubmit", "s-eve", BOB_REPO, prompt="docs")

    question = publish(
        client, ana, kind="question", body="Which sandbox user has 2FA disabled?", repo="payments"
    )
    asked = output(bash(client, bob, "s-bob", BOB_REPO, "ls"))["additionalContext"]
    assert f"question #{question['id']} from ana" in asked
    assert f"question_id={question['id']}" in asked
    output(bash(client, eve, "s-eve", BOB_REPO, "ls"))

    answer = publish(
        client, bob, kind="answer", reply_to=question["id"], body="Use sandbox user test-02.", repo="payments"
    )
    assert answer["to_session"] == "s-ana"

    received = output(bash(client, ana, "s-ana", ANA_REPO, "ls"))["additionalContext"]
    assert f"answer from bob to your question #{question['id']}" in received
    assert "test-02" in received
    assert output(bash(client, eve, "s-eve", BOB_REPO, "ls")) == {}


def test_answer_needs_an_existing_question(client, make_key):
    ana = make_key("ana")
    missing = client.post("/messages", json={"kind": "answer", "reply_to": 999, "body": "x"}, headers=ana)
    assert missing.status_code == 404
    unlinked = client.post("/messages", json={"kind": "answer", "body": "x"}, headers=ana)
    assert unlinked.status_code == 422


def test_team_shows_who_works_where_and_open_questions(client, make_key):
    ana, bob = make_key("ana"), make_key("bob")
    hook(client, ana, "UserPromptSubmit", "s-ana", ANA_REPO, prompt="add idempotency to refunds")
    edit(client, ana, "s-ana", ANA_REPO, "api/refunds.py")
    hook(client, bob, "UserPromptSubmit", "s-bob", BOB_REPO, prompt="payouts")
    question = publish(client, bob, kind="question", body="Is the sandbox down?", repo="payments")

    team = client.get("/team", params={"repo": "payments"}, headers=ana).json()
    by_user = {row["user_name"]: row for row in team["sessions"]}
    assert by_user["ana"]["you"] and not by_user["bob"]["you"]
    assert by_user["ana"]["editing"] == ["api/refunds.py"]
    assert [q["id"] for q in team["open_questions"]] == [question["id"]]

    publish(client, ana, kind="answer", reply_to=question["id"], body="No, it rate limits.", repo="payments")
    assert client.get("/team", params={"repo": "payments"}, headers=ana).json()["open_questions"] == []


def test_answers_are_listed_for_an_asker_whose_hooks_do_not_reach(client, make_key):
    ana, bob = make_key("ana"), make_key("bob")
    hook(client, bob, "UserPromptSubmit", "s-bob", BOB_REPO, prompt="payouts")
    question = publish(client, ana, kind="question", body="Which sandbox user?", repo="payments")
    assert question["from_session"] is None
    publish(client, bob, kind="answer", reply_to=question["id"], body="test-02", repo="payments")

    answers = client.get("/team", params={"repo": "payments"}, headers=ana).json()["answers_for_you"]
    assert [(a["reply_to"], a["body"], a["from_user"]) for a in answers] == [
        (question["id"], "test-02", "bob")
    ]
    assert client.get("/team", params={"repo": "payments"}, headers=bob).json()["answers_for_you"] == []


def test_new_session_learns_who_else_is_working_in_the_repo(client, make_key):
    ana, bob = make_key("ana"), make_key("bob")
    hook(client, ana, "UserPromptSubmit", "s-ana", ANA_REPO, prompt="add idempotency to refunds")
    edit(client, ana, "s-ana", ANA_REPO, "api/refunds.py")

    started = output(hook(client, bob, "SessionStart", "s-bob", BOB_REPO, source="startup"))
    context = started["additionalContext"]
    assert "who else is working in payments" in context
    assert 'ana, active just now, working on "add idempotency to refunds"; editing api/refunds.py' in context

    alone = output(hook(client, ana, "SessionStart", "s-solo", "/home/ana/code/frontend", source="startup"))
    assert alone == {}
