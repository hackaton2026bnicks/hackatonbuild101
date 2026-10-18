#!/usr/bin/env python3
"""Resumen semanal de DIK v2: una semana contra la mediana de las 4 anteriores, en markdown corto.

Los seis números con su delta, las 3 oportunidades con más O, 3 sesiones ejemplares y lo que apareció
esta semana y no estaba en la línea de base. Lee los facts.jsonl y cards.jsonl que deja el hook SessionEnd.

    python3 tools/weekly_report.py                      # semana actual
    python3 tools/weekly_report.py --week anterior      # la última semana cerrada
    python3 tools/weekly_report.py --week 2026-W41 --facts 'equipo/*.jsonl' --cards 'equipo/cards*.jsonl'

Escribe ~/.claude/session-insights/weekly/<semana>.md y lo imprime. Sin dependencias.
"""

from __future__ import annotations

import argparse
import collections
import os
from datetime import datetime, timedelta

import session_insights as si
from work_card import INSIGHTS_DIR, latest

BASE_WEEKS = 4
# (clave en scorecard, letra, nombre, 1 si subir es mejor, -1 si bajar es mejor, 0 si no se juzga)
NUMBERS = [
    ("horas_activas", "H", "Horas activas", 0, 1),
    ("L_apalancamiento", "L", "Apalancamiento", 1, 1),
    ("K_capitalizacion", "K", "Capitalización", 1, 2),
    ("F_friccion", "F", "Fricción", -1, 1),
    ("R_resueltas", "R", "Resueltas", 1, 2),
    ("C_mtokens_por_sesion", "C", "M tokens por sesión", -1, 1),
    ("compactaciones_por_h", "C", "Compactaciones por hora", -1, 2),
]


def num(x, nd=1) -> str:
    return "–" if x is None else f"{x:,.{nd}f}".replace(",", " ").replace(".", ",")


def week_label(when: datetime) -> str:
    return when.strftime("%G-W%V")


def monday(label: str) -> datetime:
    return datetime.strptime(label + "-1", "%G-W%V-%u").replace(tzinfo=si.TZ)


def resolve_week(arg: str, now: datetime) -> str:
    if arg in ("actual", None):
        return week_label(now)
    if arg == "anterior":
        return week_label(now - timedelta(days=7))
    return arg


def grams(r: dict, n_range=(3, 5)) -> set:
    """Las mismas secuencias de acciones de dominio que cuenta session_insights.ngram_patterns."""
    seq = [si.family(t) for t in r["sequence"] if si.tier(t) != "base"]
    col = [t for i, t in enumerate(seq) if i == 0 or seq[i - 1] != t]
    return {tuple(col[i : i + n]) for n in range(n_range[0], n_range[1] + 1) for i in range(len(col) - n + 1)}


def delta(cur, base, better: int, nd: int) -> str:
    if cur is None or base is None:
        return "–"
    d = cur - base
    pct = f" ({100 * d / base:+.0f} %)".replace("-", "−") if base else ""
    mark = ""
    if better and base and abs(d / base) >= 0.1:
        mark = " · mejor" if d * better > 0 else " · peor"
    return f"{'+' if d >= 0 else '−'}{num(abs(d), nd)}{pct}{mark}"


def new_patterns(cur: list[dict], base: list[dict], cards: dict, min_support: int) -> list[str]:
    """Lo que esta semana se repite y en la línea de base no aparece ni una vez."""
    out = []
    base_grams = set().union(*(grams(r) for r in base)) if base else set()
    for p in si.ngram_patterns(cur, min_support=min_support):
        if tuple(p["secuencia"]) not in base_grams and p["estado"] == "candidato":
            out.append(f"Secuencia nueva en {p['sesiones']} sesiones: `{' → '.join(p['secuencia'])}`")
        if len(out) >= 2:
            break

    def families(rs, level):
        seen = collections.defaultdict(set)
        for r in rs:
            for t in r["sequence"]:
                if si.tier(t) == level:
                    seen[si.family(t)].add(r["session_id"])
        return seen

    base_adhoc, base_capital = families(base, "adhoc"), families(base, "capital")
    adhoc = [(f, len(s)) for f, s in families(cur, "adhoc").items() if f not in base_adhoc and len(s) >= 2]
    for f, n in sorted(adhoc, key=lambda x: -x[1])[:2]:
        out.append(f"Herramienta ad hoc nueva en {n} sesiones: `{f}` (candidata a tool o MCP si sigue)")
    capital = [(f, len(s)) for f, s in families(cur, "capital").items() if f not in base_capital]
    if capital:
        names = ", ".join(f"`{f}` ({n})" for f, n in sorted(capital, key=lambda x: -x[1])[:5])
        out.append(f"Capital usado por primera vez: {names}")

    ids = {r["session_id"] for r in cur}
    created = collections.Counter(
        x for s, c in cards.items() if s in ids for x in c.get("capital_creado") or []
    )
    if created:
        out.append("Capital creado: " + ", ".join(f"`{x}`" for x, _ in created.most_common(5)))
    base_ids = {r["session_id"] for r in base}
    base_intents = {(c.get("intencion") or "").lower() for s, c in cards.items() if s in base_ids}
    intents = collections.Counter(
        (c.get("intencion") or "").lower() for s, c in cards.items() if s in ids and c.get("intencion")
    )
    for intent, n in intents.most_common():
        if n >= 2 and intent not in base_intents:
            out.append(f"Intención nueva repetida en {n} sesiones: «{intent}»")
            break
    return out


