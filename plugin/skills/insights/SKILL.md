---
name: insights
description: Analiza las sesiones de Claude Code de la persona (horas, capitalización, fricción, oportunidades, sesiones estrella) y, con su ok, suma el resultado al equipo de build101 en .md, en el tablero y en la bóveda. Usar cuando pida /insights, "analizá mis sesiones" o "subí mis insights".
argument-hint: "<nombre corto>"
---

# /insights

Hace en un paso lo de `docs/INSIGHTS-EQUIPO.md` del repo hackatonbuild101: hechos de cada sesión,
fichas con un modelo chico, indicadores, hallazgos, revisión con la persona y subida. Esa guía
tiene las fórmulas, los criterios de los hallazgos y el formato de `hallazgos.json`: leela antes
del paso 5.

La persona es `$ARGUMENTS`, en minúsculas. Si no vino, preguntale su nombre corto (nico, molle, beto).
Contale en una línea qué paso estás haciendo. Los datos personales quedan en
`~/.claude/session-insights/` y nunca entran al repo, que es público.

## 0. Preparar (Mac, Linux o Windows)

Corré esto en un solo bloque de Bash. En Windows es Git Bash: ahí no suele existir `python3`.

```bash
REPO="${BUILD101_HOME:-$(git rev-parse --show-toplevel 2>/dev/null)}"
[ -f "$REPO/tools/session_facts.py" ] || echo "NO_REPO"
for c in "$BUILD101_PYTHON" python3 python "py -3"; do
  [ -n "$c" ] && $c -c 'import sys; assert sys.version_info >= (3, 9)' 2>/dev/null && { PY="$c"; break; }
done
echo "REPO=$REPO PY=${PY:-NO_PYTHON}"
mkdir -p ~/.claude/session-insights
```

- `NO_REPO`: pedile la ruta de su clon de hackatonbuild101 y sugerile `export BUILD101_HOME=<ruta>`.
- `NO_PYTHON`: necesita Python 3.9 o más nuevo.
- En todos los pasos usá `$PY -X utf8` (sin eso Windows lee los archivos como cp1252) y las rutas
  con `~` entre comillas simples. Las herramientas entienden `/c/Users/...` de Git Bash.
- Shell state no persiste entre llamadas: repetí `REPO=... PY=...` al principio de cada bloque.

Si la persona ya corrió /insights antes (hay un `resultado.json` viejo), preguntale si quiere toda
la historia o solo desde la última carga (`--since AAAA-MM-DD` en los pasos 1 y 4).

## 1 a 4. Medir

```bash
D=~/.claude/session-insights
$PY -X utf8 "$REPO/tools/session_facts.py" --user <persona> --out "$D/facts.jsonl"
$PY -X utf8 "$REPO/tools/session_digest.py" --facts "$D/facts.jsonl" --out-dir "$D" --size 45
```

Si dice menos de 20 sesiones, avisá que con poca historia el análisis dice poco (con 13, solo un
tema llega al mínimo de 3 sesiones) y preguntá si sigue igual.

**Fichas.** Por cada `batch_N.jsonl`, un subagente en paralelo con modelo chico (haiku), con el
pedido del paso 3 de la guía, que escribe `cards_N.jsonl`. Sin subagentes: `claude -p` un lote por
vez. Antes de seguir, la cantidad de fichas tiene que ser igual a la de sesiones; si faltan, volvé
a correr el digest con `--skip "$D/cards_*.jsonl"` y clasificá solo esas.

```bash
$PY -X utf8 "$REPO/tools/session_insights.py" --facts "$D/facts.jsonl" --cards "$D/cards_*.jsonl" --out "$D/insights.json"
```

## 5. Hallazgos

Escribí `~/.claude/session-insights/hallazgos.json` siguiendo el paso 5 de la guía: cada cifra sale
de `insights.json`, de las fichas o de una cuenta sobre `facts.jsonl`. Para los temas de las
oportunidades, reusá los que ya cargó el equipo (la lista está en la guía) así se cruzan.

## 6. Revisión con la persona

Mostrale corto: la tarjeta (H, L, K, F, R, C), las tesis, las oportunidades, las estrellas, los
riesgos y lo compartible. Preguntale:

1. Si saca o cambia algo.
2. Si quiere sacar nombres de proyectos, clientes o datos personales. Si dice que sí, reemplazalos
   en `hallazgos.json` por algo genérico ("un cliente", "el repo de facturación").

**No sigas sin un ok explícito.** Si encontraste un secreto real, decile que lo rote y no lo copies.

## 7. Exportar

```bash
$PY -X utf8 "$REPO/tools/insights_export.py" --persona <persona> --facts "$D/facts.jsonl" \
  --insights "$D/insights.json" --hallazgos "$D/hallazgos.json" \
  --out "$D/resultado.json" --md "$D/resultado.md"
```

Si la validación falla, corregí `hallazgos.json` y repetí. El script borra tokens, IPs, mails y rutas.

## 8. Subir (con el ok del paso 6)

1. **Bóveda:** las oportunidades, estrellas y lo compartible van como lecciones al archivista, que
   las guarda como notas y las cruza con las de otras personas. Mostrale primero qué va a mandar:

   ```bash
   $PY -X utf8 "$REPO/tools/insights_to_vault.py" --resultado "$D/resultado.json" --dry-run
   $PY -X utf8 "$REPO/tools/insights_to_vault.py" --resultado "$D/resultado.json"
   ```

   Necesita `BUILD101_KEY`. Si el server responde 404, la cola todavía no está desplegada: decilo y seguí.
2. **Tablero:** los números van a la app del equipo, que guarda uno por persona y período (si
   subís el mismo período, lo reemplaza) y cruza las oportunidades que comparten 2 personas o más:

   ```bash
   curl -sf -X POST -H "Authorization: Bearer $BUILD101_KEY" -H "Content-Type: application/json" \
     --data-binary @"$D/resultado.json" "${BUILD101_API:-https://54-89-124-87.sslip.io}/api/insights"
   ```

   Si responde 404, el endpoint todavía no está desplegado: decilo y seguí.
3. **El .md:** pasale la ruta completa de `resultado.md` para que lo comparta con el equipo.

Al final, resumí en 3 líneas qué subió, adónde, y en qué temas coincide con el resto del equipo.
