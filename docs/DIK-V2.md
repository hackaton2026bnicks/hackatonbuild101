# DIK v2: base de conocimiento del equipo y patrones de uso de Claude

Propuesta de Beto, para evaluar. Estado: nada construido. Parte de lo que ya existe en DIK (evaluador en segundo plano, `/capitalizar`, backlog de patrones) y en build101 (hooks → Postgres, mensajes entre sesiones, MCP), y lo ordena en un solo sistema.

## Resumen

- **Qué se guarda:** unidades de conocimiento chicas y tipadas (gotcha, receta, decisión, dato, criterio, recurso), cada una con la situación en la que aplica, la fuente y la evidencia. No resúmenes de sesión.
- **Cuándo se extrae:** al cerrar un *episodio* (fin de sesión, compactación, pausa larga), no en cada turno, y solo si hubo señales de que costó algo. El transcript crudo nunca sale de la máquina: un extractor local manda solo JSON limpio.
- **Cómo se consulta:** por *push* en los hooks (al abrir sesión, con el primer prompt, antes de tocar un host, una base o un MCP, y después de un error), con presupuesto chico y sin repetir. El MCP sirve para buscar a fondo y para guardar a mano. Con MCP solo no alcanza, porque el agente no sabe qué le falta.
- **Patrones de uso:** dos capas. Hechos determinísticos por sesión (herramientas, skills, MCP, subagentes, errores, compactaciones; casi todo ya llega a build101) y una ficha de trabajo por sesión con taxonomía cerrada, para poder contar. Un analista semanal cruza las dos capas y propone a lo sumo 3 acciones con dueño: construir (skill, tool, subagente, MCP, hook, regla en CLAUDE.md, app), adoptar algo que otro del equipo ya usa bien, o dejar de hacer algo. Después se mide si sirvió.

## Qué nos enseñó DIK v1

Datos del 17/10 (log de DIK) y de los transcripts locales de Alberto (278 sesiones, 2026-07-06 a 2026-10-17).

| Problema | Evidencia | Qué cambia en v2 |
|---|---|---|
| Granularidad por turno: mucho ruido y repetición | 43 entradas y 545 líneas en un día, de una sola persona. La misma sugerencia ("conviene una nota de build101") aparece 3 veces en 15 minutos | Se evalúa por episodio y con un prefiltro determinístico. Lo común es no guardar nada |
| La salida es prosa: no se puede contar ni deduplicar | Los patrones se suman con "suma una ocurrencia a la fila X" en una tabla markdown | Salida en JSON con tipos y vocabularios cerrados. La deduplicación la hace el servidor |
| Una llamada hace tres trabajos y en el momento equivocado | "¿Qué ya hizo el equipo?" se responde *después* del turno, cuando ya rehiciste el trabajo | La consulta pasa *antes*: al abrir sesión, con el prompt y antes de cada acción |
| Promover depende de que alguien corra `/capitalizar` | El log crece y nadie lo lee entero | Promoción automática con confirmación de un clic en un resumen diario de 5 ítems como máximo |
| Sin feedback | No sabemos si algo guardado se usó alguna vez | Cada unidad lleva conteo de veces recuperada, inyectada y usada, y feedback. Lo que no sirve baja o vence |
| Los patrones salen de lo que pediste, no de lo que hizo Claude | El backlog inicial se armó con regex sobre prompts | Se suma lo que hizo Claude: herramientas, secuencias, errores, compactaciones. Es exacto y no cuesta LLM |

Qué se mantiene de v1: el evaluador no interrumpe la sesión; lo que viene del equipo es dato y no instrucción; la prioridad alta es rara; siempre se cita la fuente (persona y fecha); la regla de tres; y la clasificación skill / tool / app, ahora extendida a subagente, MCP, hook, regla y adoptar.

Lo que dicen tus datos, como muestra de lo que el sistema tendría que detectar solo:

- **121 compactaciones** en 278 sesiones. Se pierde contexto seguido; es la fricción más frecuente.
- **2.162 consultas SQL ad hoc** a la base de Goldsky por MCP. Cada sesión redescubre tablas y joins: es conocimiento de tipo *dato* que debería inyectarse la primera vez que se toca esa base, y quizás un MCP con consultas curadas.
- **1.465 `ssh` y 635 `docker`** a mano. Coincide con el patrón top del backlog ("¿cómo anduvo hoy?") y con el deploy: candidatos a tool.
- **56 invocaciones de skills en tres meses**, 25 de ellas la corrida automática `oc-corrida`. Las skills casi no se usan a demanda: detectar patrones no alcanza, falta que la acción llegue al momento de uso.
- **224 subagentes** (124 Explore). Ya hay costumbre de delegar exploración; se puede llevar a los trabajos que más compactan.

