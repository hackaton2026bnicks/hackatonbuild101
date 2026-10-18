# Plan de trabajo: Nico y Molle

Estado al 18 de octubre de 2026. Junta lo que pensó Beto y lo que vimos fallar hoy.

Molle se queda con todo lo que guarda y reparte conocimiento: la bóveda con Hermes, los patrones de uso y las skills del equipo. Nico se queda con lo que conecta eso a cada sesión, la comunicación entre agentes y el tablero.

## Quién se encarga de qué

| Objetivo | Dueño | Apoyo | Entregable |
| --- | --- | --- | --- |
| 1. Base de conocimiento | Molle (bóveda y Hermes) · Nico (conexión con las sesiones) | — | Cada sesión recibe al inicio lo que aplica y, al cerrar, le deja sus lecciones a la bóveda |
| 2. Insights y patrones de uso | Molle | Nico (prueba en Windows) | Skill `/insights` hacia atrás y la misma medición al cerrar cada sesión |
| 3. Comunicación entre agentes | Nico | Molle (prueba en Mac) | Tareas limpias, choques por sesión y sesión puente |
| 4. Tablero | Nico | Molle (qué insights importan) | Métricas por persona, patrones de uso y bots animados según la acción |
| 5. Skills compartidas y sugerencias | Molle | Nico (hook del pedido) | Paquete de skills del equipo y aviso en el momento de "esto ya lo hacés siempre" |

Para no pisarse: Molle toca `agent/hermes/`, la bóveda, el paquete de skills y `tools/`. Nico toca `server/api/`, `agent/claude-code/` y `ui/`. Lo que cruza (cómo el cierre de sesión le pasa lecciones a Hermes) se acuerda antes por `ask_team`.

## Orden de trabajo

Dentro de cada etapa, Nico y Molle trabajan en paralelo.

| Etapa | Molle | Nico | Lista cuando |
| --- | --- | --- | --- |
| **1. Ahora** | Darle a Nico acceso de lectura a la bóveda. Entrada para que el cierre de sesión le mande lecciones al archivista | Tareas limpias, choques por sesión, reparar los acentos ya guardados, comando `doctor` | El estado del equipo se lee bien y la bóveda acepta lecciones de afuera |
| **2. Conectar la base** | Skill `/insights` | El server indexa la bóveda; los hooks de inicio, archivo y error leen del índice; `learn.py` manda las lecciones a Hermes | Algo que Nico corrige aparece en Obsidian y a Molle le llega al tocar ese archivo |
| **3. Medir y sugerir** | Patrones que se repiten guardados como `receta` | Tablero con métricas y oportunidades. Hook del pedido que sugiere recetas | El tablero tiene datos de 3 personas y una sugerencia salió en una sesión real |
| **4. Compartir y mostrar** | Plugin del equipo con skills y hooks | Animaciones de los bots y sesión puente | Alguien instala todo con un comando |

## 1. Base de conocimiento

**Molle:** la bóveda `build101-vault` y los agentes de Hermes que la mantienen ([agent/hermes/](../agent/hermes/README.md)). **Nico:** conectarla a cada sesión de Claude Code.

La bóveda es la fuente. El server no guarda conocimiento propio: solo indexa las notas de `conocimiento/` para entregarlas rápido dentro de un hook. Esto reemplaza las unidades propias que guarda hoy `server/api/kb.py` en la rama `feat/knowledge-base`.

**La decisión que ordena todo:** el hook de inicio no puede esperar a Hermes. Los hooks cortan a los 4 segundos y el consultor es un modelo que tarda más. Por eso al inicio se busca en el índice, y Hermes queda para escribir y para las preguntas a demanda.

| Momento | Qué pasa | Quién lo hace |
| --- | --- | --- |
| Inicio de sesión | Busca en el índice lo relevante para el repo y el primer pedido, y lo inyecta. Si no hay nada, sigue | Hook + server (Nico) |
| Durante la sesión | Antes de tocar un archivo o después de un error, trae la nota cuyo `aplica_cuando` coincide. A demanda, el agente le pregunta al consultor | Hook + MCP (Nico) |
| Fin de sesión, corrida 1 | Saca las lecciones de la sesión (`learn.py`) y se las pasa al archivista, que las guarda como notas enlazadas | `learn.py` (Nico) → Hermes (Molle) |
| Fin de sesión, corrida 2 | Mide la sesión y busca patrones de uso (`session_end.py`). Ver objetivo 2 | `session_end.py` → server |