def report(rows: list[dict], cards: dict, target: str, min_support: int = 3) -> str:
    for r in rows:
        r["_m"] = si.session_metrics(r)
    start = monday(target)
    base_labels = [week_label(start - timedelta(days=7 * i)) for i in range(BASE_WEEKS, 0, -1)]
    by_week = collections.defaultdict(list)
    for r in rows:
        by_week[si.week(r)].append(r)
    cur = by_week.get(target, [])
    base_rows = [r for w in base_labels for r in by_week.get(w, [])]
    lines = [f"# Semana {target} · uso de Claude", ""]
    if not cur:
        return "\n".join(lines + ["Sin sesiones medidas esta semana."]) + "\n"

    card_now = si.scorecard(cur, cards)
    weekly = {w: si.scorecard(by_week[w], cards) for w in base_labels if by_week.get(w)}
    people = sorted({r.get("user", "?") for r in cur})
    human = sum(1 for r in cur if r["_m"]["humana"])
    with_card = sum(1 for r in cur if r["session_id"] in cards)
    lines += [
        f"{len(cur)} sesiones ({human} con persona, {with_card} con ficha) de {', '.join(people)}. "
        f"Línea de base: mediana de {len(weekly)} de las semanas {base_labels[0]} a {base_labels[-1]}.",
        "",
        "| | Número | Semana | Base | Δ |",
        "|---|---|---|---|---|",
    ]
    for key, letter, name, better, nd in NUMBERS:
        vals = [weekly[w][key] for w in weekly if weekly[w].get(key) is not None]
        base = si.med(vals) if vals else None
        lines.append(
            f"| **{letter}** | {name} | {num(card_now.get(key), nd)} | {num(base, nd)} "
            f"| {delta(card_now.get(key), base, better, nd)} |"
        )
    if not weekly:
        lines.append("\nSin semanas anteriores con datos: los deltas aparecen cuando haya historia.")

    lines += ["", "## Top 3 oportunidades", ""]
    month = [
        r
        for r in rows
        if start + timedelta(days=7) - timedelta(days=30)
        <= datetime.fromisoformat(r["start"])
        < start + timedelta(days=7)
    ]
    opps = si.opportunities(month, cards)[:3] if cards else []
    for i, o in enumerate(opps, 1):
        idea = o["ideas"][0] if o["ideas"] else ""
        lines.append(
            f"{i}. **{o['tipo']}** para {o['trabajo']} / {o['cliente']} · O = {num(o['O'], 0)} · "
            f"{o['sesiones']} sesiones en 30 días, {num(o['min_mediana'])} min medianos. {idea}"
        )
    if not opps:
        lines.append(
            "Ningún patrón llega a 3 sesiones con ficha en los últimos 30 días."
            + ("" if cards else " Faltan fichas: `python3 tools/work_card.py --missing`.")
        )

    lines += ["", "## 3 sesiones ejemplares", ""]
    ids = {r["session_id"] for r in cur}
    best = [
        s
        for s in si.stars(cur + base_rows, cards, limit=None)
        if s["session_id"] in ids and (cards.get(s["session_id"]) or {}).get("resultado") == "resuelto"
    ][:3]
    for s in best:
        title = s["titulo"] or s["session_id"][:8]
        why = f" {s['por_que']}" if s["por_que"] else ""
        lines.append(
            f"- **{title}** ({s['trabajo']}, {s['user']}) · S = {num(s['puntaje'], 1)} · "
            f"L {num(s['L'])}, K {num(s['K'], 2)}, F {num(s['F'])}.{why}"
        )
    if not best:
        lines.append("Ninguna sesión resuelta con ficha para comparar dentro de su trabajo.")

    lines += ["", "## Patrones nuevos", ""]
    found = new_patterns(cur, base_rows, cards, min_support)
    lines += [f"- {x}" for x in found] or ["Nada nuevo que se repita contra la línea de base."]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", nargs="+", default=[os.path.join(INSIGHTS_DIR, "facts*.jsonl")])
    ap.add_argument("--cards", nargs="*", default=[os.path.join(INSIGHTS_DIR, "cards*.jsonl")])
    ap.add_argument("--week", default="actual", help="actual, anterior o YYYY-Www")
    ap.add_argument(
        "--min-support", type=int, default=3, help="sesiones para contar una secuencia como patrón"
    )
    ap.add_argument("--out", help="por defecto ~/.claude/session-insights/weekly/<semana>.md")
    a = ap.parse_args()

    rows = [r for r in latest(a.facts).values() if r.get("start")]
    cards = latest(a.cards)
    target = resolve_week(a.week, datetime.now(si.TZ))
    text = report(rows, cards, target, a.min_support)
    out = a.out or os.path.join(INSIGHTS_DIR, "weekly", f"{target}.md")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as fh:
        fh.write(text)
    print(text + f"\n-> {out}")


if __name__ == "__main__":
    main()