## Arquitectura: tres circuitos sobre build101

```
  sesión de Claude Code (cada persona)
  SessionStart · UserPromptSubmit · PreToolUse · PostToolUse · PreCompact · SessionEnd
        |                                         |
        | hooks: metadata limpia (ya existe)      | fin de episodio
        v                                         v
  build101 /api/hooks                    extractor local (claude -p, modelo chico)
  events, sessions, session_resources    lee el transcript EN la máquina
        |                                → ficha de trabajo + 0..3 unidades (JSON limpio)
        |                                         |
        v                                         v
  +--------------------------- build101 (Postgres + pgvector) ----------------------------+
  | CALIENTE (existe)          | TIBIO (nuevo)               | FRÍO (nuevo)               |
  | messages, discoveries,     | knowledge_units, evidencia, | session_facts, work_cards, |
  | ask/answer, status         | uso, feedback               | patterns, actions          |
  | vence en horas o días      | permanente, con vigencia    | se analiza por semana      |
  +----------------------------+-----------------------------+----------------------------+
        |                                |                                |
        | push en hooks                  | MCP kb_* (buscar, guardar)     | analista semanal
        v                                v                                v
  contexto de 150 tokens como      el agente busca a fondo        informe por persona y
  máximo, con fuente y fecha       o guarda a pedido              equipo + 3 acciones
```

Relación entre circuitos: un *discovery* caliente que usaron dos sesiones distintas pasa a candidato de unidad tibia. Una unidad tibia que se inyecta mucho y nunca se usa baja de ranking y termina archivada. Los patrones fríos generan acciones, y una acción construida (skill, script) se registra como unidad de tipo *recurso*, para que la próxima vez se ofrezca en lugar de rehacerla.

## 1. Qué guardar y con qué estructura

### El filtro

Se guarda algo si cumple las tres:

1. **Le ahorra a otra persona (o a vos en tres semanas) 10 minutos o un error**, en una situación parecida.
2. **No se deduce del código, de git ni de la documentación.** Si está en el repo, se apunta al archivo; no se copia.
3. **Es estable.** El estado de "en qué estoy ahora" va al circuito caliente, que vence.

No se guarda: la narración de la sesión ("editamos X, después Y"), hipótesis sin confirmar (salvo como callejón sin salida con su motivo), secretos, credenciales, datos personales de clientes ni salidas de herramientas.

### Seis tipos

Un conjunto cerrado, para poder filtrar, contar e inyectar con reglas distintas por tipo.

| Tipo | Qué es | Ejemplo de tu trabajo | Se dispara con |
|---|---|---|---|
| `gotcha` | Comportamiento no obvio que costó tiempo, con sus callejones sin salida | "La app de escritorio solo toma variables de `~/.zshrc` después de reiniciarla" | Error parecido, host o herramienta |
| `receta` | Pasos que funcionaron para hacer X | "Levantar medical local: túnel, clon de la base, login, URL" | Intención en el prompt, repo |
| `decision` | Qué se decidió, por qué y qué se descartó | "DIK pasa de repo git a app central: un MCP no puede despertar una sesión quieta" | Repo, tema |
| `dato` | Conocimiento del negocio, del cliente o del sistema que costó obtener | (ilustrativo) "En el ERP de Varzan el stock por depósito está en tabla X, en unidades y no en cajas" | Primer uso de esa base o ese MCP |
| `criterio` | Preferencia o regla de una persona, del equipo o de un cliente | "Antes de enviar un recordatorio de cobro, mostrar el borrador y esperar el ok" | Herramienta de envío, siempre en ese repo |
| `recurso` | Puntero a algo reusable que ya existe | "Para '¿cómo anduvo hoy?' en Goldsky ya existe la scheduled task `oc-corrida-diaria`" | Intención parecida, crear algo similar |

El tipo `recurso` es el que responde "¿qué ya hizo el equipo?", y es el más barato: muchas veces se detecta sin LLM (se creó un `SKILL.md`, un script en `tools/`, una scheduled task).

