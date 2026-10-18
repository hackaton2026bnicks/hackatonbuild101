from api.hooks import hosts_in, parse, repo_relative
from api.scrub import scrub


def test_relative_paths_match_across_machines():
    windows = repo_relative(r"C:\Users\ana\code\payments\api\client.py", r"C:\Users\ana\code\payments")
    linux = repo_relative("/home/bob/src/payments/api/client.py", "/home/bob/src/payments")
    assert windows == linux == "api/client.py"


def test_files_outside_the_repo_have_no_relative_path():
    assert repo_relative("/etc/hosts", "/home/bob/src/payments") is None


def test_hosts_are_extracted_from_commands():
    command = "curl -X POST https://Sandbox.BankX.com/oauth/token && curl http://localhost:8000/x"
    assert hosts_in(command) == ["localhost", "sandbox.bankx.com"]


def test_scrub_removes_common_secrets():
    text = (
        "curl -H 'Authorization: Bearer abc.def.ghi1234567' https://u:hunter2@db.example.com "
        "API_KEY=sk-live1234567890abcdef password=letmein"
    )
    cleaned = scrub(text)
    for secret in ("abc.def.ghi1234567", "hunter2", "sk-live1234567890abcdef", "letmein"):
        assert secret not in cleaned


def test_edit_payload_keeps_metadata_but_never_file_content():
    call = parse(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "cwd": "/home/bob/src/payments",
            "tool_name": "Write",
            "tool_input": {"file_path": "/home/bob/src/payments/api/client.py", "content": "TOP SECRET CODE"},
        }
    )
    assert call.file == "api/client.py"
    assert call.edits_file
    assert call.repo == "payments"
    assert "TOP SECRET CODE" not in str(call.payload)
    assert call.summary == "edit(api/client.py)"


def test_repo_header_overrides_folder_name():
    call = parse({"hook_event_name": "Stop", "session_id": "s1", "cwd": "/x/y"}, repo_header="acme/payments")
    assert call.repo == "acme/payments"


def test_failure_summary_uses_first_error_line_scrubbed():
    call = parse(
        {
            "hook_event_name": "PostToolUseFailure",
            "session_id": "s1",
            "cwd": "/r",
            "tool_name": "Bash",
            "tool_input": {
                "command": "curl https://sandbox.bankx.com/pay -H 'Authorization: Bearer zzzzzzzzzzzzzz'"
            },
            "error": "Exit code 1\nHTTP 401 invalid_token",
        }
    )
    assert call.summary == "error(Bash: Exit code 1)"
    assert "zzzzzzzzzzzzzz" not in str(call.payload)
    assert call.hosts == ["sandbox.bankx.com"]


def test_skill_subagent_and_compaction_reach_the_payload():
    skill = parse(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "cwd": "/r",
            "tool_name": "Skill",
            "tool_input": {"skill": "capitalizar"},
        }
    )
    agent = parse(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "cwd": "/r",
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "Explore"},
        }
    )
    compact = parse({"hook_event_name": "PreCompact", "session_id": "s1", "cwd": "/r", "trigger": "auto"})
    assert skill.payload["skill"] == "capitalizar" and skill.summary == "skill(capitalizar)"
    assert agent.payload["subagent_type"] == "Explore" and agent.summary == "agent(Explore)"
    assert compact.event_type == "compact" and compact.payload["trigger"] == "auto"


def test_unknown_events_are_ignored():
    assert parse({"hook_event_name": "Notification", "session_id": "s1"}) is None
    assert parse({"hook_event_name": "PreToolUse"}) is None