Lo que el índice lee de cada nota: `tipo`, `tags`, `autor`, `actualizado`, `aplica_cuando` (en `gotcha` y `receta`, es el disparador) y los enlaces `[[Título]]`. El título es el nombre del archivo.

- [ ] Molle: darle a Nico y al server acceso de lectura a `build101-vault`
- [ ] Molle: una entrada para que el cierre de sesión le mande lecciones al archivista, como un `guardar` remoto
- [x] Nico: que el server lea la bóveda, la actualice cada 10 minutos e indexe `conocimiento/` (`server/api/vault.py`, con la deploy key de solo lectura que vive en el EC2)
- [x] Nico: que los hooks de inicio, de pedido, de archivo y de error lean de ese índice, más la tool `team_knowledge` del MCP
- [x] Nico: que `learn.py` mande las lecciones a la cola del server (`POST /lessons`), de donde las levanta `agent/hermes/bandeja.py` de Molle; los `share_discovery` también entran
- [ ] Nico y Molle: probar el ciclo completo

## 2. Insights y patrones de uso

**Molle.** Nico la prueba en Windows.

El análisis hacia atrás ya anda: Nico lo corrió sobre 13 sesiones siguiendo [INSIGHTS-EQUIPO.md](INSIGHTS-EQUIPO.md). Falta una skill que lo haga en un paso y que el resultado termine en la bóveda.

Lo que se trabó en esa corrida, y que la skill tiene que resolver:

- En Windows no existe `python3`, y el glob de fichas con rutas `/c/Users/...` no encuentra nada.
- El tablero de claude.ai pide permiso de editor aparte, y Beto pidió el resultado en `.md`.
- Con 13 sesiones, solo un tema llega al mínimo de 3 sesiones. Con poca historia, el análisis dice poco.

Qué hace la skill `/insights <nombre>`:

1. Corre los pasos 1 a 4 de la guía: hechos, lotes, fichas con un subagente chico e indicadores.
2. Escribe los hallazgos y se los muestra a la persona.
3. Saca nombres de proyectos, clientes y datos personales si la persona lo pide.
4. Exporta con `tools/insights_export.py` y genera también el `.md`.
5. Con el ok, sube: las oportunidades y estrellas como notas de la bóveda, y los números al server para el tablero.

**Hacia adelante:** la misma medición corre sola al cerrar cada sesión (corrida 2). Calcula horas, llamados a skills y tools, tokens y los seis números de [DIK-INSIGHTS.md](DIK-INSIGHTS.md). Cuando un patrón aparece en 3 sesiones o más, se guarda como `receta` en la bóveda, para que el objetivo 5 lo pueda sugerir.

- [ ] Molle: la skill, dentro del plugin del equipo (objetivo 5)
- [ ] Nico: probarla en Windows
- [ ] Molle y Beto: correrla y subir sus resultados, para tener 3 personas en el tablero
- [ ] Molle: definir con Beto qué patrón se guarda como nota y cuál solo se muestra en el tablero

## 3. Comunicación entre agentes y sesiones

**Nico.** Molle lo prueba en Mac.

El canal llega, pero lo que lleva está sucio y no avisa cuando importa. Todo esto se vio el 18 de octubre.

| Qué falla | Qué se vio | Arreglo |
| --- | --- | --- |
| La "tarea" de cada sesión es basura | Una respuesta de Claude, un `<system-reminder>`, un `<agent-message>` de un subagente, "No por ahora" | La tarea sale del primer pedido real: se descartan los textos que inyecta el sistema y las respuestas cortas |
| Acentos rotos desde Windows | "anÃ¡lisis" en estado y mensajes | Resuelto: lo nuevo en el PR #22 y lo ya guardado con `./infra/keys.sh repair-text --apply` (19 valores, 18 de octubre) |
| Dos sesiones de la misma persona se pisan | Una sesión de Nico le cambió la rama a otra en la misma carpeta, sin aviso | Detectar el choque por sesión y no por persona, y avisar cuando dos sesiones comparten carpeta |
| Identidad confusa | Los commits de Molle salen como "nico" | Tomar el nombre de la key de build101, no de git |
| La persona no se entera | Los mensajes solo le llegan al agente cuando corre un hook. Una sesión quieta no escucha | Sesión puente ([BRIDGE-SESSION.md](BRIDGE-SESSION.md)) |
| No se sabe si los hooks llegan | Beto estuvo horas sin aparecer y nadie lo vio | Un `doctor` que pruebe hook, key, MCP y encoding, y un aviso de "no te veo hace X" |

