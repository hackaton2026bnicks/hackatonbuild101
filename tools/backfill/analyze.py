"""Aggregate facts and extractor cards into the tables the E0 report is written from.

Usage: python3 tools/backfill/analyze.py [--out data/backfill]

Writes:
  <out>/summary.md     totals, work types, frictions, capability use, repeated action sequences
  <out>/units.jsonl    every knowledge unit candidate, flattened, with its session and a near-duplicate group
  <out>/ideas.md       every "could help", repeated step and correction, grouped by work type, for clustering
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import statistics
from collections import Counter, defaultdict

KEEP_GIT = {"git:commit", "git:push", "gh:pr", "gh:run", "gh:api"}
FEATURES = {
    "plan mode": ("EnterPlanMode", "ExitPlanMode"),
    "subagentes": ("Agent", "Task"),
    "skills": ("Skill",),
    "workflow": ("Workflow",),
    "monitor / loop": ("Monitor", "ScheduleWakeup", "CronCreate"),
    "otra sesión": ("mcp__ccd_session__start_session", "SendMessage", "mcp__ccd_session_mgmt__send_message"),
    "artifacts": ("Artifact",),
    "navegador": (
        "mcp__Claude_Browser__navigate", "mcp__Claude_Browser__computer", "mcp__claude-in-chrome__navigate",
    ),
    "worktree": ("EnterWorktree",),
    "tareas programadas": ("mcp__scheduled-tasks__create_scheduled_task",),
}


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-záéíóúñ0-9_]{4,}", text.lower())}


def table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def coarse(token: str) -> str | None:
    """Map a canonical action to the level where repetition across sessions shows; None drops it."""
    if token.startswith(("mcp:Claude_Browser.", "mcp:claude-in-chrome.")):
        return "navegador"
    if token.startswith(("mcp:ccd_", "mcp:visualize", "mcp:terminal", "mcp:mcp-registry")):
        return None
    if token.startswith("mcp:"):
        return token.split(".")[0]
    if token.startswith("ssh:"):
        # Addresses stay out of the tables: an IP becomes ip-*.<last octet>
        return re.sub(r"\d+\.\d+\.\d+\.(\d+)", r"ip-*.\1", token.split(">")[0])
    if token.startswith(("docker", "curl:", "script:", "skill:", "agent:", "npm:run", "npx", "psql",
                         "kubectl", "systemctl", "vercel", "supabase")):
        return token
    if token.startswith("python:") and token.endswith(".py"):
        return token
    if token in KEEP_GIT:
        return token
    return None


def coarse_sequence(sequence: list[str]) -> list[str]:
    out = []
    for token in sequence:
        c = coarse(token)
        if c and (not out or out[-1] != c):
            out.append(c)
    return out


def ngrams(sequence: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(sequence[i : i + n]) for i in range(len(sequence) - n + 1)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/backfill")
    args = parser.parse_args()

    facts = {r["session_id"]: r for r in map(json.loads, open(os.path.join(args.out, "facts.jsonl")))}
    cards = {}
    cost = 0.0
    for path in glob.glob(os.path.join(args.out, "cards", "*.json")):
        record = json.load(open(path))
        cards[record["session_id"]] = record["card"]
        cost += record.get("cost_usd") or 0
    human = [f for f in facts.values() if f["prompts"]]
    carded = [(facts[s], c) for s, c in cards.items() if s in facts]
    automated = {s for s, c in cards.items() if c.get("automatizada")}
    md = ["# E0: tablas agregadas", ""]

    md += ["## Totales", ""]
    md.append(table(["Medida", "Valor"], [
        ["Sesiones (transcripts)", len(facts)],
        ["Con prompts humanos", len(human)],
        ["Con ficha del extractor", len(cards)],
        ["Automatizadas (según ficha)", len(automated)],
        ["Rango", f"{min(f['started_at'] for f in facts.values())[:10]} a "
                  f"{max(f['ended_at'] for f in facts.values())[:10]}"],
        ["Horas activas", round(sum(f["active_minutes"] for f in facts.values()) / 60, 1)],
        ["Prompts", sum(f["prompts"] for f in facts.values())],
        ["Llamadas a herramientas", sum(f["tool_calls"] for f in facts.values())],
        ["Errores de herramientas", sum(f["tool_errors"] for f in facts.values())],
        ["Compactaciones", sum(f["compactions"] for f in facts.values())],
        ["Sesiones con compactación", sum(1 for f in facts.values() if f["compactions"])],
        ["Interrupciones", sum(f["interrupts"] for f in facts.values())],
        ["Correcciones (regex)", sum(f["corrections"] for f in facts.values())],
        ["Permisos denegados", sum(f["denials"] for f in facts.values())],
        ["Tokens de salida (millones)", round(sum(f["output_tokens"] for f in facts.values()) / 1e6, 1)],
        ["Costo del extractor (USD)", round(cost, 2)],
    ]))

    md += ["", "## Por repo", ""]
    by_repo = defaultdict(list)
    for f in facts.values():
        by_repo[f["repo"]].append(f)
    md.append(table(["Repo", "Sesiones", "Horas", "Compactaciones", "Errores"], [
        [repo, len(rows), round(sum(r["active_minutes"] for r in rows) / 60, 1),
         sum(r["compactions"] for r in rows), sum(r["tool_errors"] for r in rows)]
        for repo, rows in sorted(by_repo.items(), key=lambda kv: -len(kv[1]))
    ]))

    md += ["", "## Por tipo de trabajo (sesiones humanas con ficha)", ""]
    by_work = defaultdict(list)
    for f, c in carded:
        if f["session_id"] not in automated:
            by_work[c.get("trabajo", "?")].append((f, c))
    rows = []
    for work, items in sorted(by_work.items(), key=lambda kv: -len(kv[1])):
        minutes = [f["active_minutes"] for f, _ in items]
        results = Counter(c.get("resultado") for _, c in items)
        repos = Counter(f["repo"] for f, _ in items)
        rows.append([
            work, len(items), round(sum(minutes) / 60, 1), round(statistics.median(minutes)),
            sum(f["compactions"] for f, _ in items),
            f"{results.get('resuelto', 0)}/{results.get('parcial', 0)}/{results.get('abandonado', 0)}",
            ", ".join(f"{r} {n}" for r, n in repos.most_common(3)),
        ])
    headers = ["Trabajo", "Sesiones", "Horas", "Min. mediana", "Compact.", "Res/Parc/Aband", "Repos"]
    md.append(table(headers, rows))

    md += ["", "## Fricciones", ""]
    friction = Counter()
    friction_work = defaultdict(Counter)
    for _, c in carded:
        for fr in c.get("fricciones") or []:
            if fr != "ninguna":
                friction[fr] += 1
                friction_work[fr][c.get("trabajo")] += 1
    md.append(table(["Fricción", "Sesiones", "Trabajos donde más aparece"], [
        [fr, n, ", ".join(f"{w} {k}" for w, k in friction_work[fr].most_common(3))]
        for fr, n in friction.most_common()
    ]))

    md += ["", "## Qué podría ayudar, por tipo", ""]
    helps = Counter()
    for _, c in carded:
        for h in c.get("podria_ayudar") or []:
            if h.get("tipo") and h["tipo"] != "nada":
                helps[h["tipo"]] += 1
    md.append(table(["Tipo", "Menciones"], helps.most_common()))

    md += ["", "## Uso de capacidades (sesiones humanas)", ""]
    n_human = len(human)

    def sessions_with(pred) -> int:
        return sum(1 for f in human if pred(f))

    rows = [[name, sessions_with(lambda f, t=tools: any(f["tools"].get(x) for x in t))]
            for name, tools in FEATURES.items()]
    md.append(table(["Función", f"Sesiones (de {n_human})"], rows))
    for key, title in (("skills", "Skills"), ("subagents", "Subagentes"), ("mcp_servers", "Servidores MCP"),
                       ("slash_commands", "Slash commands")):
        usage = Counter()
        reach = Counter()
        for f in human:
            for name, n in f[key].items():
                usage[name] += n
                reach[name] += 1
        md += ["", f"### {title}", ""]
        md.append(table(["Nombre", "Usos", "Sesiones"], [[k, v, reach[k]] for k, v in usage.most_common(20)]))

    md += ["", "## Secuencias de acciones repetidas (3+ sesiones)", ""]
    support = Counter()
    sessions_of = defaultdict(set)
    for f in human:
        seq = coarse_sequence(f["sequence"])
        for n in (2, 3, 4, 5):
            for gram in ngrams(seq, n):
                if len(set(gram)) == n:
                    support[gram] += 1
                    sessions_of[gram].add(f["repo"])
    frequent = [(g, s) for g, s in support.items() if s >= 3]
    # Drop a gram when a longer one containing it has the same support
    kept = []
    for gram, s in frequent:
        joined = " → ".join(gram)
        if not any(len(o) > len(gram) and so == s and joined in " → ".join(o) for o, so in frequent):
            kept.append((gram, s))
    kept.sort(key=lambda gs: (-gs[1], -len(gs[0])))
    md.append(table(["Secuencia", "Sesiones", "Repos"], [
        [" → ".join(g), s, ", ".join(sorted(sessions_of[g]))] for g, s in kept[:40]
    ]))

    md += ["", "## Unidades candidatas", ""]
    units = []
    for f, c in carded:
        for u in c.get("unidades") or []:
            title = u.get("title", "")
            units.append({
                "id": "ku_" + hashlib.sha1(f"{f['session_id']}{title}".encode()).hexdigest()[:10],
                "session_id": f["session_id"], "date": (f["started_at"] or "")[:10], "repo": f["repo"],
                "trabajo": c.get("trabajo"), **u,
            })
    # Near-duplicate groups by title and keyword overlap, to merge during review
    group = list(range(len(units)))
    signature = [words(u.get("title", "") + " " + " ".join((u.get("triggers") or {}).get("keywords", [])))
                 for u in units]
    for i in range(len(units)):
        for j in range(i):
            a, b = signature[i], signature[j]
            if a and b and len(a & b) / len(a | b) >= 0.35:
                group[i] = group[j]
                break
    for u, g in zip(units, group, strict=True):
        u["group"] = units[g]["id"]
    with open(os.path.join(args.out, "units.jsonl"), "w") as out:
        for u in units:
            out.write(json.dumps(u, ensure_ascii=False) + "\n")
    md.append(table(["Tipo", "Unidades"], Counter(u.get("type") for u in units).most_common()))
    md += [""]
    md.append(table(["Destino", "Unidades"], Counter(u.get("destino") for u in units).most_common()))
    md += [""]
    md.append(table(["Confianza", "Unidades"], Counter(u.get("confidence") for u in units).most_common()))
    md += ["", f"Grupos de casi duplicados con 2+ unidades: "
               f"{sum(1 for n in Counter(group).values() if n > 1)}"]

    with open(os.path.join(args.out, "summary.md"), "w") as out:
        out.write("\n".join(md) + "\n")

    ideas = ["# E0: ideas, pasos repetidos y correcciones por trabajo", ""]
    for work, items in sorted(by_work.items(), key=lambda kv: -len(kv[1])):
        ideas += [f"## {work} ({len(items)})", ""]
        for f, c in items:
            label = f"{f['started_at'][:10]} {f['repo']} · {f['title'] or c.get('objetivo', '')}"[:120]
            helps = "; ".join(f"{h.get('tipo')}: {h.get('que')}" for h in c.get("podria_ayudar") or []
                              if h.get("tipo") != "nada")
            steps = "; ".join(c.get("pasos_repetidos") or [])
            corrections = "; ".join(c.get("correcciones") or [])
            ideas.append(f"- **{label}** ({round(f['active_minutes'])} min)")
            if helps:
                ideas.append(f"  - ayuda: {helps}")
            if steps:
                ideas.append(f"  - repite: {steps}")
            if corrections:
                ideas.append(f"  - corrige: {corrections}")
            if c.get("friccion_detalle"):
                ideas.append(f"  - fricción: {c['friccion_detalle']}")
        ideas.append("")
    with open(os.path.join(args.out, "ideas.md"), "w") as out:
        out.write("\n".join(ideas) + "\n")
    print(f"{len(cards)} cards, {len(units)} units -> {args.out}/summary.md, ideas.md, units.jsonl")


if __name__ == "__main__":
    main()
