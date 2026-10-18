import time


def event(session_id: str, type_: str, **payload) -> dict:
    return {
        "agent": "claude-code",
        "session_id": session_id,
        "type": type_,
        "payload": payload,
        "ts": time.time(),
    }


def test_teammates_see_each_others_sessions(client, make_key):
    ana = make_key("ana")
    bob = make_key("bob")

    batch = [
        event("s-ana", "session_start", source="startup"),
        event("s-ana", "intent", prompt="integrate PSD2 payment initiation"),
        event("s-ana", "pre_action", tool="bash", canonical="bash(curl POST {bankx}/oauth/token)"),
    ]
    response = client.post("/events", json={"events": batch}, headers=ana)
    assert response.status_code == 202
    assert response.json() == {"accepted": 3}

    sessions = client.get("/sessions", headers=bob).json()["sessions"]
    assert len(sessions) == 1
    session = sessions[0]
    assert session["user"] == "ana"
    assert session["intent"] == "integrate PSD2 payment initiation"
    assert session["last_summary"] == "bash(curl POST {bankx}/oauth/token)"
    assert session["event_count"] == 3
    assert session["ended_at"] is None


def test_other_teams_see_nothing(client, make_key):
    ana = make_key("ana", team="team-a")
    eve = make_key("eve", team="team-b")
    client.post("/events", json={"events": [event("s-ana", "session_start")]}, headers=ana)

    assert client.get("/sessions", headers=eve).json()["sessions"] == []
    assert client.get("/sessions/s-ana/events", headers=eve).json()["events"] == []


def test_session_end_marks_the_session_ended(client, make_key):
    ana = make_key("ana")
    client.post(
        "/events",
        json={"events": [event("s-ana", "session_start"), event("s-ana", "session_end", reason="exit")]},
        headers=ana,
    )
    session = client.get("/sessions", headers=ana).json()["sessions"][0]
    assert session["ended_at"] is not None


def test_session_events_return_full_payload_newest_first(client, make_key):
    ana = make_key("ana")
    batch = [event("s-ana", "session_start"), event("s-ana", "post_action", tool="bash", exit_code=1)]
    client.post("/events", json={"events": batch}, headers=ana)
    events = client.get("/sessions/s-ana/events", headers=ana).json()["events"]
    assert [e["type"] for e in events] == ["post_action", "session_start"]
    assert events[0]["payload"] == {"tool": "bash", "exit_code": 1}


def test_oversized_payload_is_rejected(client, make_key):
    ana = make_key("ana")
    big = event("s-ana", "post_action", output="x" * 70_000)
    response = client.post("/events", json={"events": [big]}, headers=ana)
    assert response.status_code == 422


def test_unknown_event_type_is_rejected(client, make_key):
    ana = make_key("ana")
    response = client.post("/events", json={"events": [event("s-ana", "something_else")]}, headers=ana)
    assert response.status_code == 422


def test_replies_and_harness_text_do_not_replace_the_task(client, make_key):
    headers = make_key("ana")
    prompts = [
        "integrate PSD2 payment initiation",
        "<system-reminder>You are operating in a git worktree.</system-reminder>",
        "Sí, creá una rama y un PR",
        "dale",
    ]
    events = [event("s-ana", "intent", prompt=p) for p in prompts]
    client.post("/events", json={"events": events}, headers=headers)

    session = client.get("/sessions", headers=headers).json()["sessions"][0]
    assert session["intent"] == "integrate PSD2 payment initiation"
