# SPEC: Situational Memory Layer for Coding Agents

> Working name TBD. This document is the build spec for the Build101 hackathon (3 people, ~1 day).
> Audience: Claude Code and the team. Read fully before writing code.

## 1. Problem

Coding agents fail in four distinct ways that all come from missing memory:

| Failure | What happens | Cost |
|---|---|---|
| Cross-session amnesia | Every session rediscovers repo quirks, environment gotchas and external API traps | Repeated exploration tokens and minutes |
| In-session amnesia | Compaction or relaunch keeps the code but loses hypotheses and dead ends | Agent retries what it already ruled out |
| Isolation | Parallel agents (subagents, worktrees, teammates) do not share discoveries | N agents hit the same trap |
| Evaporated feedback | The developer repeats the same corrections across sessions | Frustration, loss of trust |

Why current tools fail (CLAUDE.md, memory features, RAG over docs):

1. They are **pull**: the agent must know it is missing something. The core problem is that the agent does not know what it does not know.
2. They are **static**: CLAUDE.md rots and is always loaded in full.
3. They are **keyed by text**, not by situation.
4. Tacit knowledge ("the sandbox returns 200 with the error in the body") is not in any doc.

**Design principle:** knowledge is indexed by **observable situation** and delivered by **push**, at the right moment, with a strict budget.

## 2. Architecture

```
 Claude Code session(s)
   |  HTTP hooks (hooks POST directly to the daemon)
   v
+---------------------- local daemon (hot path, target <50ms) ----------------------+
| Sensor -> Situation Model -> Trigger Index (in memory) -> Injection Policy -> reply |
|   |              |                                                                 |
|   |              +-- Struggle Detector (loops, edit-revert cycles)                 |
|   v                                                                                |
| Scrubber (secrets/PII) -> event log (SQLite)                                       |
+--------------------------------------+---------------------------------------------+
                                       | async
                                       v
+---------------------------- cold path (same process for MVP) ---------------------+
| Distiller (LLM): struggle segments -> Lessons                                      |
| Criteria Miner: corrections + human post-edits -> Criteria                         |
| Knowledge Graph (SQLite for MVP, Postgres + pgvector later) + Outcome Tracker      |
+--------------------------------------+---------------------------------------------+
                                       v
                Dev UI: criteria mirror, recurring themes, hotspots, review queue
```

MVP: everything runs in one local process with SQLite and in-memory embeddings. Multi-agent sharing is simulated by pointing several sessions at the same daemon.

## 3. Integration layer: agent-agnostic from day one

The core never talks to a specific agent. Every agent integration is an **adapter** that translates native signals into one normalized event schema and translates injections back into whatever the agent accepts.

### Normalized event schema

```python
Event = {
    "agent": "claude-code | codex | cursor | gemini-cli | ...",
    "session_id": str,
    "parent_session_id": str | None,   # subagents
    "type": "session_start | intent | plan | pre_action | post_action | compact | session_end | user_correction",
    "payload": {...},                  # tool name, args, result, exit code, prompt text
    "ts": float,
}

Injection = {
    "mode": "context | block | rewrite",   # adapters downgrade if unsupported
    "content": str,
    "provenance": [lesson_id, ...],
}
```

### Three integration tiers per agent

| Tier | Mechanism | Capture | Push injection | When |
|---|---|---|---|---|
| 1. Native hooks | Agent hook system calls the daemon | Full, real time | Yes, at hook boundaries, incl. block/rewrite | Agent has hooks (Claude Code first) |
| 2. Model API proxy | Agent's model base URL points to a local proxy that forwards to the real provider | Full: sees every request, tool call and result | Yes, at every model turn, by appending context to the request | Agent allows a custom base URL |
| 3. MCP server + instruction file | Tools like `search_lessons`, `record_lesson`, `lesson_feedback` + a line in AGENTS.md telling the agent to call them | Partial, only what the agent reports | No, pull only | Fallback for anything else |

