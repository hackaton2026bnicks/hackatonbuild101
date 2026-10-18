"""Sessions of one person in the same folder share a branch: they are told, and warned before a switch."""

import pytest
from test_collab import ANA_REPO, bash, hook, output

from api import collab


@pytest.fixture(autouse=True)
def no_holdout(monkeypatch):
    monkeypatch.setattr(collab, "HOLDOUT_PERCENT", 0)


def test_a_second_session_in_the_same_folder_is_told_and_the_person_sees_it(client, make_key):
    ana = make_key("ana")
    hook(client, ana, "UserPromptSubmit", "s-1", ANA_REPO, prompt="refactor the payouts module")

    started = hook(client, ana, "SessionStart", "s-2", ANA_REPO)
    assert (
        '1 other session of yours ("refactor the payouts module") already works in this folder'
        in (output(started)["additionalContext"])
    )
    assert "Use a worktree" in started["systemMessage"]


def test_a_branch_switch_warns_the_session_running_it_and_tells_the_other(client, make_key):
    ana = make_key("ana")
    hook(client, ana, "UserPromptSubmit", "s-1", ANA_REPO, prompt="refactor the payouts module")
    hook(client, ana, "UserPromptSubmit", "s-2", ANA_REPO, prompt="fix the login bug please")

    switching = bash(client, ana, "s-2", ANA_REPO, "git checkout -b fix/login", event="PreToolUse")
    assert "This command changes the files under them too" in output(switching)["additionalContext"]
    assert "changing the branch of a folder" in switching["systemMessage"]

    told = output(bash(client, ana, "s-1", ANA_REPO, "ls"))["additionalContext"]
    assert "is running `git checkout -b fix/login` in your folder" in told


def test_worktrees_other_people_and_harmless_commands_are_left_alone(client, make_key):
    ana, bob = make_key("ana"), make_key("bob")
    hook(client, ana, "UserPromptSubmit", "s-1", ANA_REPO, prompt="refactor the payouts module")
    worktree = ANA_REPO + "/.claude/worktrees/login"
    hook(
        client,
        ana,
        "UserPromptSubmit",
        "s-2",
        ANA_REPO,
        prompt="fix the login bug please",
        workspace=worktree,
    )
    hook(client, bob, "UserPromptSubmit", "s-bob", ANA_REPO, prompt="same path on another machine")

    in_worktree = hook(
        client, ana, "PreToolUse", "s-2", ANA_REPO, tool_name="Bash",
        tool_input={"command": "git checkout main"}, workspace=worktree,
    )  # fmt: skip
    assert "shared folder" not in str(in_worktree)
    assert "shared folder" not in str(bash(client, ana, "s-1", ANA_REPO, "git status", event="PreToolUse"))
    assert "shared folder" not in str(hook(client, bob, "SessionStart", "s-bob", ANA_REPO))
