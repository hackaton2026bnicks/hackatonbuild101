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