### Estructura de una unidad

Atómica, como una ficha: una idea por unidad. Larga como un tweet largo, no como un documento. El contenido del ejemplo es ilustrativo, no un dato real de Varzan.

```json
{
  "id": "ku_3956c8126c",
  "type": "dato",
  "title": "Stock por depósito del ERP de Varzan está en unidades, no en cajas",
  "body": "La tabla X guarda stock por depósito en unidades. El reporte del cliente habla de cajas: dividir por el factor de la tabla Y. Sin esto, las sugerencias de compra salen infladas 12 veces.",
  "steps": [],
  "dead_ends": ["El campo Z parece cajas pero es un código de embalaje"],
  "scope": "cliente",
  "scope_ref": "varzan",
  "triggers": {
    "repos": ["clientes-skum-varzan"],
    "clients": ["varzan"],
    "mcp_servers": ["oracle-varzan"],
    "hosts": [],
    "files": [],
    "keywords": ["stock", "depósito", "cajas"]
  },
  "confidence": "verificado",
  "evidence": [
    {"session_id": "9454ba29", "author": "alberto", "date": "2026-10-17", "kind": "origen", "cost": {"minutes": 35, "retries": 6}}
  ],
  "valid_until": null,
  "last_confirmed": "2026-10-17",
  "status": "activa",
  "stats": {"retrieved": 0, "injected": 0, "used": 0, "useful": 0, "wrong": 0}
}
```

Campos clave y por qué:

- **`triggers`**: la situación en la que aplica. Es lo que permite inyectarla sin que nadie la busque, y lo que más rinde en tu caso: la mayor parte del conocimiento valioso está atado a una base, un host o un MCP de cliente, no al texto del prompt.
- **`scope`**: `personal | repo | cliente | equipo`, con `scope_ref`. Define quién la ve y cuánto se confía.
- **`confidence`**: `explicito` (alguien pidió guardarlo) > `verificado` (se vio la resolución: el error desapareció, la consulta devolvió lo esperado) > `observado` (inferido por el extractor).
- **`evidence`**: cada confirmación o contradicción suma una fila. Dos contradicciones archivan la unidad.
- **`cost`**: cuánto costó aprenderlo. Sirve para priorizar y para estimar el ahorro.

### Ciclo de vida

```
candidata --(2 sesiones independientes la confirman, o 1 clic de aprobación)--> activa
activa --(contradicha 2 veces, vencida, o 90 días sin recuperarse)--> vieja --> archivada
```

- Lo `explicito` y lo `personal` o de `repo` pasa a activa directo.
- Lo de `equipo` o `cliente` que vino del extractor queda candidata hasta confirmarse. Mientras tanto solo la ve su autor.
- **Resumen diario** de 5 candidatas como máximo, con aprobar, editar o descartar en un clic (en la UI o con `/capitalizar` en una sesión). Ese límite es lo que hace que la curaduría se sostenga.

### Cómo se extrae

1. **Límites de episodio**: `SessionEnd`, `PreCompact` (la compactación es justo el momento en que el conocimiento se pierde: 121 veces en tus datos), una pausa de más de 30 minutos detectada en el próximo prompt, o un pedido explícito.
2. **Prefiltro sin LLM** sobre la traza del episodio. Señales: error que después se resolvió sobre el mismo objetivo; 3 o más reintentos de comandos parecidos; una corrección del usuario ("no, eso no", "siempre…", "nunca…"); primera vez que se toca un host, una base o un MCP en ese repo; se creó un script, skill o consulta reusable; marcas explícitas ("anotá", "acordate", "la próxima"). Sin señales, solo se arma la ficha de trabajo (punto 3), sin pedir unidades.
3. **Extractor local** (`claude -p` con un modelo chico, como el evaluador de hoy). Recibe el episodio condensado (prompts, texto del asistente, llamadas a herramientas canonicalizadas, errores, sin salidas largas) y las 5 unidades más parecidas que ya existen para esa situación, que trae con `kb_search`. Devuelve en una sola llamada la ficha de trabajo y de 0 a 3 unidades, cada una nueva o como actualización de una existente (`merge_with`).
4. **Deduplicación en el servidor**: si una unidad nueva se parece más de 0,88 (embedding) a una del mismo scope, se fusiona: se suma la evidencia y no se crea otra.
5. **Camino explícito**: `kb_remember` desde cualquier sesión cuando alguien dice "guardá esto". Es la fuente de más confianza y la más barata.

