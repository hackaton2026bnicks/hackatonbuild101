# DIK v2 · Método para evaluar y capitalizar el uso de Claude

Complemento de [DIK-V2.md](DIK-V2.md), sección 3 (patrones de uso). Aquella propone las capas y el analista semanal; esto es la etapa **E0 hecha** sobre datos reales y la fórmula para medir, comparar y decidir qué construir. Corre igual hacia atrás (línea de base con toda la historia) y hacia adelante (cada sesión y cada semana), para una persona o para todo el equipo.

Probado con las 275 sesiones de Alberto (2026-07-06 a 2026-10-18, 167 h activas, 38.800 acciones) y con una sesión de Nico tomada de los hooks de build101.

## Qué responde

Seis preguntas, siempre con los mismos números para poder comparar semanas, personas y trabajos:

1. **¿Dónde se va el tiempo?** Horas por tipo de trabajo y cliente.
2. **¿Qué funciona y se puede copiar?** Sesiones ejemplares y lo que tienen en común.
3. **¿Qué se repite y conviene construir?** Oportunidades priorizadas, con el tipo de acción.
4. **¿Dónde cuesta?** Fricción por trabajo, por herramienta y por rasgo de sesión.
5. **¿Estamos capitalizando?** Qué parte del trabajo pasa por activos reusables, y si sube.
6. **¿Qué es riesgoso?** Escrituras a producción, envíos sin confirmar, secretos.

## Las dos capas por sesión

| Capa | Qué es | Cómo se saca | Archivo |
|---|---|---|---|
| A. Hechos | Volumen, herramientas, MCP, skills, subagentes, errores, interrupciones, rechazos, compactaciones, tokens, salidas (commits, PR, artifacts) y la **secuencia canónica** de acciones (`ssh{host}`, `mcp:<base>.query`, `skill:<rutina>`, `bash:python:sql`) | Determinístico, sin LLM, desde `~/.claude/projects` (8 s para 275 sesiones) o desde los hooks de build101 | `tools/session_facts.py` |
| B. Ficha | Trabajo, intención canónica, cliente, resultado, valor, fricciones, pasos repetidos, capital usado y creado, qué podría ayudar, estrella | Un modelo chico con vocabularios cerrados; 6 lotes en paralelo para 277 sesiones | `tools/work_card_prompt.md` |

Los indicadores, detectores y puntajes salen de cruzar las dos: `tools/session_insights.py`.

## Los seis números

Se calculan por sesión y se agregan por semana, persona, repo o trabajo. Entre paréntesis, la línea de base de Alberto.

| | Número | Fórmula | Qué dice |
|---|---|---|---|
| **H** | Horas activas (167 h) | Suma de los huecos de 5 min o menos entre eventos | Dónde se va el tiempo de verdad, sin contar sesiones abiertas y quietas |
| **L** | Apalancamiento (23,9) | acciones de Claude (incluidos subagentes) ÷ prompts humanos | Cuánto trabaja Claude por cada pedido. Se lee junto con F y R: alto con fricción alta es Claude dando vueltas |
| **K** | Capitalización (0,18) | acciones *capital* ÷ (acciones *capital* + *ad hoc*) | Qué parte del trabajo pasa por activos que ya traen el conocimiento. Es el número que el método busca subir |
| **F** | Fricción (7,2) | 100 × (errores + 3·(interrupciones + rechazos + correcciones) + 10·compactaciones) ÷ acciones | Costo de fricción cada 100 acciones, pesado por lo que cuesta cada evento: un reintento, un turno humano, el contexto perdido |
| **R** | Resueltas (66 %) | sesiones `resuelto` ÷ sesiones con ficha | Si el trabajo termina |
| **C** | Costo (5,8 M tokens) | Mediana de tokens por sesión, más compactaciones por hora activa (0,73) | Cuánto sale cada sesión y cuánto contexto se pierde |

**Niveles de cada acción para K**, que es lo que más se discute:

- **capital**: skill, subagente especializado (Explore, Plan, propios), herramienta MCP de dominio (un MCP con las operaciones de un proceso del negocio o de administración), script o comando del repo (`query.py`, `sync-memoria.sh`, `npm run`, `make`).
- **ad hoc**: herramienta genérica a la que Claude le pone el conocimiento en el momento: SQL suelto (`*.query`), `ssh` + `docker exec`, heredoc de Python que consulta datos, `curl`, navegador, búsqueda web.
- **base**: leer, editar, buscar y navegar el repo. No entra en K.