La sesión puente es una sesión de Claude Code con su propio CLAUDE.md: sabe que su trabajo es comunicarse con el equipo, corre `tools/bridge_listen.py`, y con cada mensaje nuevo decide si contesta, usa el MCP o le avisa a la persona. Después vuelve a escuchar.

- [x] Nico: tarea limpia (el primer pedido real; se descartan `<…>`, `&lt;…`, "No por ahora" y respuestas cortas) y `./infra/keys.sh repair-tasks` para recalcular las ya guardadas. Rama `fix/etapa1-signals`
- [x] Nico: aviso cuando dos sesiones de la misma persona comparten carpeta, al iniciar y antes de un `git checkout`/`switch`/`stash`/`pull`. Rama `fix/etapa1-signals`
- [x] Nico: reparar los acentos ya guardados (hecho en producción)
- [ ] Nico: avisar al equipo que hagan `git pull`, reinicien Claude Code y corran `doctor`
- [x] Nico: comando `doctor` en `agent/claude-code/`. Rama `fix/etapa1-signals`
- [ ] Nico: sesión puente lista para instalar
- [ ] Molle: probar todo en Mac y contar por `ask_team` lo que siga fallando

## 4. Tablero

**Nico.** Molle revisa con Beto qué insights importan.

El tablero contesta tres preguntas, y cada indicador se queda solo si ayuda a contestar una. Va en nuestra app (`ui/index.html`).

| Pregunta | Indicador | De dónde sale | Ya está |
| --- | --- | --- | --- |
| ¿Qué pasa ahora? | Quién trabaja en qué, con su tarea limpia | Hooks | Sí, con tareas sucias |
| | Preguntas sin contestar y tiempo hasta la respuesta | Mensajes | Sí |
| | Choques de archivos evitados | Avisos de solapamiento | No |
| ¿El equipo aprende? | Notas en la bóveda y % de sesiones que usaron al menos una | Índice de la bóveda | No |
| | Lo inyectado que se marcó útil, contra el holdout | Índice | No |
| ¿Usamos bien a Claude? | Horas, skills, tools y tokens por persona | Hooks | Parcial |
| | K (capitalización) y F (fricción) por persona y por trabajo, con la tendencia de 4 semanas | Hooks + `/insights` | Parcial |
| | Oportunidades que comparten 2 personas o más | `/insights` | No |
| | Sesiones estrella con el *cómo* para copiar | `/insights` | No |

Costo en tokens (C) y apalancamiento (L) van al detalle de cada persona: sin F y R al lado no dicen si Claude trabaja o da vueltas.

De los 10 insights del catálogo de [DIK-INSIGHTS.md](DIK-INSIGHTS.md), van a la vista principal los 4 que terminan en algo para construir: repetición determinística (tool), repetición con criterio (skill), capacidad sin adoptar (adoptar) y riesgo (hook).

**Para la demo:** los bots ya cambian de estado según la acción. Falta una animación y un ícono por estado, y un efecto cuando un agente le contesta a otro.

- [ ] Nico: endpoint que reciba el `resultado.json` de `/insights` por persona y período
- [ ] Nico: secciones "¿El equipo aprende?" y "Oportunidades compartidas"
- [ ] Nico: animaciones de los bots
- [ ] Molle: revisar con Beto que esos 4 insights son los que miraría un viernes

## 5. Skills compartidas y sugerencias en el momento

**Molle.** Nico hace el hook que sugiere.

Lo que uno construye le tiene que quedar a todo el equipo, y la sesión tiene que avisar cuando estás por repetir algo que ya tiene una forma mejor.

**Paquete del equipo.** Claude Code tiene plugins con marketplace: un repo con skills, comandos, hooks y MCP que cada uno agrega una vez con `/plugin marketplace add` y después actualiza desde ahí. Sirve también para instalar los hooks de build101 sin copiar settings a mano.

**Sugerencia en el momento.** El ejemplo de Beto es la nota semanal de noticias de IA que hace todos los lunes:

1. Al cerrar cada sesión se detecta la repetición: la misma intención en 3 sesiones o más.
2. Se guarda como `receta` en la bóveda, con `aplica_cuando` y la forma que mejor funcionó. Si hay una skill, la nota la nombra.
3. Al próximo pedido parecido, el hook del pedido la encuentra en el índice y la sugiere.
4. Si la receta es de otra persona, llega igual.

- [ ] Molle: repo de plugin del equipo con `/insights` y los hooks de build101
- [ ] Molle: sumar a la `receta` un campo `skill:` cuando ya existe una
- [ ] Nico: hook en `UserPromptSubmit` que sugiera como máximo una receta por sesión
- [ ] Nico y Molle: probarlo con una tarea real que alguno repita
