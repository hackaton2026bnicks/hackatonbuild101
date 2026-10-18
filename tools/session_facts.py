#!/usr/bin/env python3
"""Capa A de DIK v2: hechos deterministicos por sesion, desde los transcripts locales.

Lee ~/.claude/projects/*/*.jsonl (y los subagentes de cada sesion) y escribe una
linea JSON por sesion con volumen, herramientas, fricciones, salidas y la secuencia
canonica de acciones. No usa LLM y no manda nada a ningun lado. Escribe en ~/.claude/session-insights.

    python3 tools/session_facts.py                   # toda la historia -> facts_baseline.jsonl
    python3 tools/session_facts.py --since 2026-10-01 --share --out facts-share.jsonl
    python3 tools/session_facts.py --session <id>    # solo esa sesion, agregada al final de facts.jsonl

--share quita prompts, titulos y comandos (deja solo conteos y tokens canonicos),
para poder juntar los hechos de todo el equipo sin mover texto de nadie.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import shlex
from datetime import datetime

PROJECTS = os.path.expanduser("~/.claude/projects")
INSIGHTS_DIR = os.path.expanduser(os.environ.get("BUILD101_INSIGHTS_DIR", "~/.claude/session-insights"))
IDLE_GAP_S = 300  # un hueco mayor a 5 min no cuenta como tiempo activo

CORRECTION_RE = re.compile(
    r"^\s*(no[,.! ]|no$|nop|eso no|pero |pará|para,|espera|esperá|mal\b|está mal|esta mal|"
    r"no es|no era|te equivocaste|otra vez|de nuevo|sigue sin|sigue igual|no funciona|no anda|"
    r"no hace falta|no hagas|no toques|dejá|deja eso|revert|volvé|volve atras)",
    re.I,
)
MARKERS = (
    "<local-command",
    "<command-name>",
    "<task-notification>",
    "<bash-input>",
    "<scheduled-task",
    "<bash-stdout>",
    "<system-reminder>",
    "Caveat:",
    "This session is being continued",
)


def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def repo_of(cwd: str) -> str:
    if not cwd:
        return "?"
    cwd = re.sub(r"/\.claude/worktrees/[^/]+.*$", "", cwd)
    home = os.path.expanduser("~")
    rel = cwd[len(home) + 1 :] if cwd.startswith(home) else cwd
    parts = rel.split("/")
    if parts and parts[0] in ("repos", "agents") and len(parts) > 1:
        return "/".join(parts[1:3])
    return rel or "~"


def bash_token(cmd: str) -> str:
    """Reduce un comando de shell a un token estable: ssh{host}, docker logs, git commit..."""
    cmd = cmd.strip()
    segs = re.split(r"\s*(?:&&|;|\|\||\|)\s*", cmd)
    for seg in segs:
        seg = seg.strip()
        if not seg or seg.startswith(("cd ", "export ", "source ", "echo ", "set ")) or seg in ("cd",):
            continue
        try:
            w = shlex.split(seg, posix=True)
        except ValueError:
            w = seg.split()
        while w and ("=" in w[0] and not w[0].startswith("-")):
            w = w[1:]
        if not w:
            continue
        head = os.path.basename(w[0])
        if head in ("sudo", "time", "timeout", "nohup", "env"):
            w = w[1:] if head != "timeout" else w[2:]
            if not w:
                continue
            head = os.path.basename(w[0])
        args = [a for a in w[1:] if not a.startswith("-")]
        if head in ("ssh", "scp"):
            rest, i = w[1:], 0
            while i < len(rest) and rest[i].startswith("-"):
                i += 2 if rest[i][-1] in "bcDEeFIiJLlmOoPpQRSWw" and len(rest[i]) == 2 else 1
            host = rest[i].split("@")[-1].split(":")[0] if i < len(rest) else "?"
            return f"{head}{{{host}}}"
        if head in ("scp", "rsync"):
            return head
        if head in (
            "git",
            "gh",
            "docker",
            "npm",
            "pnpm",
            "yarn",
            "npx",
            "pip",
            "pip3",
            "brew",
            "kubectl",
            "systemctl",
            "uv",
            "poetry",
            "make",
            "supabase",
            "vercel",
            "railway",
        ):
            sub = args[0] if args else ""
            if head == "docker" and sub == "compose":
                sub = "compose " + (args[1] if len(args) > 1 else "")
            if head == "gh" and len(args) > 1:
                sub = f"{args[0]} {args[1]}"
            return f"{head} {sub}".strip()
        if head in ("python", "python3", "node", "bash", "sh", "zsh", "deno", "bun"):
            script = next((a for a in args), "")
            if head.startswith("python") and "-c" in w:
                return "python -c"
            if head.startswith("python") and "-" in w[1:2]:
                return "python stdin"
            return f"{head} {os.path.basename(script)}".strip() if script else head
        if head == "curl":
            url = next((a for a in args if a.startswith("http")), "")
            m = re.match(r"https?://([^/:]+)", url)
            return f"curl{{{m.group(1)}}}" if m else "curl"
        if head in ("psql", "mysql", "sqlite3"):
            return head
        return head
    return "shell"


def python_purpose(cmd: str) -> str:
    """Para qué es un heredoc de Python: sql, datos, http o edición de archivos."""
    if re.search(r"psycopg|sqlite3|sqlalchemy|\bselect\b.+\bfrom\b", cmd, re.I | re.S):
        return "sql"
    if re.search(r"pandas|openpyxl|csv|parquet|pyarrow|json\.load", cmd):
        return "datos"
    if re.search(r"requests|urllib|httpx", cmd):
        return "http"
    if re.search(r"re\.sub|\.replace\(|open\([^)]*['\"]w|write_text", cmd):
        return "editar"
    return "otro"


def tool_token(name: str, inp: dict) -> str:
    if name == "Bash":
        cmd = inp.get("command", "")
        tok = bash_token(cmd)
        if tok in ("python stdin", "python -c"):
            tok = f"python:{python_purpose(cmd)}"
        return "bash:" + tok
    if name.startswith("mcp__"):
        parts = name.split("__")
        server = parts[1] if len(parts) > 1 else "?"
        tool = parts[2] if len(parts) > 2 else "?"
        if re.match(r"^[0-9a-f]{8}-", server):
            server = "connector"
        return f"mcp:{server}.{tool}"
    if name == "Skill":
        return "skill:" + str(inp.get("skill", "?"))
    if name in ("Agent", "Task"):
        return "agent:" + str(inp.get("subagent_type") or "general-purpose")
    if name in ("Read", "Edit", "Write", "NotebookEdit", "MultiEdit"):
        p = inp.get("file_path") or inp.get("notebook_path") or ""
        ext = os.path.splitext(p)[1] or "noext"
        return f"{name.lower()}({ext})"
    return name


def human_text(d: dict):
    """Devuelve el texto si la linea es un prompt escrito por una persona, si no None."""
    if d.get("isMeta") or d.get("isCompactSummary"):
        return None
    origin = d.get("origin") or {}
    if origin and origin.get("kind") != "human":
        return None
    if not origin and d.get("promptSource") in ("system", "sdk"):
        return None
    cont = (d.get("message") or {}).get("content")
    if isinstance(cont, str):
        txt = cont
    elif isinstance(cont, list):
        blocks = [b for b in cont if isinstance(b, dict)]
        if any(b.get("type") == "tool_result" for b in blocks):
            return None
        txt = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if not txt.strip() and any(b.get("type") == "image" for b in blocks):
            txt = "[imagen]"
    else:
        return None
    t = txt.strip()
    if not t or t.startswith("[Request interrupted"):
        return None
    if any(t.startswith(m) or m in t[:120] for m in MARKERS):
        return None
    return t


def tool_result_text(b: dict) -> str:
    c = b.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def scan_file(path: str):
    """Itera lineas JSON validas."""
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except Exception:
                continue


def usage_add(acc, seen, d):
    m = d.get("message") or {}
    mid = m.get("id") or d.get("requestId")
    u = m.get("usage")
    if not u or (mid and mid in seen):
        return
    if mid:
        seen.add(mid)
    acc["input"] += u.get("input_tokens", 0) or 0
    acc["output"] += u.get("output_tokens", 0) or 0
    acc["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
    acc["cache_write"] += u.get("cache_creation_input_tokens", 0) or 0


def subagent_facts(session_dir: str):
    out = {"n": 0, "tool_calls": 0, "tokens": collections.Counter(), "types": collections.Counter()}
    for p in glob.glob(os.path.join(session_dir, "subagents", "*.jsonl")):
        out["n"] += 1
        seen = set()
        for d in scan_file(p):
            if d.get("type") == "assistant":
                usage_add(out["tokens"], seen, d)
                for b in (d.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        out["tool_calls"] += 1
        meta = p[:-6] + ".meta.json"
        if os.path.exists(meta):
            try:
                out["types"][json.load(open(meta)).get("agentType", "?")] += 1
            except Exception:
                pass
    return out


def session_facts(path: str, share: bool = False):
    sid = os.path.basename(path)[:-6]
    f = {
        "session_id": sid,
        "project_dir": os.path.basename(os.path.dirname(path)),
        "cwd": None,
        "repo": None,
        "branches": set(),
        "entrypoint": None,
        "versions": set(),
        "models": collections.Counter(),
        "title": None,
        "ai_title": None,
        "start": None,
        "end": None,
        "active_min": 0.0,
        "wall_min": 0.0,
        "human_prompts": 0,
        "prompt_chars": 0,
        "prompts": [],
        "slash_commands": collections.Counter(),
        "peer_msgs": 0,
        "task_notifications": 0,
        "assistant_msgs": 0,
        "tool_calls": 0,
        "tools": collections.Counter(),
        "mcp": collections.Counter(),
        "skills": collections.Counter(),
        "subagents": collections.Counter(),
        "bash": collections.Counter(),
        "files_edited": set(),
        "files_read": set(),
        "tool_errors": 0,
        "error_tools": collections.Counter(),
        "api_errors": 0,
        "interrupts": 0,
        "denials": 0,
        "compactions": 0,
        "corrections": 0,
        "correction_examples": [],
        "commits": 0,
        "prs": 0,
        "pr_links": 0,
        "artifacts": 0,
        "sessions_started": 0,
        "plan_mode": 0,
        "web": 0,
        "browser": 0,
        "tokens": collections.Counter(),
        "cost_usd": None,
        "lines_added": None,
        "lines_removed": None,
        "sequence": [],
        "permission_modes": collections.Counter(),
        "spawned": False,
        "scheduled_task": None,
    }
    seen = set()
    last_t = None
    for d in scan_file(path):
        t = d.get("type")
        u = d.get("uuid")
        if u and t in ("user", "assistant"):
            if u in seen_uuids:  # sesion bifurcada: las lineas copiadas ya se contaron en la original
                continue
            seen_uuids.add(u)
        ts = parse_ts(d.get("timestamp", "")) if d.get("timestamp") else None
        if ts:
            f["start"] = f["start"] or ts
            if last_t:
                gap = (ts - last_t).total_seconds()
                if 0 < gap <= IDLE_GAP_S:
                    f["active_min"] += gap / 60
            last_t = ts
            f["end"] = ts
        if d.get("cwd") and not f["cwd"]:
            f["cwd"] = d["cwd"]
        if d.get("gitBranch"):
            f["branches"].add(d["gitBranch"])
        if d.get("entrypoint") and not f["entrypoint"]:
            f["entrypoint"] = d["entrypoint"]
        if d.get("version"):
            f["versions"].add(d["version"])
        if d.get("permissionMode"):
            f["permission_modes"][d["permissionMode"]] += 1

        if t == "custom-title":
            f["title"] = d.get("customTitle")
        elif t == "ai-title":
            f["ai_title"] = d.get("aiTitle") or d.get("title")
        elif t == "pr-link":
            f["pr_links"] += 1
        elif t == "cost-state":
            f["cost_usd"] = d.get("totalCostUSD")
            f["lines_added"] = d.get("totalLinesAdded")
            f["lines_removed"] = d.get("totalLinesRemoved")
        elif t == "system":
            st = d.get("subtype")
            if st == "compact_boundary":
                f["compactions"] += 1
            elif st == "api_error":
                f["api_errors"] += 1
        elif t == "user":
            origin = d.get("origin") or {}
            if origin.get("kind") == "peer":
                f["peer_msgs"] += 1
            elif origin.get("kind") == "task-notification":
                f["task_notifications"] += 1
            cont = (d.get("message") or {}).get("content")
            if isinstance(cont, list):
                for b in cont:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_result":
                        txt = tool_result_text(b)
                        if "doesn't want to proceed" in txt[:200]:
                            f["denials"] += 1
                        elif b.get("is_error"):
                            f["tool_errors"] += 1
                            tok = tool_ids.get(b.get("tool_use_id"))
                            if tok:
                                f["error_tools"][tok] += 1
                    elif b.get("type") == "text" and b.get("text", "").startswith("[Request interrupted"):
                        f["interrupts"] += 1
            elif isinstance(cont, str):
                if cont.startswith("Background (from the session that started this one)"):
                    f["spawned"] = True
                m = re.match(r'<scheduled-task name="([^"]+)"', cont)
                if m:
                    f["scheduled_task"] = m.group(1)
                if cont.startswith("[Request interrupted"):
                    f["interrupts"] += 1
                m = re.search(r"<command-name>(/[^<]+)</command-name>", cont[:300])
                if m:
                    f["slash_commands"][m.group(1).strip()] += 1
            txt = human_text(d)
            if txt is not None:
                f["human_prompts"] += 1
                f["prompt_chars"] += len(txt)
                if CORRECTION_RE.match(txt) and f["human_prompts"] > 1:
                    f["corrections"] += 1
                    if len(f["correction_examples"]) < 3:
                        f["correction_examples"].append(txt[:160])
                if len(f["prompts"]) < 40:
                    f["prompts"].append(txt[:400])
        elif t == "assistant":
            f["assistant_msgs"] += 1
            m = d.get("message") or {}
            if m.get("model") and m["model"] != "<synthetic>":
                f["models"][m["model"]] += 1
            usage_add(f["tokens"], seen, d)
            for b in m.get("content") or []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                name, inp = b.get("name", "?"), b.get("input") or {}
                tok = tool_token(name, inp)
                tool_ids[b.get("id")] = tok
                f["tool_calls"] += 1
                f["tools"][name] += 1
                f["sequence"].append(tok)
                if name.startswith("mcp__"):
                    f["mcp"][tok.split(".")[0][4:]] += 1
                if name == "Skill":
                    f["skills"][str(inp.get("skill"))] += 1
                if name in ("Agent", "Task"):
                    f["subagents"][str(inp.get("subagent_type") or "general-purpose")] += 1
                if name == "Bash":
                    bt = bash_token(inp.get("command", ""))
                    f["bash"][bt] += 1
                    c = inp.get("command", "")
                    if re.search(r"\bgit\b[^|;&]*\bcommit\b", c):
                        f["commits"] += 1
                    if re.search(r"\bgh pr create\b", c):
                        f["prs"] += 1
                if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                    f["files_edited"].add(inp.get("file_path") or inp.get("notebook_path") or "?")
                if name == "Read":
                    f["files_read"].add(inp.get("file_path", "?"))
                if name == "Artifact" and inp.get("action", "publish") == "publish":
                    f["artifacts"] += 1
                if name.endswith("start_session"):
                    f["sessions_started"] += 1
                if name in ("EnterPlanMode", "ExitPlanMode"):
                    f["plan_mode"] += 1
                if name in ("WebSearch", "WebFetch"):
                    f["web"] += 1
                if "Browser" in name or "claude-in-chrome" in name:
                    f["browser"] += 1

    sub = subagent_facts(os.path.join(os.path.dirname(path), sid))
    f["subagent_files"] = sub["n"]
    f["subagent_tool_calls"] = sub["tool_calls"]
    f["subagent_tokens"] = dict(sub["tokens"])
    f["repo"] = repo_of(f["cwd"] or "")
    if f["start"] and f["end"]:
        f["wall_min"] = round((f["end"] - f["start"]).total_seconds() / 60, 1)
    f["active_min"] = round(f["active_min"], 1)
    f["start"] = f["start"].isoformat() if f["start"] else None
    f["end"] = f["end"].isoformat() if f["end"] else None
    f["files_edited"] = len(f["files_edited"])
    f["files_read"] = len(f["files_read"])
    for k in ("branches", "versions"):
        f[k] = sorted(f[k])
    f["first_prompt"] = f["prompts"][0] if f["prompts"] else None
    if share:
        for k in ("prompts", "first_prompt", "title", "ai_title", "correction_examples", "cwd", "branches"):
            f[k] = None
    return f


tool_ids: dict = {}
seen_uuids: set = set()


def dumps(f: dict) -> str:
    return json.dumps(f, ensure_ascii=False, default=lambda o: dict(o) if isinstance(o, dict) else str(o))


def transcript_of(session_id: str, projects: str = PROJECTS):
    hits = glob.glob(os.path.join(projects, "*", f"{glob.escape(session_id)}.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def facts_for(path: str, user: str, share: bool = False):
    """Hechos de un solo transcript, o None si la sesion no tuvo ni prompts ni acciones."""
    tool_ids.clear()
    f = session_facts(path, share=share)
    if not f["start"] or (f["human_prompts"] == 0 and f["tool_calls"] == 0):
        return None
    f["user"] = user
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", default=PROJECTS)
    # facts.jsonl lo llena el hook SessionEnd y no se reescribe: Claude Code borra los transcripts viejos
    ap.add_argument("--out", help="por defecto facts_baseline.jsonl, o facts.jsonl con --session")
    ap.add_argument("--since", help="YYYY-MM-DD, solo sesiones que empezaron desde esa fecha")
    ap.add_argument("--user", default=os.environ.get("USER", "me"))
    ap.add_argument("--share", action="store_true", help="sin texto: solo conteos y tokens canonicos")
    ap.add_argument("--session", action="append", help="solo esa sesion, repetible; agrega al final de --out")
    a = ap.parse_args()
    a.out = a.out or os.path.join(INSIGHTS_DIR, "facts.jsonl" if a.session else "facts_baseline.jsonl")

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    if a.session:
        found = {s: transcript_of(s, a.projects) for s in a.session}
        if not all(found.values()):
            print("sin transcript: " + ", ".join(s for s, p in found.items() if not p))
        paths = [p for p in found.values() if p]
    else:
        paths = sorted(glob.glob(os.path.join(a.projects, "*", "*.jsonl")), key=os.path.getmtime)
    n = 0
    with open(a.out, "a" if a.session else "w") as out:
        for path in paths:
            f = facts_for(path, a.user, share=a.share)
            if not f or (a.since and f["start"][:10] < a.since):
                continue
            out.write(dumps(f) + "\n")
            n += 1
    print(f"{n} sesiones -> {a.out}")


if __name__ == "__main__":
    main()