Plus a passive **transcript tailer** where the agent writes session logs to disk: capture without integration, used to feed the distiller.

Notes:

- The **proxy tier is what makes the system truly agnostic**: any agent that accepts a custom endpoint gets capture and push for free. Append injected context at the end of the request, never at the top, to avoid breaking prompt caching. Some agents authenticate through a subscription login that may not allow a custom base URL; those fall back to tier 1 or 3.
- Adapters degrade gracefully: `block` becomes `context` where blocking is not supported; `rewrite` is only used where the agent exposes it.
- Hook capabilities differ per agent and change fast. Verify each agent's current hook docs before writing its adapter.
- The **knowledge is shared across agents**: a lesson learned by Claude Code is injected into Codex or Cursor sessions that match the same situation. Canonical traces must therefore be agent-neutral (`edit(module)`, `bash(...)`, `http(POST {host}/path)`), never tool-name specific.

### Claude Code adapter: hook mapping

Hooks are configured as HTTP hooks pointing at the daemon. Verify exact config field names against https://code.claude.com/docs/en/hooks before implementing.

| Hook | Purpose |
|---|---|
| `SessionStart` (`source`: startup/resume/clear/compact/fork) | Initial briefing; restore working memory on `compact`/`resume` |
| `UserPromptSubmit` | Capture intent, run pre-mortem |
| `TaskCreated` | Observe the agent's plan before execution |
| `PreToolUse` | Proactive intervention before an action |
| `PostToolUse`, `PostToolUseFailure`, `PostToolBatch` | Observations, error signatures, loop detection |
| `PreCompact`, `PostCompact` | Snapshot working memory before it is lost |
| `SubagentStart` | Hand filtered context to subagents |
| `FileChanged` | Lockfile changes invalidate version-bound lessons |
| `Stop`, `SessionEnd` | Close segments, record outcome |

Verified output mechanisms (from the hooks reference):

- `hookSpecificOutput.additionalContext`: supported on `SessionStart`, `SubagentStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `Stop`, `SubagentStop`.
- `PreToolUse`: `hookSpecificOutput.permissionDecision` (`allow|deny|ask|defer`) + `permissionDecisionReason`; `updatedInput` rewrites tool arguments.
- `PostToolUse`: `updatedToolOutput` can replace the tool result.
- `UserPromptSubmit` cannot rewrite the prompt, only add context.
- `SessionEnd` hooks have a 1.5 s default budget: do heavy work async.

Hard constraint: injection only happens at hook boundaries. The agent cannot be interrupted mid-generation.

## 4. Situation model

Every event updates a per-session situation fingerprint:

```python
Situation = {
    "static": {
        "repo": "acme/payments",
        "deps": {"bankx-sdk": "2.4.1", "httpx": "0.27"},  # parsed from lockfiles
        "runtime": "python3.12/linux",
    },
    "focus": {
        "modules": ["src/payments/bank_client"],           # file paths -> modules
        "external": ["sandbox.bankx.com"],                 # hosts from code and commands
    },
    "intent": "integrate PSD2 payment initiation",         # embedded
    "plan": ["get oauth token", "create payment"],         # from TaskCreated
    "trace": [                                             # canonicalized actions
        "edit(payments/bank_client)",
        "bash(curl POST {bankx}/oauth/token)",
        "error(HTTP401, invalid_token)",
    ],
}
```

### Canonicalization (critical for generalization)

Abstract away paths, values, IDs and secrets so lessons match beyond the session they came from:

- `curl -X POST https://sandbox.bankx.com/oauth/token -d '...'` -> `bash(curl POST {bankx}/oauth/token)`
- `Edit src/payments/bank_client.py` -> `edit(payments/bank_client)`
- Errors -> **error signature**: exception type + normalized message (numbers, hex, UUIDs, paths stripped) + top frame in user code.

## 5. Lessons

