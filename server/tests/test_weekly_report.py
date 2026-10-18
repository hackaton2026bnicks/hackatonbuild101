import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import weekly_report  # noqa: E402


def session(sid, day, tools, trabajo="desarrollar"):
    facts = {
        "session_id": sid,
        "start": f"{day}T15:00:00+00:00",
        "repo": "r",
        "title": sid,
        "user": "ana",
        "active_min": 20.0,
        "human_prompts": 2,
        "tool_calls": len(tools),
        "subagent_tool_calls": 0,
        "sequence": tools,
        "tool_errors": 1,
        "interrupts": 0,
        "denials": 0,
        "corrections": 0,
        "compactions": 0,
        "tokens": {"input": 1000, "output": 500},
        "scheduled_task": None,
    }
    card = {
        "session_id": sid,
        "trabajo": trabajo,
        "resultado": "resuelto",
        "valor": "medio",
        "intencion": "x",
        "podria_ayudar": [{"tipo": "skill", "que": "receta de deploy"}],
        "capital_creado": ["script:deploy.sh"],
    }
    return facts, card


def test_the_week_is_compared_with_the_previous_four_and_new_patterns_show():
    old = ["read(.py)", "bash:git status", "skill:capitalizar"]
    new = ["bash:ssh{vps}", "bash:docker", "bash:curl{api.x}", "bash:ssh{vps}"]
    pairs = [session(f"b{i}", f"2026-09-{14 + i}", old) for i in range(6)]
    pairs += [session(f"c{i}", f"2026-10-1{3 + i}", new) for i in range(3)]
    rows = [f for f, _ in pairs]
    cards = {c["session_id"]: c for _, c in pairs}
    text = weekly_report.report(rows, cards, "2026-W42")
    for letter in "HLKFRC":
        assert f"| **{letter}** |" in text
    assert "## Top 3 oportunidades" in text and "receta de deploy" in text
    assert "## 3 sesiones ejemplares" in text and "**c0**" in text
    assert "`bash:ssh{vps} → bash:docker → bash:curl{api.x}" in text


def test_a_first_week_without_history_still_reports():
    facts, card = session("a", "2026-10-13", ["read(.py)"])
    text = weekly_report.report([facts], {"a": card}, "2026-W42")
    assert "Sin semanas anteriores con datos" in text
    assert weekly_report.report([], {}, "2026-W42").endswith("Sin sesiones medidas esta semana.\n")
