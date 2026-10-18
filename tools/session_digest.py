#!/usr/bin/env python3
"""Arma los lotes que lee el clasificador de fichas (capa B), a partir de facts.jsonl.

Cada línea es una sesión resumida: título, repo, minutos, herramientas de dominio,
contadores de fricción, salidas y los prompts de la persona recortados. Los lotes se
pasan a tools/work_card_prompt.md (un subagente o un `claude -p` por lote).

    python3 tools/session_digest.py --facts ~/.claude/session-insights/facts.jsonl \
        --out-dir ~/.claude/session-insights --size 45
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re

from insights_paths import local_path

GENERIC = re.compile(
    r"^(read\(|edit\(|write\(|bash:(grep|sed|cat|ls|head|tail|wc|find|for|mkdir|echo|awk|cp|rm|mv|sleep|date|printf|shell)|ToolSearch|Task|mcp:ccd_)"
)


def digest(r: dict) -> dict:
    toks = collections.Counter(
        re.sub(r"^mcp:(Claude_Browser|claude-in-chrome)\..*", "navegador", t)
        for t in r["sequence"]
        if not GENERIC.match(t)
    )
    p = r.get("prompts") or []
    prompts = []
    if p:
        prompts.append(p[0][:600])
        prompts += [x[:220] for x in p[1:9]]
        if len(p) > 9:
            prompts.append("... ultimo: " + p[-1][:220])
    return {
        "session_id": r["session_id"],
        "fecha": r["start"][:10],
        "repo": r["repo"],
        "titulo": r.get("title") or r.get("ai_title"),
        "min_activos": r["active_min"],
        "prompts_persona": r["human_prompts"],
        "acciones": r["tool_calls"] + r.get("subagent_tool_calls", 0),
        "herramientas_top": dict(toks.most_common(10)),
        "skills": r["skills"],
        "subagentes": r["subagents"],
        "friccion": {
            "errores": r["tool_errors"],
            "interrupciones": r["interrupts"],
            "rechazos": r["denials"],
            "correcciones_heuristica": r["corrections"],
            "compactaciones": r["compactions"],
        },
        "salidas": {
            "archivos_editados": r["files_edited"],
            "commits": r["commits"],
            "prs": r["prs"] + r["pr_links"],
            "artifacts": r["artifacts"],
        },
        "tarea_programada": r.get("scheduled_task"),
        "lanzada_por_otra_sesion": r.get("spawned", False),
        "mensajes_de_otras_sesiones": r.get("peer_msgs", 0),
        "prompts": prompts,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--facts", required=True, help="facts.jsonl SIN --share (el clasificador necesita los prompts)"
    )
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--size", type=int, default=45, help="sesiones por lote")
    ap.add_argument("--skip", help="cards ya hechas (glob separado por comas): saltea esas sesiones")
    a = ap.parse_args()

    done = set()
    if a.skip:
        import glob

        for pat in a.skip.split(","):
            for path in glob.glob(local_path(pat)):
                with open(path, encoding="utf-8") as fh:
                    done |= {json.loads(line)["session_id"] for line in fh if line.strip()}

    with open(local_path(a.facts), encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    if rows and rows[0].get("prompts") is None:
        raise SystemExit("facts.jsonl está en modo --share: generalo sin --share para armar los lotes")
    rows = sorted((r for r in rows if r["session_id"] not in done), key=lambda r: r["start"])

    out = local_path(a.out_dir)
    os.makedirs(out, exist_ok=True)
    n = 0
    for i in range(0, len(rows), a.size):
        path = os.path.join(out, f"batch_{i // a.size}.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for r in rows[i : i + a.size]:
                fh.write(json.dumps(digest(r), ensure_ascii=False) + "\n")
        n += 1
        print(path, len(rows[i : i + a.size]))
    print(f"{len(rows)} sesiones en {n} lotes")


if __name__ == "__main__":
    main()
