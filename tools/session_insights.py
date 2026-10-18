#!/usr/bin/env python3
"""Indicadores, detectores y puntajes sobre las sesiones de Claude (DIK v2, capas A + B).

Entrada: uno o más facts.jsonl de tools/session_facts.py (una persona o todo el equipo) y,
opcional, fichas de trabajo (cards*.jsonl) hechas con tools/work_card_prompt.md.
Salida: insights.json (todo lo calculado) y un resumen en texto por stdout.

    python3 tools/session_insights.py --facts facts.jsonl --cards 'cards_*.jsonl' --out insights.json
    python3 tools/session_insights.py --facts 'equipo/*.jsonl' --since 2026-09-01 --baseline-weeks 4

La misma corrida sirve hacia atrás (línea de base con toda la historia) y hacia adelante
(semana actual contra la mediana de las 4 semanas anteriores).
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import statistics as st
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=-3))

# --- Clasificación de cada acción en tres niveles ------------------------------------------
# capital: un activo reusable que ya trae el conocimiento (skill, MCP de dominio, script del repo,
#          subagente especializado). adhoc: herramienta genérica a la que Claude le pone el
#          conocimiento en el momento (SQL a mano, ssh + docker exec, heredoc de Python, navegador).
#          base: leer, editar, buscar, navegar el repo.
GENERIC_MCP = re.compile(r"^mcp:[\w-]+\.(query|sql|execute_sql|run_query|describe|list_tables)$")
REPO_SCRIPT = re.compile(r"^(python3?|node|bash|sh|npx tsx|uv run) [\w.-]+\.(py|sh|js|ts|mjs)$|^[\w.-]+\.sh$")
REPO_RUNNER = ("npm run", "npm test", "pnpm ", "make", "pytest", "python3 -m", "uv run")
ADHOC_BASH = (
    "ssh{",
    "scp",
    "docker",
    "curl",
    "python:sql",
    "python:datos",
    "python:http",
    "python:otro",
    "psql",
    "kubectl",
    "mysql",
)


def tier(tok: str) -> str:
    if tok.startswith("skill:") or (tok.startswith("agent:") and tok != "agent:general-purpose"):
        return "capital"
    if tok.startswith("mcp:"):
        if tok.startswith(("mcp:ccd_", "mcp:visualize", "mcp:terminal")):
            return "base"
        if GENERIC_MCP.match(tok) or tok.startswith(("mcp:Claude_Browser", "mcp:claude-in-chrome")):
            return "adhoc"
        return "capital"
    if tok.startswith("bash:"):
        b = tok[5:]
        if REPO_SCRIPT.match(b) or b.startswith(REPO_RUNNER):
            return "capital"
        if b.startswith(ADHOC_BASH):
            return "adhoc"
        return "base"
    if tok in ("WebSearch", "WebFetch", "agent:general-purpose"):
        return "adhoc"
    return "base"


def family(tok: str) -> str:
    """Agrupa tokens para contar: todo el navegador junto, una base SQL junta, etc."""
    tok = re.sub(r"^mcp:(Claude_Browser|claude-in-chrome)\..*", "navegador", tok)
    tok = re.sub(r"^(mcp:[\w-]+)\.(query|describe|list_tables)$", r"\1.sql", tok)
    return tok


# --- Indicadores por sesión ------------------------------------------------------------------
# F pondera cada evento por lo que cuesta: un error es un reintento (~1 acción), una corrección,
# interrupción o rechazo es un turno humano perdido (~3), una compactación es contexto perdido (~10).
W_ERR, W_HUMAN, W_COMPACT = 1, 3, 10


def session_metrics(r: dict) -> dict:
    acts = r["tool_calls"] + r.get("subagent_tool_calls", 0)
    tiers = collections.Counter(tier(t) for t in r["sequence"])
    friction_pts = (
        W_ERR * r["tool_errors"]
        + W_HUMAN * (r["interrupts"] + r["denials"] + r["corrections"])
        + W_COMPACT * r["compactions"]
    )
    tokens = sum(r["tokens"].values()) + sum((r.get("subagent_tokens") or {}).values())
    return {
        "acciones": acts,
        "L": acts / max(1, r["human_prompts"]),  # apalancamiento
        "K": tiers["capital"] / max(1, tiers["capital"] + tiers["adhoc"]),  # capitalización
        "F": 100 * friction_pts / max(10, acts),  # fricción cada 100 acciones
        "E": r["tool_errors"] / max(1, r["tool_calls"]),  # tasa de error
        "tokens": tokens,
        "tiers": dict(tiers),
        "humana": r["human_prompts"] > 0 and not r.get("scheduled_task"),
    }


def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else 0.0


def week(r) -> str:
    return datetime.fromisoformat(r["start"]).astimezone(TZ).strftime("%G-W%V")


def scorecard(rs: list[dict], cards: dict) -> dict:
    """Los 6 números que se comparan entre semanas, personas y trabajos."""
    if not rs:
        return {}
    ms = [r["_m"] for r in rs]
    tiers = collections.Counter()
    for m in ms:
        tiers.update(m["tiers"])
    acts = sum(m["acciones"] for m in ms)
    prompts = sum(r["human_prompts"] for r in rs)
    fr = sum(
        W_ERR * r["tool_errors"]
        + W_HUMAN * (r["interrupts"] + r["denials"] + r["corrections"])
        + W_COMPACT * r["compactions"]
        for r in rs
    )
    with_card = [cards[r["session_id"]] for r in rs if r["session_id"] in cards]
    resolved = [c for c in with_card if c.get("resultado") == "resuelto"]
    return {
        "sesiones": len(rs),
        "horas_activas": round(sum(r["active_min"] for r in rs) / 60, 1),
        "L_apalancamiento": round(acts / max(1, prompts), 1),
        "K_capitalizacion": round(tiers["capital"] / max(1, tiers["capital"] + tiers["adhoc"]), 3),
        "F_friccion": round(100 * fr / max(10, acts), 2),
        "R_resueltas": round(len(resolved) / len(with_card), 2) if with_card else None,
        "C_mtokens_por_sesion": round(med([m["tokens"] for m in ms]) / 1e6, 1),
        "compactaciones_por_h": round(
            sum(r["compactions"] for r in rs) / max(0.1, sum(r["active_min"] for r in rs) / 60), 2
        ),
    }


# --- Detectores ---------------------------------------------------------------------------------
def ngram_patterns(rs, n_range=(3, 5), min_support=3):
    """Secuencias de acciones de dominio (sin leer/editar/buscar) que se repiten en 3+ sesiones."""
    sup, cnt = collections.defaultdict(set), collections.Counter()
    for r in rs:
        seq = [family(t) for t in r["sequence"] if tier(t) != "base"]
        col = [t for i, t in enumerate(seq) if i == 0 or seq[i - 1] != t]
        for n in range(n_range[0], n_range[1] + 1):
            for i in range(len(col) - n + 1):
                g = tuple(col[i : i + n])
                if len(set(g)) >= 2:
                    sup[g].add(r["session_id"])
                    cnt[g] += 1
    out = []
    for g, s in sup.items():
        if len(s) < min_support:
            continue
        ts = [tier(t) if not t.startswith("navegador") else "adhoc" for t in g]
        out.append(
            {
                "secuencia": list(g),
                "sesiones": len(s),
                "veces": cnt[g],
                "estado": "capitalizado" if all(x == "capital" for x in ts) else "candidato",
                "session_ids": sorted(s),
            }
        )
    out.sort(key=lambda p: (-p["sesiones"], -len(p["secuencia"])))
    # quedarse con la más larga de cada familia de soporte parecido
    kept = []
    for p in out:
        if any(
            set(p["session_ids"]) <= set(k["session_ids"])
            and " ".join(p["secuencia"]) in " ".join(k["secuencia"])
            for k in kept
        ):
            continue
        kept.append(p)
    return kept[:25]


def adhoc_hotspots(rs, by_id, min_support=3):
    """Acciones ad hoc que se repiten en muchas sesiones: candidatas a tool o MCP curado."""
    sup, cnt, mins, people = (
        collections.Counter(),
        collections.Counter(),
        collections.defaultdict(list),
        collections.defaultdict(set),
    )
    for r in rs:
        seen = set()
        for t in r["sequence"]:
            if tier(t) != "adhoc":
                continue
            f = family(t)
            cnt[f] += 1
            if f not in seen:
                seen.add(f)
                sup[f] += 1
                mins[f].append(r["active_min"])
                people[f].add(r.get("user", "?"))
    out = [
        {
            "accion": f,
            "sesiones": s,
            "veces": cnt[f],
            "personas": len(people[f]),
            "min_mediana_sesion": round(med(mins[f]), 1),
        }
        for f, s in sup.items()
        if s >= min_support
    ]
    return sorted(out, key=lambda x: -x["veces"])[:20]


def error_hotspots(rs):
    """Dónde falla Claude, por herramienta y con tasa.

    Una base con muchos errores es conocimiento que se redescubre.
    """
    err, calls = collections.Counter(), collections.Counter()
    for r in rs:
        for t in r["sequence"]:
            calls[family(t)] += 1
        for t, n in r["error_tools"].items():
            err[family(t)] += n
    out = [
        {"herramienta": t, "errores": n, "llamadas": calls[t], "tasa": round(n / max(1, calls[t]), 3)}
        for t, n in err.items()
        if n >= 5
    ]
    return sorted(out, key=lambda x: -x["errores"])[:15]


def compactions_per_100(rs):
    return round(100 * sum(r["compactions"] for r in rs) / max(1, sum(r["tool_calls"] for r in rs)), 2)


def context_friction(rs):
    """Qué rasgos de sesión van con más compactaciones (por 100 acciones), en sesiones grandes."""
    big = [r for r in rs if r["tool_calls"] >= 150]
    feats = {
        "navegador": lambda r: r["browser"] > 0,
        "subagentes": lambda r: bool(r["subagents"]),
        "ssh": lambda r: any(k.startswith("ssh") for k in r["bash"]),
        "plan_mode": lambda r: r["plan_mode"] > 0,
    }
    out = {}
    for k, fn in feats.items():
        a = [r for r in big if fn(r)]
        b = [r for r in big if not fn(r)]
        out[k] = {
            "con": len(a),
            "sin": len(b),
            "comp_100_con": compactions_per_100(a),
            "comp_100_sin": compactions_per_100(b),
        }
    tt = sorted((r["_m"]["tokens"] for r in rs), reverse=True)
    out["concentracion_tokens_top10pct"] = round(sum(tt[: max(1, len(tt) // 10)]) / max(1, sum(tt)), 2)
    return out


def adoption(rs):
    feats = {
        "skills": lambda r: bool(r["skills"]),
        "subagentes": lambda r: bool(r["subagents"]),
        "plan_mode": lambda r: r["plan_mode"] > 0,
        "otra_sesion": lambda r: r["sessions_started"] > 0,
        "artifacts": lambda r: r["artifacts"] > 0,
        "navegador": lambda r: r["browser"] > 0,
        "mcp_dominio": lambda r: any(tier(t) == "capital" and t.startswith("mcp:") for t in r["sequence"]),
        "scripts_repo": lambda r: any(tier(t) == "capital" and t.startswith("bash:") for t in r["sequence"]),
        "tarea_programada": lambda r: bool(r.get("scheduled_task")),
        "compacta": lambda r: r["compactions"] > 0,
    }
    people = sorted({r.get("user", "?") for r in rs})
    return {
        p: {
            k: round(
                sum(1 for r in rs if r.get("user", "?") == p and fn(r))
                / max(1, sum(1 for r in rs if r.get("user", "?") == p)),
                2,
            )
            for k, fn in feats.items()
        }
        for p in people
    }


def stars(rs, cards, limit=15):
    """Sesiones ejemplares: más apalancamiento y capital, menos fricción en su trabajo, y resueltas."""
    hum = [r for r in rs if r["_m"]["humana"]]
    groups = collections.defaultdict(list)
    for r in hum:
        groups[(cards.get(r["session_id"]) or {}).get("trabajo", "?")].append(r)
    out = []
    for trabajo, g in groups.items():
        if len(g) < 3:
            continue

        def z(vals, x):
            sd = st.pstdev(vals) or 1
            return (x - st.mean(vals)) / sd

        Ls = [min(r["_m"]["L"], 200) for r in g]
        Ks = [r["_m"]["K"] for r in g]
        Fs = [r["_m"]["F"] for r in g]
        for r in g:
            c = cards.get(r["session_id"]) or {}
            score = (
                z(Ls, min(r["_m"]["L"], 200))
                + z(Ks, r["_m"]["K"])
                - z(Fs, r["_m"]["F"])
                + (1 if c.get("resultado") == "resuelto" else -1 if c.get("resultado") == "abandonado" else 0)
                + (0.5 if c.get("valor") == "alto" else 0)
                + (1 if c.get("estrella") else 0)
            )
            out.append(
                {
                    "session_id": r["session_id"],
                    "trabajo": trabajo,
                    "repo": r["repo"],
                    "user": r.get("user"),
                    "titulo": r.get("title") or r.get("ai_title"),
                    "puntaje": round(score, 2),
                    "L": round(r["_m"]["L"], 1),
                    "K": round(r["_m"]["K"], 2),
                    "F": round(r["_m"]["F"], 1),
                    "por_que": c.get("estrella_por_que") or "",
                }
            )
    out.sort(key=lambda x: -x["puntaje"])
    return out[:limit] if limit else out


# --- Puntaje de oportunidad ----------------------------------------------------------------------
EFFORT = {
    "regla": 1,
    "adoptar": 1,
    "hook": 2,
    "skill": 2,
    "subagente": 2,
    "tool": 3,
    "mcp": 5,
    "app": 8,
    "nada": 99,
}


def opportunities(rs, cards, days=None):
    """O = sesiones × minutos medianos × personas × (1 + fricción relativa) ÷ esfuerzo.
    Se agrupa por la intención canónica de la ficha y por el tipo de ayuda propuesto."""
    by_id = {r["session_id"]: r for r in rs}
    F_med = med([r["_m"]["F"] for r in rs if r["_m"]["humana"]]) or 1
    groups = collections.defaultdict(list)
    for sid, c in cards.items():
        if sid not in by_id:
            continue
        for p in c.get("podria_ayudar") or []:
            if p.get("tipo") in (None, "nada"):
                continue
            key = (p["tipo"], c.get("trabajo", "?"), c.get("cliente", "?"))
            groups[key].append((by_id[sid], c, p))
    out = []
    for (tipo, trabajo, cliente), items in groups.items():
        ses = {r["session_id"] for r, _, _ in items}
        if len(ses) < 3:  # regla de tres
            continue
        rs_ = [by_id[s] for s in ses]
        mins = med([r["active_min"] for r in rs_])
        people = len({r.get("user") for r in rs_})
        f_rel = max(0.0, med([r["_m"]["F"] for r in rs_]) / F_med - 1)
        score = len(ses) * mins * people * (1 + f_rel) / EFFORT.get(tipo, 3)
        ideas = collections.Counter(p.get("que", "")[:120] for _, _, p in items)
        out.append(
            {
                "tipo": tipo,
                "trabajo": trabajo,
                "cliente": cliente,
                "sesiones": len(ses),
                "min_mediana": round(mins, 1),
                "personas": people,
                "friccion_relativa": round(f_rel, 2),
                "esfuerzo": EFFORT.get(tipo, 3),
                "O": round(score, 1),
                "ideas": [i for i, _ in ideas.most_common(4)],
            }
        )
    return sorted(out, key=lambda x: -x["O"])[:20]


def card_aggregates(rs, cards):
    ids = {r["session_id"] for r in rs}
    cs = [c for s, c in cards.items() if s in ids]
    agg = {k: collections.Counter() for k in ("trabajo", "resultado", "valor", "cliente")}
    fr = collections.Counter()
    minutes = collections.Counter()
    by_id = {r["session_id"]: r for r in rs}
    for c in cs:
        for k in agg:
            agg[k][c.get(k, "?")] += 1
        for f in c.get("fricciones") or []:
            fr[f] += 1
        minutes[c.get("trabajo", "?")] += by_id[c["session_id"]]["active_min"]
    intents = collections.defaultdict(list)
    for c in cs:
        intents[(c.get("trabajo"), (c.get("intencion") or "").lower().strip())].append(c["session_id"])
    created = collections.Counter(x.split(":")[0] for c in cs for x in c.get("capital_creado") or [])
    used = collections.Counter(x for c in cs for x in c.get("capital_usado") or [])
    return {
        **{k: dict(v.most_common()) for k, v in agg.items()},
        "fricciones": dict(fr.most_common()),
        "horas_por_trabajo": {k: round(v / 60, 1) for k, v in minutes.most_common()},
        "capital_creado_por_tipo": dict(created.most_common()),
        "capital_usado_top": dict(used.most_common(15)),
        "estrellas_llm": sum(1 for c in cs if c.get("estrella")),
    }


def load(patterns: list[str]) -> list[dict]:
    rows = []
    for pat in patterns:
        # Del archivo más viejo al más nuevo: la medición más reciente de una sesión manda
        for p in sorted(glob.glob(os.path.expanduser(pat)), key=os.path.getmtime):
            with open(p) as fh:
                rows += [json.loads(line) for line in fh if line.strip()]
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", nargs="+", required=True)
    ap.add_argument("--cards", nargs="*", default=[])
    ap.add_argument("--since")
    ap.add_argument("--baseline-weeks", type=int, default=4)
    ap.add_argument("--out", default="insights.json")
    a = ap.parse_args()

    rows = list({r["session_id"]: r for r in load(a.facts)}.values())  # una sesion retomada: la ultima
    if a.since:
        rows = [r for r in rows if r["start"][:10] >= a.since]
    cards = {c["session_id"]: c for c in load(a.cards)} if a.cards else {}
    for r in rows:
        r["_m"] = session_metrics(r)
    by_id = {r["session_id"]: r for r in rows}

    weeks = collections.defaultdict(list)
    for r in rows:
        weeks[week(r)].append(r)
    weekly = {w: scorecard(g, cards) for w, g in sorted(weeks.items())}
    ws = sorted(weekly)
    trend = None
    if len(ws) > a.baseline_weeks:
        cur, base = ws[-1], ws[-1 - a.baseline_weeks : -1]
        trend = {
            "semana": cur,
            "linea_base": base,
            **{
                k: {"actual": weekly[cur][k], "base": round(med([weekly[w][k] for w in base]), 3)}
                for k in (
                    "L_apalancamiento",
                    "K_capitalizacion",
                    "F_friccion",
                    "compactaciones_por_h",
                    "C_mtokens_por_sesion",
                )
            },
        }

    people = collections.defaultdict(list)
    for r in rows:
        people[r.get("user", "?")].append(r)
    by_trabajo = collections.defaultdict(list)
    for r in rows:
        if r["session_id"] in cards:
            by_trabajo[(cards[r["session_id"]].get("trabajo"), r.get("user", "?"))].append(r)

    out = {
        "generado": datetime.now(TZ).isoformat(timespec="minutes"),
        "total": scorecard(rows, cards),
        "por_persona": {p: scorecard(g, cards) for p, g in people.items()},
        "por_repo": {
            k: scorecard(g, cards)
            for k, g in sorted(
                collections.defaultdict(
                    list, {repo: [r for r in rows if r["repo"] == repo] for repo in {r["repo"] for r in rows}}
                ).items(),
                key=lambda x: -len(x[1]),
            )
        },
        "por_trabajo_y_persona": {
            f"{t}|{p}": scorecard(g, cards)
            for (t, p), g in sorted(by_trabajo.items(), key=lambda x: -len(x[1]))
            if len(g) >= 3
        },
        "semanal": weekly,
        "tendencia": trend,
        "patrones_secuencia": ngram_patterns(rows),
        "adhoc_repetido": adhoc_hotspots(rows, by_id),
        "errores": error_hotspots(rows),
        "contexto": context_friction(rows),
        "adopcion": adoption(rows),
        "estrellas": stars(rows, cards),
        "oportunidades": opportunities(rows, cards) if cards else [],
        "fichas": card_aggregates(rows, cards) if cards else {},
    }
    with open(a.out, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    t = out["total"]
    print(
        f"{t['sesiones']} sesiones · {t['horas_activas']} h activas · "
        f"L={t['L_apalancamiento']} K={t['K_capitalizacion']} F={t['F_friccion']} R={t['R_resueltas']}"
    )
    if trend:
        print(
            "tendencia",
            json.dumps({k: v for k, v in trend.items() if isinstance(v, dict)}, ensure_ascii=False),
        )
    for o in out["oportunidades"][:8]:
        print(
            f"  O={o['O']:8.1f} {o['tipo']:9s} {o['trabajo']}/{o['cliente']} "
            f"ses={o['sesiones']} min={o['min_mediana']} · {o['ideas'][0][:80]}"
        )
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
