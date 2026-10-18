import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


session_end = load("session_end", ROOT / "agent" / "claude-code" / "session_end.py")
sys.path.insert(0, str(ROOT / "tools"))
work_card = load("work_card", ROOT / "tools" / "work_card.py")


def fake_popen():
    calls = []

    def popen(args, **kwargs):
        calls.append((args, kwargs))

    return popen, calls


def test_the_hook_detaches_the_measurement_and_returns():
    popen, calls = fake_popen()
    raw = {"hook_event_name": "SessionEnd", "session_id": "abc-123", "transcript_path": "/t/abc-123.jsonl"}
    assert session_end.start_worker(raw, {}, popen=popen)
    args, kwargs = calls[0]
    assert args[-3:] == ["--worker", "abc-123", "/t/abc-123.jsonl"]
    # Its own process group and no pipe back to Claude Code, so closing never waits for it
    assert kwargs["start_new_session"] is True
    assert {kwargs[k] for k in ("stdin", "stdout", "stderr")} == {subprocess.DEVNULL}


def test_card_runs_and_odd_session_ids_are_not_measured():
    popen, calls = fake_popen()
    assert not session_end.start_worker({"session_id": "abc"}, {"BUILD101_INSIGHTS_CHILD": "1"}, popen=popen)
    assert not session_end.start_worker({"session_id": "abc"}, {"DIK_EVALUATOR": "1"}, popen=popen)
    assert not session_end.start_worker({"session_id": "../../etc/passwd"}, {}, popen=popen)
    assert calls == []


def test_one_measurement_per_close(tmp_path):
    lock = session_end.claim(tmp_path, "abc")
    assert lock and session_end.claim(tmp_path, "abc") is None
    lock.unlink()
    assert session_end.claim(tmp_path, "abc")


FACTS = {
    "session_id": "abc",
    "start": "2026-10-18T01:00:00+00:00",
    "repo": "hackatonbuild101",
    "title": "t",
    "active_min": 12.0,
    "human_prompts": 2,
    "tool_calls": 5,
    "subagent_tool_calls": 0,
    "sequence": ["read(.py)", "skill:capitalizar", "bash:git commit"],
    "skills": {"capitalizar": 1},
    "subagents": {},
    "tool_errors": 0,
    "interrupts": 0,
    "denials": 0,
    "corrections": 0,
    "compactions": 0,
    "files_edited": 1,
    "commits": 1,
    "prs": 0,
    "pr_links": 0,
    "artifacts": 0,
    "spawned": False,
    "peer_msgs": 0,
    "prompts": ["hacé el hook"],
    "user": "ana",
}


def test_the_card_run_cannot_reach_the_team_server_or_trigger_itself(monkeypatch):
    monkeypatch.setenv("BUILD101_KEY", "b101_secret")
    seen = {}

    def run(cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        result = json.dumps({"session_id": "other", "trabajo": "desarrollar", "resultado": "resuelto"})
        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"result": result, "total_cost_usd": 0.001}), stderr=""
        )

    card = work_card.make_card(FACTS, "haiku", run=run)
    assert card["session_id"] == "abc" and card["trabajo"] == "desarrollar"
    assert card["_meta"]["model"] == "haiku"
    assert "BUILD101_KEY" not in seen["env"] and seen["env"]["BUILD101_INSIGHTS_CHILD"] == "1"
    assert "--no-session-persistence" in seen["cmd"] and seen["cmd"][seen["cmd"].index("--tools") + 1] == ""
    assert '"skills": {"capitalizar": 1}' in seen["input"]


def test_the_newest_measurement_of_a_session_wins(tmp_path):
    import os

    baseline, hook = tmp_path / "facts_baseline.jsonl", tmp_path / "facts.jsonl"
    hook.write_text('{"session_id": "a", "v": "hook-1"}\n{"session_id": "a", "v": "hook-2"}\n')
    baseline.write_text('{"session_id": "a", "v": "base"}\n{"session_id": "b", "v": "base"}\nnot json\n')
    os.utime(baseline, (1, 1))
    rows = work_card.latest([str(tmp_path / "facts*.jsonl")])
    assert rows["a"]["v"] == "hook-2" and rows["b"]["v"] == "base"