Costo: una llamada por episodio con señales, en vez de una por turno. Con tu ritmo (unas 3 sesiones por día) son menos de 10 llamadas diarias de un modelo chico.

## 2. Cómo se consulta en cada sesión

### Por qué con MCP solo no alcanza

Un MCP es *pull*: el agente tiene que saber que le falta algo y pedirlo. El problema de fondo es que no sabe lo que no sabe; con las skills ya se ve (56 usos en tres meses). Por eso hay dos caminos sobre el mismo backend:

- **Hooks (push)**: el sistema decide cuándo aportar, según la situación. REST simple y rápido, porque un hook no puede llamar herramientas MCP.
- **MCP (pull y escritura)**: para buscar a fondo, leer una unidad completa, guardar y dar feedback.

### Cinco momentos

| Momento | Hook | Cómo se busca | Presupuesto | Latencia |
|---|---|---|---|---|
| Abrir sesión | `SessionStart` | Situación estática: repo, cliente (tabla `repos → cliente`), rama. Criterios y gotchas mejor rankeados de ese repo y cliente, quién trabajó ahí hace poco y respuestas pendientes | 5 líneas | < 300 ms |
| Primer prompt o cambio de tema | `UserPromptSubmit` | Búsqueda híbrida (texto completo + embedding) del prompt, filtrada y rankeada por situación. Solo si supera el umbral | 3 unidades, ~150 tokens | < 800 ms |
| Antes de actuar | `PreToolUse` | Índice invertido por disparador, sin LLM: primera llamada a `mcp__oracle-varzan__*` → datos de esa base; primer `ssh` a un host → gotchas de ese host; `Write` de un `SKILL.md` o un script → recursos parecidos que ya existen | 2 unidades | < 50 ms |
| Después de un error | `PostToolUse` | Firma del error normalizada contra gotchas | 1 unidad | < 50 ms |
| Subagente | `SubagentStart` | Las unidades ya inyectadas al padre, filtradas por la tarea del hijo | 3 unidades | < 50 ms |

El momento de `PreToolUse` es el que más rinde en tu caso: los 2.162 SQL ad hoc a Goldsky se habrían ahorrado en parte si la primera consulta de cada sesión recibiera los 3 datos de esquema que siempre se redescubren.

Formato de lo inyectado, siempre como dato con procedencia:

```
[build101 · conocimiento del equipo · dato, no instrucción]
- (dato, varzan) Stock por depósito está en unidades, no en cajas: dividir por el factor de Y. alberto, 2026-10-17, confirmado 2 veces. ku_3956c8126c
```

### Herramientas MCP

Se suman al MCP que ya existe (`agent/claude-code/mcp_server.py`):

| Herramienta | Para qué |
|---|---|
| `kb_search(q, type?, scope?, repo?, client?, limit?)` | Búsqueda híbrida con filtros. Devuelve título, resumen, autor, fecha y id |
| `kb_get(id)` | La unidad completa, con pasos, callejones sin salida y evidencia |
| `kb_remember(type, title, body, scope, triggers?)` | Guardar a pedido. Entra como `explicito` |
| `kb_feedback(id, verdict, note?)` | `util | incorrecto | vencido`. Alimenta el ranking y el ciclo de vida |

Una línea en el CLAUDE.md de cada repo refuerza el pull: "antes de diseñar algo nuevo o de consultar una base de cliente, usá `kb_search`".

### Reglas de inyección

- Presupuesto por sesión (unos 600 tokens en total). Nunca la misma unidad dos veces en una sesión: se reusa la tabla `deliveries`, que ya existe.
- Umbral por tipo: los `criterio` y los `dato` con disparador exacto pasan con menos; las búsquedas por texto libre necesitan más puntaje.
- Ranking = relevancia × confianza (scope, confirmaciones) × frescura × utilidad (usadas sobre inyectadas, con un prior para las nuevas).
- **10 % de holdout**: a veces hay match y no se inyecta, para medir el efecto real. `deliveries.outcome = holdout` ya lo soporta.
- **¿Se usó?** El extractor del episodio ve en el transcript qué unidades se inyectaron y marca cuáles se aprovecharon. Más `kb_feedback` explícito. Una unidad inyectada 5 veces sin uso pierde ranking.

