#!/usr/bin/env python3
"""Capa B de DIK v2: la ficha de trabajo de una sesión, con tools/work_card_prompt.md y `claude -p`.

Toma los hechos de tools/session_facts.py, arma el mismo resumen que los lotes de la línea de base
(tools/session_digest.py) y le pide la ficha a un modelo chico.
El hook SessionEnd lo usa para una sesión; a mano sirve para ponerse al día con las que no tienen ficha.

    python3 tools/work_card.py --session <id>
    python3 tools/work_card.py --missing --since 2026-10-01

Lee y agrega en ~/.claude/session-insights (facts.jsonl, cards.jsonl). Sin dependencias.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import tempfile
import time

from session_digest import digest

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT = os.path.join(HERE, "work_card_prompt.md")
INSIGHTS_DIR = os.path.expanduser(os.environ.get("BUILD101_INSIGHTS_DIR", "~/.claude/session-insights"))
MODEL = os.environ.get("BUILD101_CARD_MODEL", "haiku")
TIMEOUT = 180


def clean_env() -> dict:
    """Sin las variables del proxy y la auth de la app de escritorio el CLI usa su propio login. Sin la clave
    de build101 y con la marca, ningún hook de esta corrida manda nada ni vuelve a pedir una ficha."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k == "CLAUDE_CODE_OAUTH_TOKEN"
        or not k.startswith(
            ("CLAUDE_CODE_", "CLAUDECODE", "CLAUDE_AGENT_SDK_", "ANTHROPIC_BASE_URL", "BUILD101_KEY")
        )
    }
    env["BUILD101_INSIGHTS_CHILD"] = "1"
    env["DIK_EVALUATOR"] = "1"
    return env


def parse_card(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("la salida no trae un objeto JSON")
    return json.loads(text[start : end + 1])


def make_card(r: dict, model: str = MODEL, run=subprocess.run) -> dict:
    """Pide la ficha y la devuelve con `_meta` (modelo, segundos, costo). Levanta si falla dos veces."""
    cmd = [
        "claude",
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        "--no-session-persistence",  # la corrida no deja transcript que después se mida
        "--setting-sources",
        "project",  # sin hooks del usuario
        "--strict-mcp-config",
        "--tools",
        "",
        "--system-prompt",
        open(PROMPT).read(),
    ]
    data = "<sesion>\n" + json.dumps(digest(r), ensure_ascii=False) + "\n</sesion>"
    error = ""
    # Carpeta vacía: no levanta CLAUDE.md ni settings de ningún repo
    with tempfile.TemporaryDirectory() as workdir:
        for _ in range(2):
            started = time.time()
            try:
                out = run(
                    cmd,
                    input=data,
                    capture_output=True,
                    text=True,
                    cwd=workdir,
                    env=clean_env(),
                    timeout=TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                error = "timeout"
                continue
            if out.returncode != 0:
                error = (out.stderr or out.stdout).strip()[-300:]
                continue
            try:
                envelope = json.loads(out.stdout)
                card = parse_card(envelope.get("result", ""))
            except ValueError as e:
                error = f"parse: {e}"
                continue
            card["session_id"] = r["session_id"]
            card["_meta"] = {
                "model": model,
                "seconds": round(time.time() - started, 1),
                "cost_usd": envelope.get("total_cost_usd"),
                "user": r.get("user"),
            }
            return card
    raise RuntimeError(error or "sin salida")


def append_line(path: str, row: dict) -> None:
    """Una sola escritura por línea, para que dos sesiones que cierran juntas no se mezclen."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def latest(patterns) -> dict:
    """Última línea por session_id, leyendo los archivos del más viejo al más nuevo: si una sesión se retoma y
    vuelve a cerrar, o la línea de base se rehace, manda la medición más reciente."""
    rows = {}
    for pat in patterns:
        for p in sorted(glob.glob(os.path.expanduser(pat)), key=os.path.getmtime):
            with open(p) as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("session_id"):
                        rows[row["session_id"]] = row
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", action="append", default=[])
    ap.add_argument(
        "--missing", action="store_true", help="todas las sesiones de --facts que no tienen ficha"
    )
    ap.add_argument("--since", help="YYYY-MM-DD, con --missing")
    ap.add_argument("--facts", default=os.path.join(INSIGHTS_DIR, "facts*.jsonl"))
    ap.add_argument("--cards", default=os.path.join(INSIGHTS_DIR, "cards*.jsonl"))
    ap.add_argument("--out", default=os.path.join(INSIGHTS_DIR, "cards.jsonl"))
    ap.add_argument("--model", default=MODEL)
    a = ap.parse_args()

    facts = latest([a.facts])
    ids = list(a.session)
    if a.missing:
        have = latest([a.cards])
        ids += [
            s
            for s, r in sorted(facts.items(), key=lambda x: x[1]["start"])
            if s not in have and (not a.since or r["start"][:10] >= a.since)
        ]
    for sid in ids:
        if sid not in facts:
            print(f"{sid}: sin hechos en {a.facts}")
            continue
        try:
            card = make_card(facts[sid], a.model)
        except RuntimeError as e:
            print(f"{sid}: {e}")
            continue
        append_line(a.out, card)
        print(f"{sid}: {card.get('trabajo')} · {card.get('resultado')} · {card.get('intencion')}")


if __name__ == "__main__":
    main()