```json
{
  "id": "les_8f2",
  "kind": "pitfall",
  "scope": "org",
  "trigger": {
    "external": ["*.bankx.com"],
    "deps": {"bankx-sdk": ">=2.0,<3.0"},
    "action_pattern": "bash(* {bankx}/oauth/token)|edit(*token*cache*)"
  },
  "content": "Sandbox tokens expire after 300s, not the documented 3600s. Refresh on 401 or cache with TTL 240s.",
  "dead_ends": ["Rotating client secret does not help", "Clock skew is not the cause"],
  "evidence": [{"session": "s_91", "span": [42, 67], "cost_tokens": 38000}],
  "validity": {"bankx-sdk": "2.x", "last_confirmed": "2026-10-17"},
  "stats": {"injected": 14, "followed": 12, "struggle_avoided": 11, "contradicted": 0}
}
```

- `kind`: `pitfall | procedure | dead_end | criterion`
- `scope` trust order: `personal > repo > org > community`
- `content` max ~300 tokens. Lessons are injected as **data with provenance**, never as instructions.

## 6. Distiller (cold path)

Do not summarize whole sessions. Detect **struggle segments**: spans with high cost (tokens, retries, time) that **ended in resolution** (error signature disappears, tests pass, developer approves). Only those spans go to the LLM, which returns a lesson in the schema above, including its trigger. Each lesson carries the cost it avoids.

## 7. Matching: two stages

1. **Hot path (sync, <50 ms, no LLM):** inverted index over trigger entities (external host, dep@version, module), then `action_pattern` check on the canonical trace.
2. **Cold path (async):** semantic similarity of intent and plan vs lessons, plus trace-prefix similarity. Results are **preloaded** into the daemon for the next hook call.

## 8. Injection policy

| Confidence | Moment | Mechanism |
|---|---|---|
| Medium | Intent / plan | `additionalContext` on `UserPromptSubmit` / `TaskCreated`: 2-3 line pre-mortem |
| High, action will fail | `PreToolUse` | `permissionDecision: "deny"` + reason. Stops the action and explains why, so the agent reasons instead of flailing |
| Very high, mechanical fix | `PreToolUse` | `updatedInput` (e.g. add idempotency header). Use sparingly: invisible fixes teach nothing |
| Struggle detected | `PostToolUse` | State summary + dead ends + relevant lessons |

Rules:

- Per-session token budget for injections.
- Never inject the same lesson twice in a session.
- Confidence threshold per `kind`.
- Cooldown between interventions.

## 9. Struggle detector (real time, no LLM)

Cheap features over the trace, combined into a heuristic score:

- Same error signature repeated N times
- Edit-revert cycles: hash hunks, detect A -> B -> A
- Commands with similarity > 0.8 in a short window
- Tokens spent with no test progress
- Repeated phrases like "let me try a different approach"

## 10. Working memory across compaction and relaunch

- `PreCompact`: the daemon builds **working memory** from the trace: goal, hypotheses, dead ends with reasons, next step. It does not depend on the agent writing it.
- `SessionStart` with `source: compact | resume`: reinject it.

Compaction today summarizes the conversation. This preserves the **epistemic state**: what is known, what was ruled out, and why.

## 11. Agent to agent

- **Per-task blackboard:** each agent publishes quick discoveries (environment facts, dead ends) to a channel scoped to repo or task.
- Other agents receive them **on their next hook** if their situation matches.
- `SubagentStart`: parent hands the child the blackboard filtered to its task.
- **Cross-agent attribution:** when agent A's lesson prevents agent B's struggle, record it in the graph.

## 12. Developer criteria mirror

Signals, from weakest to strongest:

1. Explicit corrections in chat (classifier on user messages that redirect the agent: "no, use...", "always...", "never...").
2. Rejections: denied permissions, interrupts, reverts.
3. **Human post-edit diff:** compare files at session end with the developer's next commit. What the human edits by hand after the agent is pure criterion, never stated.