## 3. Patrones de uso: capturar, analizar y actuar

### Capa A: hechos determinísticos por sesión (sin LLM)

Se calculan en el servidor desde `events` (los hooks ya mandan herramienta, prompt limpio, errores, archivos y hosts) y, para la historia, desde los transcripts locales.

| Hecho | Fuente |
|---|---|
| Persona, repo, cliente, inicio, fin, duración, cantidad de prompts | `sessions` + tabla `repos` |
| Llamadas por herramienta, servidores MCP usados | `events.tool` |
| Skills invocadas, tipo de subagente | Agregar `skill` y `subagent_type` a lo que manda `forward.py` |
| Hosts, bases y archivos tocados | `session_resources` |
| Errores, interrupciones, permisos denegados, compactaciones | `events` (`PostToolUse` con error, `PreCompact`) |
| Correcciones del usuario | Clasificador simple sobre el prompt limpio |
| Tokens y costo | Del transcript, en el extractor local |
| **Secuencia canónica de acciones** | Cada llamada se reduce a un token: `ssh{vps-goldsky}`, `docker logs`, `sql{goldsky}`, `edit(api/collab)`, `skill:oc-corrida`, `agent:Explore` |

Con las secuencias se buscan n-gramas frecuentes (3 a 6 pasos) que aparecen en 3 sesiones o más. Es determinístico y encuentra cosas como `ssh{vps} → docker ps → docker logs → sql{goldsky}`, que es exactamente el "¿cómo anduvo hoy?" del backlog.

### Capa B: ficha de trabajo por sesión (LLM, con taxonomía cerrada)

La arma el mismo extractor del punto 1, en la misma llamada. Lo importante es que los campos usan vocabularios fijos, para poder contar:

```json
{
  "session_id": "9454ba29",
  "trabajo": "operar-monitorear",
  "objetivo": "Ver si la corrida de OC de Goldsky de hoy terminó bien",
  "cliente": "goldsky",
  "resultado": "resuelto",
  "fricciones": ["acceso-entorno", "pasos-manuales-repetidos"],
  "pasos_repetidos": ["ssh al VPS y revisar logs del contenedor", "contar SKUs analizados con SQL"],
  "podria_ayudar": [
    {"tipo": "tool", "que": "comando salud-motor que junte logs, conteos y errores", "por_que": "los mismos 4 pasos a mano"}
  ],
  "recursos_usados": ["skill:oc-corrida"],
  "unidades_utiles": ["ku_..."]
}
```

Vocabularios iniciales, sembrados con el backlog actual. El extractor puede proponer `otro:<nombre>`, y la revisión semanal decide si entra como categoría nueva.

- **`trabajo`**: `operar-monitorear`, `responder-cliente`, `analizar-datos`, `validar-resultados`, `desarrollar`, `depurar`, `desplegar-infra`, `documento-cliente`, `revisar-ux`, `relevar-sistema-cliente`, `admin-finanzas`, `investigar-evaluar`, `coordinar-meta`.
- **`fricciones`**: `contexto-perdido`, `acceso-entorno`, `pasos-manuales-repetidos`, `dato-dificil-de-encontrar`, `claude-corregido`, `herramienta-falla`, `esperar-a-otro`, `ninguna`.
- **`resultado`**: `resuelto`, `parcial`, `abandonado`, `en-curso`.
- **`podria_ayudar.tipo`**: `skill`, `tool`, `subagente`, `mcp`, `hook`, `regla`, `app`, `adoptar`, `nada`.

### Qué es un patrón

Una de estas combinaciones, con soporte de 3 sesiones o más (la regla de tres de v1):

- **De secuencia**: el mismo n-grama canónico de acciones.
- **De trabajo**: el mismo `trabajo` con el mismo `objetivo` parecido (embedding) o los mismos `pasos_repetidos`.
- **De fricción**: la misma fricción en el mismo repo, host o herramienta.
- **De corrección**: la misma corrección del usuario en 2 sesiones o más.

Cada patrón guarda soporte (sesiones), personas, minutos medianos, primera y última vez, y estado: `observando → candidato → aprobado → construido → medido` (o `descartado`).

### Analista semanal

Un job programado (o `/capitalizar semana`) corre consultas SQL sobre las dos capas y le pasa el resultado a un Claude analista que escribe el informe. Detectores y la acción que proponen:

