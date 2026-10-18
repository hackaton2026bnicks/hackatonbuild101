"""The local extractor: from a transcript to vault lessons, without the transcript leaving the machine."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent" / "claude-code"
sys.path.insert(0, str(AGENT_DIR))
spec = importlib.util.spec_from_file_location("learn", AGENT_DIR / "learn.py")
learn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learn)


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def tool_use(tool_id, name, **args):
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": tool_id, "name": name, "input": args}]},
    }


def result(tool_id, text, error=False):
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": text, "is_error": error}
    return {"type": "user", "message": {"content": [block]}}


def say(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


SOLVED = [
    user("<system-reminder>worktree</system-reminder>"),
    user("get a token from the bank sandbox"),
    tool_use(
        "t1",
        "Bash",
        command="curl -d '{}' https://sandbox.bankx.com/oauth/token -H 'Authorization: Bearer "
        "sk-live1234567890abcdef'",
    ),
    result("t1", "415 Unsupported Media Type", error=True),
    say("The endpoint rejects JSON; trying form encoding."),
    tool_use(
        "t2",
        "Bash",
        command="curl --data-urlencode grant_type=client_credentials https://sandbox.bankx.com/oauth/token",
    ),
    result("t2", '{"access_token": "..."}'),
]
QUIET = [
    user("rename the payouts module"),
    tool_use("t1", "Edit", file_path="/repo/payouts.py"),
    result("t1", "ok"),
]


def write(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return str(path)


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(learn, "STATE_DIR", tmp_path / "state")


def test_a_solved_error_is_a_signal_and_secrets_and_harness_text_are_dropped():
    lines = [json.dumps(e) for e in SOLVED]
    out, signals = learn.condense(lines, [])
    assert out[0] == "USER: get a token from the bank sandbox"
    assert [out[i] for i in signals] == ["ERROR (Bash): 415 Unsupported Media Type"]
    assert "OK (Bash) after the error above" in out
    text = learn.episode(out, signals)
    assert "sk-live1234567890abcdef" not in text and "system-reminder" not in text


def test_corrections_and_requests_to_remember_are_signals():
    for prompt in ("no, eso no: el stock está en unidades", "acordate que staging usa otra base"):
        _, signals = learn.condense([json.dumps(user(prompt))], [])
        assert signals == [0]
    assert learn.condense([json.dumps(e) for e in QUIET], [])[1] == []


def test_lessons_go_to_the_archivista_queue_and_each_line_is_read_once(tmp_path):
    transcript = write(tmp_path / "s1.jsonl", SOLVED)
    asked, sent = [], []
    lesson = {"type": "gotcha", "title": "Token wants form data", "body": "JSON: 415; use a form.",
              "aplica_cuando": "", "dead_ends": ["sending JSON"], "files": ["bank/client.py"],
              "hosts": ["sandbox.bankx.com"], "keywords": ["415", "oauth"]}  # fmt: skip

    def ask(text, model):
        asked.append(text)
        return [lesson]

    assert learn.learn("s1", transcript, "payments", {}, ask=ask, send=sent.append) == 1
    assert sent == [
        {
            "texto": "JSON: 415; use a form.\nLo que parecía y no era: sending JSON."
            "\nHosts: sandbox.bankx.com.",
            "titulo": "Token wants form data",
            "tipo": "gotcha",
            "aplica_cuando": "415, oauth",
            "archivos": ["bank/client.py"],
            "fuente": learn.FUENTE,
            "repo": "payments",
            "session_id": "s1",
        }
    ]
    assert "415 Unsupported Media Type" in asked[0]

    assert learn.learn("s1", transcript, "payments", {}, ask=ask, send=sent.append) == 0
    assert len(asked) == 1  # nothing new since the last run, so no model call


def test_a_session_without_signals_costs_no_model_call(tmp_path):
    transcript = write(tmp_path / "s2.jsonl", QUIET)

    def ask(text, model):
        raise AssertionError("should not be called")

    assert learn.learn("s2", transcript, "payments", {}, ask=ask, send=lambda body: None) == 0


def test_model_output_is_validated():
    text = 'Here: [{"type": "dato", "title": "T", "body": "B", "files": ["a.py", 3]}, {"title": ""}, 5]'
    assert learn.parse_units(text) == [
        {
            "type": None,
            "title": "T",
            "body": "B",
            "aplica_cuando": "",
            "dead_ends": [],
            "files": ["a.py"],
            "hosts": [],
            "keywords": [],
        }
    ]
    assert learn.parse_units("no lessons here") == []


def test_the_hook_starts_a_detached_worker_only_when_it_should():
    calls = []

    def popen(args, **kwargs):
        calls.append(args)

    raw = {"session_id": "abc-1", "transcript_path": "/t/abc-1.jsonl", "cwd": "/repo/payments"}
    assert learn.start_worker(raw, {"BUILD101_KEY": "k", "CLAUDE_PROJECT_DIR": "/repo/payments"}, popen)
    assert calls[0][-4:] == ["--worker", "abc-1", "/t/abc-1.jsonl", "payments"]

    assert not learn.start_worker(raw, {}, popen)  # no key
    assert not learn.start_worker(raw, {"BUILD101_KEY": "k", "BUILD101_LEARN": "0"}, popen)
    assert not learn.start_worker(raw, {"BUILD101_KEY": "k", "BUILD101_LEARN_CHILD": "1"}, popen)
    assert not learn.start_worker({**raw, "session_id": "../etc"}, {"BUILD101_KEY": "k"}, popen)
