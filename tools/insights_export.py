#!/usr/bin/env python3
"""Arma el resultado.json que se sube al tablero del equipo: números calculados + hallazgos revisados.

Los números salen de insights.json (tools/session_insights.py), nunca a mano. Los hallazgos
(tesis, estrellas, oportunidades, riesgos, capital para compartir) los escribe Claude en
hallazgos.json siguiendo docs/INSIGHTS-EQUIPO.md y la persona los aprueba. El script valida
el formato y borra secretos, IPs y rutas locales antes de que nada salga de la máquina.

    python3 tools/insights_export.py --persona nico \
        --facts ~/.claude/session-insights/facts.jsonl \
        --insights ~/.claude/session-insights/insights.json \
        --hallazgos ~/.claude/session-insights/hallazgos.json \
        --out ~/.claude/session-insights/resultado.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

METODO = "dik-insights-1"
TIPOS = {"skill", "tool", "subagente", "mcp", "hook", "regla", "app", "adoptar"}
TRABAJOS = {
    "operar-monitorear",
    "responder-cliente",
    "analizar-datos",
    "validar-resultados",
    "desarrollar",
    "depurar",
    "desplegar-infra",
    "documento-cliente",
    "revisar-ux",
    "relevar-sistema-cliente",
    "admin-finanzas",
    "investigar-evaluar",
    "disenar-producto",
    "coordinar-meta",
}
LIMITES = {"tesis": 3, "estrellas": 5, "oportunidades": 10, "riesgos": 5, "compartible": 8}
CAMPOS = {
    "estrellas": {"titulo", "trabajo", "como", "evidencia"},
    "oportunidades": {"tema", "que", "tipo", "trabajo", "evidencia", "sesiones", "horas", "O"},
    "riesgos": {"que", "accion"},
    "compartible": {"que", "tipo", "donde"},
}

SECRETOS = [
    (re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), "[token]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[token]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[token]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "[token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[token]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[token]"),
    (
        re.compile(r"(?i)\b(bearer|password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*\S+"),
        r"\1=[redactado]",
    ),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[clave]"),
    (re.compile(r"\b[0-9a-f]{32,}\b"), "[hex]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]"),
    (re.compile(r"(/Users|/home|C:\\Users)[/\\][^\s\"']+"), "[ruta]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
]


def scrub(value, counter):
    if isinstance(value, str):
        for rx, rep in SECRETOS:
            value, n = rx.subn(rep, value)
            counter[0] += n
        return value
    if isinstance(value, list):
        return [scrub(v, counter) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v, counter) for k, v in value.items()}
    return value


def check_hallazgos(h: dict) -> list[str]:
    errs = []
    for k, cap in LIMITES.items():
        items = h.get(k, [])
        if not isinstance(items, list):
            errs.append(f"{k}: tiene que ser una lista")
            continue
        if len(items) > cap:
            errs.append(f"{k}: {len(items)} ítems, el máximo es {cap}")
        for i, it in enumerate(items):
            if k == "tesis":
                if not isinstance(it, str) or len(it) > 400:
                    errs.append(f"tesis[{i}]: texto de hasta 400 caracteres")
                continue
            falta = CAMPOS[k] - set(it)
            if falta:
                errs.append(f"{k}[{i}]: faltan {sorted(falta)}")
            if "tipo" in it and it["tipo"] not in TIPOS:
                errs.append(f"{k}[{i}].tipo '{it['tipo']}' no está en {sorted(TIPOS)}")
            if k in ("estrellas", "oportunidades") and it.get("trabajo") not in TRABAJOS:
                errs.append(f"{k}[{i}].trabajo '{it.get('trabajo')}' no está en el vocabulario")
            if k == "oportunidades" and not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", str(it.get("tema", ""))):
                errs.append(f"oportunidades[{i}].tema '{it.get('tema')}': slug en minúsculas con guiones")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", required=True, help="nombre corto, minúsculas: alberto, nico")
    ap.add_argument("--facts", required=True)
    ap.add_argument("--insights", required=True)
    ap.add_argument("--hallazgos", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    persona = a.persona.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,30}", persona):
        sys.exit("--persona: minúsculas, números, - o _")
    with open(os.path.expanduser(a.facts)) as fh:
        starts = sorted(json.loads(line)["start"][:10] for line in fh if line.strip())
    with open(os.path.expanduser(a.insights)) as fh:
        ins = json.load(fh)
    with open(os.path.expanduser(a.hallazgos)) as fh:
        hall = json.load(fh)

    errs = check_hallazgos(hall)
    if errs:
        sys.exit("hallazgos.json no pasa la validación:\n  " + "\n  ".join(errs))

    weeks = sorted(ins.get("semanal", {}))[-12:]
    pt = ins.get("por_trabajo_y_persona") or {}
    users = {k.partition("|")[2] for k in pt}
    por_trabajo = {
        k.partition("|")[0]: v for k, v in pt.items() if k.partition("|")[2] == persona or len(users) == 1
    }
    fichas = ins.get("fichas") or {}
    adop = ins.get("adopcion") or {}
    out = {
        "persona": persona,
        "metodo": METODO,
        "cargado": datetime.now().astimezone().isoformat(timespec="minutes"),
        "periodo": {"desde": starts[0], "hasta": starts[-1]},
        "tarjeta": ins["total"],
        "semanal": [{"semana": w, **ins["semanal"][w]} for w in weeks],
        "por_trabajo": por_trabajo,
        "adopcion": adop.get(persona) or (next(iter(adop.values())) if len(adop) == 1 else {}),
        "capital": {
            "usado_top": fichas.get("capital_usado_top", {}),
            "creado": fichas.get("capital_creado_por_tipo", {}),
        },
        "friccion_por_tipo": fichas.get("fricciones", {}),
        **{k: hall.get(k, []) for k in LIMITES},
    }
    n = [0]
    out = scrub(out, n)
    body = json.dumps(out, ensure_ascii=False, indent=1)
    if len(body.encode()) > 200_000:
        sys.exit("resultado.json pasa de 200 KB: recortá hallazgos")
    with open(os.path.expanduser(a.out), "w") as fh:
        fh.write(body)
    print(
        f"{a.out}: {out['tarjeta']['sesiones']} sesiones, {len(out['oportunidades'])} oportunidades, "
        f"{n[0]} reemplazos de datos sensibles"
    )
    print(f"documento en el tablero: resultados/{persona}--{out['periodo']['hasta']}")


if __name__ == "__main__":
    main()