| Señal | Acción | Ejemplo con tus datos |
|---|---|---|
| Misma secuencia determinística en 3+ sesiones | **tool** (script o herramienta MCP) | `ssh` + `docker` + SQL → `salud-motor` |
| Mismo trabajo con criterio y receta parecida en 3+ sesiones | **skill** | `responder-cliente` |
| Trabajo con mucha lectura o exploración y compactación alta | **subagente** que devuelve un resumen y cuida el contexto del principal | Relevar el ERP de un cliente: un subagente que devuelve el mapa de tablas |
| La misma consulta a datos una y otra vez | unidades de tipo **dato** + **MCP** con consultas curadas | 2.162 SQL ad hoc a Goldsky |
| La misma corrección en 2+ sesiones | **regla** en CLAUDE.md o criterio inyectable | "Respuestas punteadas y cortas" |
| Una regla que no puede fallar | **hook** que la hace cumplir | Mostrar el borrador antes de enviar un recordatorio de cobro |
| Fricción de acceso repetida | script de entorno | `dev-up`: túnel, clon de la base, app, chequeo |
| Compactación alta en un repo | memoria de trabajo en `PreCompact` (SPEC §10) o partir en subagentes | 121 compactaciones |
| Otra persona del equipo ya lo resuelve mejor | **adoptar** | Ver la matriz de abajo |
| Skill o MCP instalado sin uso en trabajos donde aplica | **adoptar** o desinstalar | Skills con 0 usos |
| Lo necesita alguien sin Claude | **app** | Tablero de salud para el cliente |

Prioridad de cada acción = sesiones × minutos medianos × personas ÷ esfuerzo (S, M o L).

### Difusión: que lo que uno descubre lo capitalice el resto

La **matriz de adopción** cruza personas con capacidades (skills, servidores MCP, tipos de subagente, funciones como plan mode, worktrees, scheduled tasks, artifacts, navegador) y la superpone con los trabajos. Así salen recomendaciones concretas y comparables (ejemplo hipotético):

> Para `relevar-sistema-cliente`, Nico usa un subagente Explore y tarda una mediana de 25 minutos sin compactar. Vos lo hiciste 5 veces esta semana en la sesión principal, con una mediana de 70 minutos y 2 compactaciones. Sesión de ejemplo: …

Reglas para que sea justo: comparar solo con al menos 3 sesiones por lado, por el mismo `trabajo`, y mostrar la sesión ejemplar (resumen y unidades que dejó) para que se pueda copiar el *cómo*, no solo el número.

### Salida y cierre del ciclo

- **Informe personal** (privado): tu semana en 5 líneas y 3 acciones como máximo (construir, adoptar, dejar de hacer).
- **Informe del equipo**: top patrones, matriz de adopción, sesiones ejemplares, estado de la base de conocimiento (nuevas, usadas, viejas) y movimiento del backlog.
- **Backlog con dueño**: cada acción aprobada en la revisión del viernes tiene un responsable. Cuando se construye, se registra como unidad de tipo `recurso` y se empieza a inyectar donde aplica.
- **Medición**: 2 a 4 semanas después, el analista compara el patrón original. ¿Bajaron los minutos de ese trabajo? ¿Desapareció la secuencia manual? Si no, la acción no llegó al momento de uso, y eso también es un hallazgo.

## Modelo de datos

Tablas nuevas en el Postgres de build101, con `pgvector` y `tsvector` en español:

```
repos(team, repo, client)
knowledge_units(id, team, type, title, body, steps jsonb, dead_ends text[], scope, scope_ref,
                triggers jsonb, confidence, status, author, created_at, updated_at,
                last_confirmed, valid_until, supersedes, embedding vector(384), tsv tsvector)
ku_evidence(ku_id, session_id, user_name, ts, kind, cost jsonb)        -- origen | confirma | contradice
ku_events(ku_id, session_id, user_name, ts, event)                     -- recuperada | inyectada | holdout | usada | util | incorrecta | vencida
ku_triggers(ku_id, kind, value)                                        -- índice invertido para PreToolUse
session_facts(team, session_id, user_name, repo, client, started_at, ended_at, prompts,
              tools jsonb, mcp_servers text[], skills text[], subagents jsonb, errors,
              interrupts, compactions, denials, corrections, tokens, cost_usd, sequence text[])
work_cards(team, session_id, episode, trabajo, objetivo, cliente, resultado, fricciones text[],
           pasos_repetidos text[], podria_ayudar jsonb, recursos_usados text[], unidades_utiles text[], created_at)
patterns(id, team, kind, key, label, support, people text[], minutes_median, first_seen, last_seen, status)
pattern_occurrences(pattern_id, session_id, user_name, ts)
actions(id, team, pattern_id, tipo, title, owner, status, created_at, built_at, ku_id, effect jsonb)
```

