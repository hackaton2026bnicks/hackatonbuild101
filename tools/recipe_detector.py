"""Detecta lo que una persona repite y lo manda a la bóveda como receta (docs/PLAN-EQUIPO.md, objetivo 5).

Lee las fichas de trabajo (cards*.jsonl, una por sesión, con su `intencion`) y los hechos
(facts*.jsonl) de ~/.claude/session-insights. Agrupa las sesiones cuya intención se parece (mismas
palabras de fondo) y, cuando un grupo llega a 3 sesiones, arma una receta: qué se repite, la sesión
que mejor salió y con qué, los pasos que se hacen a mano y la skill si ya hay una. La deja en la
cola del archivista (POST /api/lessons, fuente "patrones de uso"), que la guarda en la bóveda; desde
ahí el hook del pedido la sugiere la próxima vez, a esta persona o a otra.

Recuerda qué grupos ya mandó (recetas_enviadas.json), así un grupo que sigue creciendo no se manda
de nuevo. Solo biblioteca estándar, Python 3.9+.

Uso:      python3 tools/recipe_detector.py [--dry-run] [--min 3]
Entorno:  BUILD101_KEY, BUILD101_URL, BUILD101_INSIGHTS_DIR, BUILD101_INSIGHTS_USER
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request

from insights_paths import local_path

INSIGHTS_DIR = local_path(os.environ.get("BUILD101_INSIGHTS_DIR", "~/.claude/session-insights"))
DEFAULT_URL = "https://54-89-124-87.sslip.io/api/hooks/claude-code"
HOOK_PATH = "/api/hooks/claude-code"
FUENTE = "patrones de uso"
MIN_SESSIONS = 3
SIMILAR = 0.5  # Jaccard de las palabras de fondo de dos intenciones
STOP = set(
    "a al como con de del el en es la las lo los mi mis para por que se sin su sus un una uno y o "
    "the of to and for in on".split()
)


def words(text: str) -> frozenset[str]:
    """Palabras de fondo: sin acentos, palabras vacías ni plural, así 'deploys' agrupa con 'deploy'."""
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    out = set()
    for word in re.findall(r"[a-z0-9]+", plain):
        if word in STOP or len(word) < 3:
            continue
        out.add(word[:-1] if word.endswith("s") and len(word) > 4 else word)
    return frozenset(out)


def similar(a: frozenset, b: frozenset) -> bool:
    return bool(a and b) and len(a & b) / len(a | b) >= SIMILAR


def groups(cards: list[dict]) -> list[list[dict]]:
    """Une las sesiones de intención parecida (enlace simple: si A se parece a B y B a C, van juntas)."""
    keyed = [(words(c.get("intencion") or ""), c) for c in cards]
    parent = list(range(len(keyed)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(keyed)):
        for j in range(i + 1, len(keyed)):
            if similar(keyed[i][0], keyed[j][0]):
                parent[root(i)] = root(j)
    out = collections.defaultdict(list)
    for i, (_, card) in enumerate(keyed):
        out[root(i)].append(card)
    return sorted(out.values(), key=len, reverse=True)


def friction(fact: dict) -> float:
    """El F de docs/DIK-INSIGHTS.md: errores, correcciones humanas y contexto perdido cada 100 acciones."""
    acts = fact.get("tool_calls", 0) + fact.get("subagent_tool_calls", 0)
    human = fact.get("interrupts", 0) + fact.get("denials", 0) + fact.get("corrections", 0)
    points = fact.get("tool_errors", 0) + 3 * human + 10 * fact.get("compactions", 0)
    return 100 * points / max(10, acts)


def best(group: list[dict], facts: dict) -> dict:
    """La sesión que mejor salió: resuelta primero, después la de menos fricción."""
    order = {"resuelto": 0, "parcial": 1, "en-curso": 2, "abandonado": 3}
    return min(
        group,
        key=lambda c: (order.get(c.get("resultado"), 4), friction(facts.get(c["session_id"], {}))),
    )


def most_common(values) -> str | None:
    counted = collections.Counter(v for v in values if v)
    return counted.most_common(1)[0][0] if counted else None


def recipe(group: list[dict], facts: dict, persona: str) -> dict:
    intents = [c.get("intencion") or "" for c in group]
    title = min(collections.Counter(intents).most_common(), key=lambda kv: (-kv[1], len(kv[0])))[0]
    hours = sum(facts.get(c["session_id"], {}).get("active_min", 0) for c in group) / 60
    top = best(group, facts)
    top_fact = facts.get(top["session_id"], {})
    used = [u for c in group for u in c.get("capital_usado") or []]
    skill = most_common(u.split(":", 1)[1] for u in used if u.startswith("skill:"))
    steps = [
        s
        for s, _ in collections.Counter(s for c in group for s in c.get("pasos_repetidos") or []).most_common(
            3
        )
    ]
    keywords = collections.Counter(w for i in intents for w in words(i))
    # El autor de la nota lo pone el server desde la key; acá el nombre es solo para leer, y sin
    # uno real (en Windows USER no existe y llega "me") la frase va sin nombre
    who = f"{persona} repite esto" if persona and persona != "me" else "Esto se repite"
    text = [
        f"{who} en {len(group)} sesiones ({hours:.1f} h activas, trabajo "
        f"{most_common(c.get('trabajo') for c in group)}): " + "; ".join(sorted(set(intents))) + ".",
        f"La que mejor salió ({(top_fact.get('start') or '')[:10]}, {top.get('resultado')}, "
        f"F {friction(top_fact):.0f}) usó: "
        + (", ".join(top.get("capital_usado") or []) or "nada reusable")
        + ".",
    ]
    if steps:
        text.append("Pasos que se hacen a mano cada vez: " + "; ".join(steps) + ".")
    ideas = [
        p["que"] for c in group for p in c.get("podria_ayudar") or [] if p.get("tipo") not in (None, "nada")
    ]
    if ideas:
        text.append("Lo que ayudaría, según las fichas: " + "; ".join(sorted(set(ideas))[:3]) + ".")
    text.append(
        "Guardala como receta con la forma que mejor funcionó, para sugerirla cuando se vuelva a pedir."
    )
    lesson = {
        "tipo": "receta",
        "titulo": title[:200],
        "texto": " ".join(text)[:4000],
        "aplica_cuando": " / ".join(w for w, _ in keywords.most_common(6))[:300],
        "fuente": FUENTE,
    }
    if skill:
        lesson["skill"] = skill[:128]
    return lesson


def load(pattern: str) -> dict:
    """Última línea por session_id: si una sesión se retoma y vuelve a cerrar, manda la última medición."""
    rows = {}
    for path in sorted(glob.glob(local_path(pattern)), key=os.path.getmtime):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("session_id"):
                    rows[row["session_id"]] = row
    return rows


def pending(found: list[list[dict]], sent: list[list[str]]) -> list[list[dict]]:
    """Grupos nuevos: un grupo que comparte la mitad o más de sus sesiones con uno ya mandado es el mismo."""
    out = []
    for group in found:
        ids = {c["session_id"] for c in group}
        if not any(len(ids & set(old)) * 2 >= len(ids) for old in sent):
            out.append(group)
    return out


def api_base(environ: dict) -> str:
    url = environ.get("BUILD101_URL", DEFAULT_URL).rstrip("/")
    return url[: -len(HOOK_PATH)] if url.endswith(HOOK_PATH) else url


def post(base: str, key: str, lesson: dict, urlopen=urllib.request.urlopen) -> None:
    request = urllib.request.Request(
        base + "/api/lessons",
        data=json.dumps(lesson, ensure_ascii=False).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=15):
        pass


def run(
    folder: str, persona: str, environ: dict, minimum: int = MIN_SESSIONS, dry_run: bool = False, log=print
) -> int:
    """Una pasada. Devuelve cuántas recetas mandó (o mandaría, con dry_run)."""
    cards = list(load(os.path.join(folder, "cards*.jsonl")).values())
    facts = load(os.path.join(folder, "facts*.jsonl"))
    state_path = os.path.join(folder, "recetas_enviadas.json")
    try:
        with open(state_path, encoding="utf-8") as fh:
            sent = json.load(fh)
    except (OSError, ValueError):
        sent = []
    found = [g for g in groups(cards) if len(g) >= minimum]
    todo = pending(found, sent)
    if dry_run:
        for group in todo:
            log(json.dumps(recipe(group, facts, persona), ensure_ascii=False, indent=1))
        return len(todo)
    key = environ.get("BUILD101_KEY")
    if not key:
        log("Falta BUILD101_KEY")
        return 0
    done = 0
    for group in todo:
        try:
            post(api_base(environ), key, recipe(group, facts, persona))
        except (urllib.error.URLError, OSError, ValueError) as error:
            log(f"no pude mandar la receta: {error}")
            continue
        sent.append(sorted(c["session_id"] for c in group))
        done += 1
    if done:
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(sent, fh)
    return done


def default_persona(environ: dict) -> str:
    for name in ("BUILD101_INSIGHTS_USER", "B101_NAME", "USER", "USERNAME"):
        if environ.get(name):
            return environ[name]
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", default=INSIGHTS_DIR)
    ap.add_argument("--persona", default=default_persona(os.environ))
    ap.add_argument(
        "--min", type=int, default=MIN_SESSIONS, help="sesiones para que algo cuente como repetido"
    )
    ap.add_argument("--dry-run", action="store_true", help="muestra las recetas sin mandarlas")
    a = ap.parse_args(argv)
    n = run(local_path(a.dir), a.persona, dict(os.environ), a.min, a.dry_run)
    print(f"{n} recetas {'para mandar' if a.dry_run else 'en la cola del archivista'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