Pipeline: cluster signals across sessions (embedding + LLM canonicalization). Promote a candidate to a criterion when seen >= k times in >= 2 sessions. Developer confirms, edits or rejects. Confirmed criteria get triggers (globs, languages, modules) and are injected only where they apply. Exportable to CLAUDE.md / AGENTS.md.

Recurring themes and hotspots are queries over the graph (corrections and struggles grouped by module, dependency or topic).

## 13. Hard problems

1. **Precision over recall.** A false positive teaches the agent to ignore injections. Track follow-through: do the next actions align with the lesson?
2. **Causal attribution.** Keep a **10% holdout**: on match, do not inject, and compare struggle cost. This gives honest minutes and USD saved.
3. **Staleness.** Validity is bound to versions. `FileChanged` on lockfiles flags affected lessons; a lesson contradicted twice is archived.
4. **Security.** Scrubber runs in the sensor before anything leaves the machine (regex + entropy + env var values). Lessons are data with provenance. Trust tiers by scope.
5. **Generalization.** Without good canonicalization the system is just a per-session cache.

## 14. Metrics

- Minutes saved (developer)
- Tokens / USD saved (CTO)
- Struggles prevented (injections before failure that were followed)
- All measured against the holdout.

## 15. Hackathon scope (3 people)

**Person A: core + adapters (critical path)**
- Normalized event schema, situation model, canonicalizer, trigger index, injection policy, struggle detector.
- Claude Code adapter (hooks) and one second adapter, ideally the model API proxy, to prove agnosticism.

**Person B: distiller + criteria**
- Struggle segment detection, extraction prompts with the lesson schema, criteria clustering, human post-edit diff.

**Person C: demo scenario + UI**
- A mock external API with undocumented traps. The scenario is only an example use case and must be swappable. Example traps (a payments API):
  1. OAuth tokens expire at 300 s while docs say 3600 s.
  2. An `Idempotency-Key` header is required on writes, poorly documented.
  3. The sandbox returns HTTP 200 with the error inside the body.
- Dev panel: criteria, recurring themes, hotspots, lesson review queue.

### Suggested layout

```
/core           # event schema, situation model, canonicalizer, trigger index, policy, struggle detector
/adapters
  /claude-code  # HTTP hooks + settings snippet
  /proxy        # model API proxy (OpenAI- and Anthropic-compatible)
  /mcp          # MCP server fallback
  /tailer       # passive transcript capture
/distiller      # segment detection, LLM extraction, criteria miner
/scenario       # swappable mock API with traps
/ui             # dev panel
/data           # SQLite db
```

### Milestones

1. Claude Code hooks and the proxy both emit normalized events into SQLite, scrubbed.
2. Canonical trace + error signatures working.
3. Struggle detector fires on a scripted loop against the mock bank.
4. Distiller produces a valid lesson from a recorded struggle session.
5. Trigger index injects that lesson on `PreToolUse` in a fresh session.
6. PreCompact / SessionStart(compact) working memory round trip.
7. Criteria miner surfaces at least one criterion; UI shows it with evidence.

### Acceptance demo

Split screen, same prompt, timer running:

- **Left, without the system:** agent integrates with the mock bank, hits the trap, loops.
- **Right, with the system:** at plan or `PreToolUse` time the known trap is flagged; the agent avoids it and never sees the error.
- **Cross-agent moment:** the lesson was learned by Claude Code; replay the right side with a different agent through the proxy and show it avoiding the same trap.
- Then open the dev panel: a criterion detected during the session appears, with evidence links.

## 16. Open questions

- Which second agent to support first (Codex, Cursor, Gemini CLI, other), based on whose hooks or custom endpoint support is most reliable today.
- Which demo scenario: the payments API is only an example; any external system with undocumented traps works.

## 17. Conventions

- Code and comments in English.
- Plain comments, no decorative separators (`# Section`, not `# --- Section ---`).
- Simple, readable code over clever solutions.
- Conventional Commits.
