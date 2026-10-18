import importlib.util
from pathlib import Path

from api.scrub import scrub

FORWARD = Path(__file__).resolve().parents[2] / "agent" / "claude-code" / "forward.py"
spec = importlib.util.spec_from_file_location("forward", FORWARD)
forward = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forward)


def test_file_contents_and_tool_output_never_leave_the_machine():
    raw = {
        "hook_event_name": "PostToolUse",
        "session_id": "s1",
        "cwd": "/home/ana/payments",
        "tool_name": "Write",
        "tool_input": {"file_path": "/home/ana/payments/api.py", "content": "PRIVATE_SOURCE"},
        "tool_response": {"stdout": "PRIVATE_OUTPUT"},
        "transcript_path": "/home/ana/.claude/transcript.jsonl",
    }
    sent = forward.sanitize(raw, {})
    assert "PRIVATE" not in str(sent)
    assert "transcript_path" not in sent
    assert sent["tool_input"] == {"file_path": "/home/ana/payments/api.py"}


def test_edit_strings_are_dropped():
    raw = {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "tool_name": "Edit",
        "tool_input": {"file_path": "/r/a.py", "old_string": "OLD_CODE", "new_string": "NEW_CODE"},
    }
    sent = forward.sanitize(raw, {})
    assert "OLD_CODE" not in str(sent) and "NEW_CODE" not in str(sent)


def test_environment_secrets_are_blanked_even_without_a_known_format():
    environ = {"STRIPE_SECRET": "plainword123", "HOME": "/home/ana", "BUILD101_KEY": "b101_abc"}
    raw = {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "tool_name": "Bash",
        "tool_input": {"command": "curl -u admin:plainword123 https://api.stripe.com"},
    }
    sent = forward.sanitize(raw, environ)
    assert "plainword123" not in sent["tool_input"]["command"]
    assert "api.stripe.com" in sent["tool_input"]["command"]


def test_prompt_and_error_are_scrubbed():
    raw = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "s1",
        "prompt": "use token=supersecretvalue to call the API",
    }
    assert "supersecretvalue" not in forward.sanitize(raw, {})["prompt"]


def test_random_looking_tokens_are_redacted_but_hashes_are_kept():
    secret = "Zx8Kq2LmN4pR7tVw9yB3cD6fG1hJ5kM0"
    sha = "4e1243bd22c66e76c2ba9eddc1f91394e57f9f83"
    cleaned = scrub(f"export X={secret} && git checkout {sha}")
    assert secret not in cleaned
    assert sha in cleaned


def test_paths_are_relative_to_the_project_even_after_cd():
    raw = {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "cwd": "/home/ana/payments/server",
        "tool_name": "Edit",
        "tool_input": {"file_path": "/home/ana/payments/server/api/db.py"},
    }
    sent = forward.sanitize(raw, {"CLAUDE_PROJECT_DIR": "/home/ana/payments"})
    assert sent["cwd"] == "/home/ana/payments"
    assert forward.sanitize(raw, {})["cwd"] == "/home/ana/payments/server"


def test_skill_and_subagent_names_are_sent_but_not_their_args_or_prompt():
    skill = {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "tool_name": "Skill",
        "tool_input": {"skill": "capitalizar", "args": "PRIVATE_ARGS"},
    }
    agent = {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "Explore", "description": "PRIVATE_DESC", "prompt": "PRIVATE_PROMPT"},
    }
    assert forward.sanitize(skill, {})["tool_input"] == {"skill": "capitalizar"}
    assert forward.sanitize(agent, {})["tool_input"] == {"subagent_type": "Explore"}
    assert "PRIVATE" not in str(forward.sanitize(skill, {})) + str(forward.sanitize(agent, {}))


def test_compaction_is_sent_with_its_trigger_but_not_the_instructions():
    raw = {
        "hook_event_name": "PreCompact",
        "session_id": "s1",
        "cwd": "/home/ana/payments",
        "trigger": "auto",
        "custom_instructions": "PRIVATE_FOCUS",
    }
    sent = forward.sanitize(raw, {})
    assert sent == {
        "hook_event_name": "PreCompact",
        "session_id": "s1",
        "cwd": "/home/ana/payments",
        "workspace": "/home/ana/payments",
        "trigger": "auto",
    }


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def fake_urlopen(*outcomes):
    calls = []

    def urlopen(request, timeout):
        outcome = outcomes[len(calls)]
        calls.append(timeout)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    return urlopen, calls


def restarting(code=502):
    import urllib.error

    return urllib.error.HTTPError("https://x", code, "Bad Gateway", {}, None)


def test_a_restarting_server_gets_one_retry():
    urlopen, calls = fake_urlopen(restarting(), b'{"ok": 1}')
    assert forward.post("req", urlopen=urlopen, sleep=lambda s: None) == {"ok": 1}
    assert len(calls) == 2

    import urllib.error

    urlopen, calls = fake_urlopen(urllib.error.URLError(ConnectionRefusedError()), b"{}")
    assert forward.post("req", urlopen=urlopen, sleep=lambda s: None) == {}
    assert len(calls) == 2


def test_timeouts_and_other_errors_are_not_retried():
    import urllib.error

    for failure in (TimeoutError(), urllib.error.URLError(TimeoutError()), restarting(401), restarting(500)):
        urlopen, calls = fake_urlopen(failure, b"{}")
        assert forward.post("req", urlopen=urlopen, sleep=lambda s: None) is None
        assert len(calls) == 1

    urlopen, calls = fake_urlopen(restarting(), restarting())
    assert forward.post("req", urlopen=urlopen, sleep=lambda s: None) is None
    assert len(calls) == 2
    assert urllib.error  # imported for the failure types above


def test_hook_payload_is_read_as_utf8_even_when_the_console_is_cp1252():
    import io
    import json

    payload = json.dumps({"prompt": "corran el análisis, año"}, ensure_ascii=False).encode("utf-8")
    stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="cp1252")
    assert forward.read_stdin_json(stdin) == {"prompt": "corran el análisis, año"}


def test_the_workspace_is_the_worktree_the_shell_is_in_or_else_the_project():
    project = r"C:\Users\ana\payments"
    assert forward.workspace(project + r"\server\api", project) == "C:/Users/ana/payments"
    assert forward.workspace(project + r"\.claude\worktrees\login\server", project) == (
        "C:/Users/ana/payments/.claude/worktrees/login"
    )
    out = forward.sanitize(
        {"hook_event_name": "Stop", "cwd": "/home/ana/payments/ui"},
        {"CLAUDE_PROJECT_DIR": "/home/ana/payments"},
    )
    assert out["cwd"] == out["workspace"] == "/home/ana/payments"