**Reglas para comparar** (entre semanas o entre personas): siempre dentro del mismo `trabajo`, con medianas, con 3 sesiones o más de cada lado, separando las corridas programadas de las sesiones con persona, y mostrando la sesión ejemplar para copiar el *cómo*, no solo el número.

## Catálogo de insights

Cada tipo tiene un detector, un umbral y la acción que suele disparar. El ejemplo es real, de las sesiones de Alberto.

| # | Tipo | Detector | Acción típica | Ejemplo |
|---|---|---|---|---|
| 1 | **Mapa de trabajo** | Horas por `trabajo` × `cliente` | Decidir dónde invertir primero | `desarrollar` se lleva 66 h (40 %). Dos clientes suman el 84 % del tiempo |
| 2 | **Uso estrella** | Puntaje S (abajo) y marca de la ficha; rasgos que distinguen a las estrellas del resto | Difundir el *cómo*: plantilla de pedido, skill, MCP | El 94 % de las sesiones estrella usa capital (skill, MCP de dominio o script del repo), contra 47 % del resto. 6 de las 21 son de administración con un MCP propio, de 1 a 9 minutos. El largo del primer pedido no las distingue (44 % contra 43 %) |
| 3 | **Repetición determinística** | N-gramas de acciones de dominio en 3+ sesiones; acción ad hoc en 3+ sesiones | **tool** (script o herramienta MCP) | `ssh` a la VPS de un cliente: 909 llamadas en 32 sesiones, la mitad `docker exec psql`. Levantar el entorno local (túnel, base, login) se pide en 10 sesiones |
| 4 | **Repetición con criterio** | Misma `intencion` de la ficha en 3+ sesiones, con pasos que cambian | **skill** | Deploy a una VPS (27 sesiones; 15 fichas piden una receta o un script), barrido UX en el rol del comprador (9), revisar órdenes de compra sugeridas en dos clientes (7), propuesta a cliente (reescrita 3 o 4 veces) |
| 5 | **Conocimiento que se redescubre** | Tasa de error por herramienta de datos; fricción `dato-dificil-de-encontrar`; la misma pregunta en 2+ sesiones | Unidad `dato`, regla en CLAUDE.md o **MCP** curado | El MCP SQL de un cliente: 190 errores en 1.734 consultas (11 %), 146 por columnas que no existen. Otro cliente no tiene MCP: 38 de las 75 sesiones de su repo consultan la base por bash (`query.py`, `cols.py`, Python, `docker exec`) |
| 6 | **Fricción de contexto** | Compactaciones cada 100 acciones por rasgo de sesión; concentración de tokens | **subagente**, archivo de estado, partir la sesión | En sesiones grandes, con navegador se compacta 3,5 veces más (0,84 contra 0,24). El 10 % de las sesiones se lleva el 67 % de los tokens |
| 7 | **Fricción de comprensión** | `claude-corregido` + `explicacion-poco-clara`; la misma corrección en 2+ sesiones | **regla** de estilo o criterio | 66 sesiones (24 %) con corrección o explicación poco clara. "Punteá" o "punteo" aparece en 30 prompts de 25 sesiones |
| 8 | **Capacidad sin adoptar** | Matriz persona × capacidad; capital que existe y no se invocó cuando aplicaba | **adoptar** | 4 corridas diarias no invocaron la skill de la rutina. 223 acciones de navegador para facturar con una API ya investigada. 30 usos de skills a demanda en 3 meses |
| 9 | **Riesgo** | Escrituras a producción fuera de una herramienta, envíos sin confirmación, secretos en prompts, modo de permisos | **hook** | 35 escrituras a la base de producción por `ssh` + `psql` en 8 sesiones. 2 avisos de cobranza enviados con el período equivocado. Un token pegado en un prompt |
| 10 | **Tendencia** | Semana actual contra la mediana de las 4 anteriores | Revisar si lo construido llegó al momento de uso | Las sesiones se triplicaron desde la W40 y K quedó entre 0,10 y 0,20: crece el uso, no el capital |

## Fórmulas de decisión

### Oportunidad: qué construir primero

```
O = sesiones (30 días) × minutos activos medianos × personas × (1 + fricción relativa) ÷ esfuerzo
```

- *Fricción relativa*: F mediana de las sesiones del patrón ÷ F mediana general − 1, con piso 0.
- *Esfuerzo*: regla y adoptar 1, hook / skill / subagente 2, tool 3, MCP 5, app 8.
- Solo cuentan patrones con 3 sesiones o más (la regla de tres de DIK v1).
- Se lee como minutos-persona en juego por mes, ajustados por fricción y costo de construirlo.

