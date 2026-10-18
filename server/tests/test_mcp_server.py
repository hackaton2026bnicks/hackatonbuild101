import importlib.util
import sys
from pathlib import Path

import pytest
from test_collab import ANA_REPO, BOB_REPO, bash, hook, output

from api import collab

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent" / "claude-code"
sys.path.insert(0, str(AGENT_DIR))
spec = importlib.util.spec_from_file_location("mcp_server", AGENT_DIR / "mcp_server.py")
mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp)


@pytest.fixture(autouse=True)
def no_holdout(monkeypatch):
    monkeypatch.setattr(collab, "HOLDOUT_PERCENT", 0)


@pytest.fixture
def via_client(client, monkeypatch):
    """Route the MCP server's HTTP calls to the test API instead of the network."""

    def request(method, path, environ, body=None):
        headers = {"Authorization": f"Bearer {environ['BUILD101_KEY']}"}
        response = client.request(method, path, json=body, headers=headers)
        if response.status_code >= 400:
            raise mcp.ToolError(f"Team server answered {response.status_code}: {response.text}")
        return response.json()

    monkeypatch.setattr(mcp, "request", request)


def env(headers, project):
    return {"BUILD101_KEY": headers["Authorization"].split()[1], "CLAUDE_PROJECT_DIR": project}


def call(name, environ, **arguments):
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    result = mcp.handle(message, environ)["result"]
    return result["content"][0]["text"], result["isError"]


def test_handshake_lists_the_tools_with_usage_instructions():
    init = mcp.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}, {})["result"]
    assert "share_discovery" in init["instructions"]
    assert init["capabilities"] == {"tools": {}}
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, {}) is None
    tools = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, {})["result"]["tools"]
    assert {t["name"] for t in tools} == {"share_discovery", "ask_team", "answer_team", "team_status"}


def test_without_a_key_tools_fail_softly():
    text, is_error = call("team_status", {"CLAUDE_PROJECT_DIR": ANA_REPO})
    assert is_error and "BUILD101_KEY" in text


def test_file_topics_are_repo_relative_like_the_hooks():
    assert mcp.topics_for(
        {"files": [ANA_REPO + "/api/client.py", "api/b.py"], "hosts": ["Sandbox.X.com"]},
        {"CLAUDE_PROJECT_DIR": ANA_REPO},
    ) == ["file:api/client.py", "file:api/b.py", "host:sandbox.x.com"]


def test_two_agents_share_ask_and_answer_through_the_tools(client, make_key, via_client):
    ana, bob = make_key("ana"), make_key("bob")
    hook(client, ana, "UserPromptSubmit", "s-ana", ANA_REPO, prompt="refunds")
    bash(client, bob, "s-bob", BOB_REPO, "curl https://sandbox.bankx.com/refunds")
    ana_env, bob_env = env(ana, ANA_REPO), env(bob, BOB_REPO)

    text, is_error = call(
        "share_discovery",
        ana_env,
        body="sandbox.bankx.com returns 200 with the error in the body; check body.status.",
        hosts=["sandbox.bankx.com"],
    )
    assert not is_error and "host:sandbox.bankx.com" in text
    assert "check body.status" in output(bash(client, bob, "s-bob", BOB_REPO, "ls"))["additionalContext"]

    text, _ = call("ask_team", bob_env, question="Which sandbox user has 2FA disabled?")
    question_id = int(text.split("#")[1].split(".")[0])
    asked = output(hook(client, ana, "UserPromptSubmit", "s-ana", ANA_REPO, prompt="go on"))
    assert f"question_id={question_id}" in asked["additionalContext"]

    status, _ = call("team_status", ana_env)
    assert "- you: go on" in status and f"#{question_id} from bob" in status

    text, is_error = call(
        "answer_team", ana_env, question_id=question_id, answer="Use test-02, password in 1Password."
    )
    assert not is_error
    answered = output(bash(client, bob, "s-bob", BOB_REPO, "ls"))["additionalContext"]
    assert "answer from ana" in answered and "test-02" in answered


def test_mcp_reads_utf8_messages_even_when_the_console_is_cp1252(monkeypatch):
    import io
    import json

    line = json.dumps({"jsonrpc": "2.0", "id": 1, "text": "análisis"}, ensure_ascii=False) + "\n"
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(line.encode("utf-8")), encoding="cp1252"))
    seen = []
    monkeypatch.setattr(mcp, "handle", lambda message, environ: seen.append(message))
    mcp.main()
    assert seen[0]["text"] == "análisis"