Embeddings: un modelo multilingüe chico corriendo en el mismo servidor (por ejemplo, de la familia bge-small), para que la búsqueda del hook entre en la latencia y no tenga costo por consulta.

## Privacidad y confianza

- **El transcript crudo nunca sale de la máquina.** Igual que `forward.py` hoy: solo metadata limpia por hooks, y solo JSON destilado y pasado por el scrubber desde el extractor local.
- Nada de secretos, tokens ni datos personales de clientes en las unidades: el extractor lo tiene como regla y el servidor vuelve a pasar el scrubber.
- **El análisis de uso es para capitalizar, no para vigilar.** El informe personal lo ve solo esa persona; el equipo ve agregados, la matriz de adopción y las sesiones ejemplares que su autor marcó como compartibles. Si no se cuida esto, la gente deja de prender los hooks.
- Todo lo que vuelve a una sesión se presenta como dato del equipo, con procedencia, nunca como instrucción.

## Plan por etapas

| Etapa | Qué | Listo cuando |
|---|---|---|
| **E0. Historia y taxonomía** (1 día) | Script local que corre el extractor sobre las 278 sesiones históricas: fichas de trabajo, hechos determinísticos y unidades candidatas | Hay un informe inicial con los 10 patrones principales y 50 unidades revisadas a mano. La taxonomía se ajusta con datos reales antes de fijarla |
| **E1. Base mínima** (1-2 días) | Tablas, `kb_search`, `kb_remember`, `kb_feedback`, `kb_get` en el MCP, `GET /api/kb/brief` y hook de `SessionStart` | Abrir una sesión en goldsky muestra hasta 5 líneas relevantes en menos de 300 ms |
| **E2. Extracción por episodio** (1-2 días) | Reemplaza el evaluador por turno de DIK: prefiltro, extractor con unidades y ficha, deduplicación, resumen diario | Como máximo 5 candidatas por día y persona, con 70 % o más aprobadas |
| **E3. Inyección situacional** (2 días) | `UserPromptSubmit`, `PreToolUse` por host, MCP y archivo, `PostToolUse` por error, holdout | 80 % o más de lo inyectado marcado como útil o neutro |
| **E4. Analista semanal** (1-2 días) | `session_facts`, secuencias, patrones, matriz de adopción, informe y backlog de acciones | Primer informe de equipo con 3 acciones con dueño |
| **E5. Cierre del ciclo** | Medición del efecto de las acciones, vigencia y archivo de unidades | Una acción construida muestra baja de minutos en su patrón |

E0 va primero porque es barato, no depende del servidor y es la mejor manera de validar la taxonomía y el filtro con datos reales en vez de inventarlos.

## Métricas

- **Base de conocimiento**: unidades activas, % de sesiones con al menos una unidad usada, precisión (útil o neutro sobre inyectadas), unidades viejas archivadas por mes.
- **Efecto**: minutos y tokens por trabajo, comparados contra el holdout.
- **Patrones**: acciones construidas por mes, adopción (personas nuevas usando una capacidad), baja de minutos en los patrones atacados, tasa de compactación.

## Decisiones abiertas

1. **¿DIK se funde en build101?** Recomiendo que build101 sea el backend único y que DIK quede como cliente: el extractor local, las skills (`/capitalizar`, `/escucha`) y la instalación de hooks.
2. **Embeddings locales o por API.** Recomiendo locales en el servidor por latencia y costo; se puede cambiar después sin tocar el resto.
3. **Visibilidad del análisis por persona.** Propuesta: privado por defecto, compartible por sesión.
4. **Quién aprueba las unidades de equipo.** Propuesta: el autor, con un clic en el resumen diario; si no lo hace en 3 días, pasa a otra persona del equipo.
5. **Taxonomía de trabajos.** Sembrada desde el backlog actual. Se revisa con los resultados de E0 y de nuevo a las dos semanas.