### Tipo de acción: qué construir

| Si el patrón… | Construir |
|---|---|
| Repite los mismos pasos | **tool**: script en el repo o herramienta MCP |
| Repite una intención con criterio y receta | **skill** |
| Lee o verifica mucho y compacta | **subagente** que devuelve solo el resumen |
| Redescubre datos o esquema | unidad **dato** y MCP curado |
| Recibe la misma corrección | **regla** en CLAUDE.md o memoria |
| Tiene una regla que no puede fallar o un riesgo | **hook** |
| Lo necesita alguien sin Claude | **app** |
| Ya tiene capital que no se usó | **adoptar** |

### Estrella: qué sesión copiar

```
S = z(L) + z(K) − z(F) + resultado (+1 resuelto, −1 abandonado) + 0,5 si valor alto + 1 si la ficha la marca
```

Los z se calculan **dentro del mismo trabajo**, así no se compara un deploy con una consulta. El 10 % superior con resultado `resuelto` son las sesiones ejemplares de la semana.

### Efecto: si lo construido sirvió

Para cada acción construida, sobre las sesiones de su patrón, 4 semanas antes contra 4 semanas después:

- Δ minutos activos medianos, Δ F, Δ errores de la herramienta atacada.
- **Adopción**: qué parte de las ocurrencias usa el capital nuevo.
- Sirvió si baja 20 % el tiempo o 30 % la fricción **y** la adopción pasa del 50 %. Si el patrón sigue apareciendo sin el capital nuevo, la acción no llegó al momento de uso: es un hallazgo en sí mismo (DIK-V2, inyección situacional).

## Hacia atrás y hacia adelante

| Cuándo | Qué corre | Resultado |
|---|---|---|
| Una vez por persona | `session_facts.py` sobre toda la historia + fichas en lotes | Línea de base por persona y trabajo |
| Al cerrar cada sesión | Hook `SessionEnd`: hechos de la sesión + ficha con `claude -p` y un modelo chico. Se suben a build101 (`session_facts`, `work_cards` de DIK-V2) | El dato queda listo sin que nadie corra nada |
| Cada semana | `session_insights.py` sobre la semana contra las 4 anteriores | Tarjeta personal, 3 oportunidades, sesiones ejemplares, matriz de adopción. Revisión del viernes: hasta 3 acciones con dueño |
| Cada mes | Efecto de lo construido; revisión de vocabularios y pesos | Backlog que se cierra con evidencia |

Privacidad, igual que DIK-V2: el transcript no sale de la máquina, `--share` deja solo conteos y tokens canónicos, el informe personal es privado y el equipo ve agregados por trabajo y las sesiones que su autor marcó como compartibles.

## Cómo correrlo hoy

```bash
python3 tools/session_facts.py --user <persona> --out ~/.claude/session-insights/facts.jsonl
```

Las fichas: correr `tools/work_card_prompt.md` sobre lotes de unas 45 sesiones (con subagentes en paralelo o `claude -p`) y guardar `cards_*.jsonl` al lado.

```bash
python3 tools/session_insights.py --facts ~/.claude/session-insights/facts.jsonl --cards '~/.claude/session-insights/cards_*.jsonl' --out ~/.claude/session-insights/insights.json
```

Para el equipo, cada persona corre el primer comando con `--share` y se juntan los archivos en `--facts 'equipo/*.jsonl'`.

## Límites conocidos

- `session_insights.py` agrupa las oportunidades por tipo de ayuda, trabajo y cliente: es un primer corte y mezcla ideas distintas. La lista de 10 del informe sale de agrupar a mano, por tema, las 270 ideas de las fichas. Hacia adelante ese paso lo hace el analista semanal de DIK-V2, con un LLM sobre las ideas de la semana.
- Las correcciones por expresión regular son ruidosas ("no entiendo" no es una corrección). La ficha manda; el regex queda como señal barata.
- L alto no siempre es bueno: las corridas programadas lo inflan y una sesión que da vueltas también. Por eso se compara dentro del trabajo y junto con F y R.
- Los pesos de F (1, 3, 10) hacen que errores y compactaciones aporten parecido en la línea de base (42 % y 48 %). Hay que recalibrarlos con datos del equipo.
- Los hooks de build101 todavía no mandan tokens, compactaciones ni el nombre de la skill o el subagente. Hay que sumarlos a `forward.py` (DIK-V2, capa A).
- En el server hay por ahora una sola sesión real de Nico: la comparación entre personas está armada pero sin datos suficientes. Con 2 semanas de hooks de los dos ya alcanza.
