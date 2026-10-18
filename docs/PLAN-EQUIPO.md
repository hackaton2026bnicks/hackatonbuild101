# Plan de trabajo: Nico y Molle

Estado al 18 de octubre de 2026. Junta lo que pensó Alberto, la arquitectura de Hermes que Beto ya usa en producción y lo que vimos fallar hoy. Las preguntas abiertas están al final.

Molle se queda con todo lo que guarda y reparte conocimiento: la bóveda con Hermes, los patrones de uso y las skills del equipo. Nico se queda con los hooks que lo conectan a cada sesión, la comunicación entre agentes y el tablero. El pitch lo arman entre los dos.

## Reparto

| Objetivo | Dueño | Apoyo | Entregable |
| --- | --- | --- | --- |
| 1. Base de conocimiento | Molle | Nico (índice y hooks) | Bóveda git con Hermes en la VPS, conectada a cada sesión al inicio y al final |
| 2. Insights y patrones de uso | Molle | Nico (prueba en Windows) | Skill `/insights` hacia atrás y la misma medición al cerrar cada sesión |
| 3. Comunicación entre agentes | Nico | Molle (prueba en Mac) | Tareas limpias, choques por sesión y sesión puente |
| 4. Tablero | Nico | Molle (qué insights importan) | Métricas por persona, patrones de uso y bots animados según la acción |
| 5. Skills compartidas y sugerencias | Molle | Nico (hook del pedido) | Paquete de skills del equipo y aviso en el momento de "esto ya lo hacés siempre" |
| 6. Pitch | Los dos | Beto y Alberto revisan | Guion de 3 minutos, demo en vivo y 3 números propios |

Para no pisarse: Molle toca `agent/hermes/`, la bóveda, el paquete de skills y `tools/`. Nico toca `server/api/`, `agent/claude-code/` y `ui/`. Lo que cruza (cómo el hook le pasa cosas a Hermes y cómo el server lee la bóveda) se acuerda antes por `ask_team`.

## Orden de trabajo

Cada etapa arranca cuando la anterior deja andando lo que la siguiente necesita. Dentro de cada etapa, Nico y Molle trabajan en paralelo.

| Etapa | Molle | Nico | Lista cuando |
| --- | --- | --- | --- |
| **1. Ahora** | Hermes y el sync en la VPS con la plantilla de Beto. Formato de nota, incluida la `receta` | Tareas limpias, choques por sesión, reparar los acentos ya guardados, comando `doctor` | La bóveda se sincroniza sola con GitHub y el estado del equipo se lee bien |
| **2. Conectar la base** | Entrada remota para que el cierre de sesión le mande lecciones al archivista | El server indexa la bóveda; los hooks de inicio, archivo y error leen del índice; `learn.py` manda a Hermes | Algo que Nico corrige aparece en Obsidian y a Molle le llega al tocar ese archivo |
| **3. Medir y sugerir** | Skill `/insights`. Patrones que se repiten guardados como `receta` | Tablero con métricas y oportunidades. Hook del pedido que sugiere recetas | El tablero tiene datos de 3 personas y una sugerencia salió en una sesión real |
| **4. Compartir y mostrar** | Plugin del equipo con skills y hooks | Animaciones de los bots y sesión puente | Alguien instala todo con un comando |
| **5. Pitch** | Relato y slides | Guion de la demo y datos de prueba | Un ensayo con Beto y Alberto sobre prod |

## 1. Base de conocimiento (Molle)

La base es la bóveda git `build101-vault`, que mantiene Hermes. El server no guarda conocimiento propio: solo indexa la bóveda para entregarla rápido dentro de un hook. Esto reemplaza las unidades propias que guarda hoy `server/api/kb.py` en la rama `feat/knowledge-base`.

**La decisión que ordena todo:** el hook de inicio no puede esperar a Hermes. Los hooks cortan a los 4 segundos y el consultor es un modelo que tarda más. Por eso al inicio se busca en el índice, y Hermes queda para escribir y para las preguntas a demanda.

```
GitHub (build101-vault) ── fuente de verdad, historial, revisión humana
        │  sync cada 10 min: pull primero, después push
        ▼
VPS: clon de la bóveda
        ├── contenedor Hermes ── MCP de archivos, solo conocimiento/ y privado/
        │                         reglas/ montada de solo lectura
        └── contenedor api ───── la lee de solo lectura y la indexa
                                  │
                                  ▼
                   hooks de cada sesión (inicio, archivo, error, pedido)
```

| Momento | Qué pasa | Quién lo hace | Tiempo |
| --- | --- | --- | --- |
| Inicio de sesión | Busca en el índice lo relevante para el repo y el primer pedido, y lo inyecta. Si no hay nada, sigue | Hook + server | Menos de 1 s |
| Durante la sesión | Antes de tocar un archivo o después de un error, trae la nota que aplica. A demanda, el agente le pregunta al consultor | Hook + MCP | Rápido, o lento a demanda |
| Fin de sesión, corrida 1 | Saca las lecciones de la sesión (`learn.py`) y se las pasa al archivista, que las guarda como notas enlazadas | `learn.py` → Hermes | En segundo plano |
| Fin de sesión, corrida 2 | Mide la sesión y busca patrones de uso (`session_end.py`). Ver objetivo 2 | `session_end.py` → server | En segundo plano |
| Cada 10 minutos | Sync con GitHub. El server vuelve a indexar | Contenedor de sync | — |

Las trampas que Beto ya pisó y hay que resolver desde el arranque:

- El MCP de archivos ve las carpetas, nunca la raíz del montaje. Si ve la raíz, Hermes puede escribir `scripts/`, que corre como root.
- Rutas absolutas en el prompt de Hermes. Tres rebotes de ruta seguidos tiran el MCP unos 60 segundos.
- Se montan carpetas, no archivos sueltos. Un archivo montado se queda con la versión vieja después de un `git pull`, sin dar error.
- El sync hace `pull --rebase` siempre, antes de fijarse si hay cambios propios.
- Todo archivo es de uid 1000. Si nace de root, Hermes ya no lo puede escribir.
- Solo viaja a GitHub lo que está en una lista de rutas permitidas, para que no se filtren `.env` ni `.mcp.json`.

Tareas:

- [ ] Molle: montar Hermes y el sync en la VPS con la plantilla de Beto
- [ ] Molle: una entrada para que el cierre de sesión le mande lecciones al archivista, como un `guardar` remoto
- [ ] Nico: que el server indexe las notas de la bóveda y que `learn.py` mande a Hermes en vez de al server
- [ ] Nico: que los hooks de inicio, de archivo y de error lean de ese índice
- [ ] Los dos: probar el ciclo completo

## 2. Insights y patrones de uso (Molle)

El análisis hacia atrás ya anda: Nico lo corrió sobre 13 sesiones siguiendo [INSIGHTS-EQUIPO.md](INSIGHTS-EQUIPO.md). Falta una skill que lo haga en un paso y que el resultado termine en la base.

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

- [ ] Molle: la skill en el plugin del equipo (objetivo 5)
- [ ] Nico y Molle: probarla en Windows y en Mac
- [ ] Molle y Beto: correrla y subir sus resultados, para tener 3 personas en el tablero
- [ ] Molle: definir con Alberto qué patrón se guarda como nota y cuál solo se muestra en el tablero

## 3. Comunicación entre agentes y sesiones (Nico)

El canal llega, pero lo que lleva está sucio y no avisa cuando importa. Todo esto se vio el 18 de octubre.

| Qué falla | Qué se vio | Arreglo |
| --- | --- | --- |
| La "tarea" de cada sesión es basura | Una respuesta de Claude, un `<system-reminder>`, un `<agent-message>` de un subagente, "No por ahora" | La tarea sale del primer pedido real: se descartan los textos que inyecta el sistema y las respuestas cortas |
| Acentos rotos desde Windows | "anÃ¡lisis" en estado y mensajes | Resuelto para lo nuevo en el PR #22. Falta reparar lo ya guardado (`fix/repair-mojibake`) |
| Dos sesiones de la misma persona se pisan | Una sesión de Nico le cambió la rama a otra en la misma carpeta, sin aviso | Detectar el choque por sesión y no por persona, y avisar cuando dos sesiones comparten carpeta |
| Identidad confusa | Los commits de Molle salen como "nico" | Tomar el nombre de la key de build101, no de git |
| La persona no se entera | Los mensajes solo le llegan al agente cuando corre un hook. Una sesión quieta no escucha | Sesión puente ([BRIDGE-SESSION.md](BRIDGE-SESSION.md)) |
| No se sabe si los hooks llegan | Beto estuvo horas sin aparecer y nadie lo vio | Un `doctor` que pruebe hook, key, MCP y encoding, y un aviso de "no te veo hace X" |

La sesión puente es una sesión de Claude Code con su propio CLAUDE.md: sabe que su trabajo es comunicarse con el equipo, corre `tools/bridge_listen.py`, y con cada mensaje nuevo decide si contesta, usa el MCP o le avisa a la persona. Después vuelve a escuchar.

- [ ] Nico: tarea limpia y choques por sesión, con tests armados con los textos reales de la tabla
- [ ] Nico: mergear la reparación de acentos y avisar que hay que hacer `git pull` y reiniciar Claude Code
- [ ] Nico: comando `doctor` en `agent/claude-code/`
- [ ] Nico: sesión puente lista para instalar
- [ ] Molle: probar todo en Mac y contar por `ask_team` lo que siga fallando

## 4. Tablero (Nico)

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
- [ ] Molle: revisar con Alberto que esos 4 insights son los que miraría un viernes

## 5. Skills compartidas y sugerencias en el momento (Molle)

Lo que uno construye le tiene que quedar a todo el equipo, y la sesión tiene que avisar cuando estás por repetir algo que ya tiene una forma mejor.

**Paquete del equipo.** Claude Code tiene plugins con marketplace: un repo con skills, comandos, hooks y MCP que cada uno agrega una vez con `/plugin marketplace add` y después actualiza desde ahí. Sirve también para instalar los hooks de build101 sin copiar settings a mano.

**Sugerencia en el momento.** El ejemplo de Alberto es la nota semanal de noticias de IA que hace todos los lunes:

1. Al cerrar cada sesión se detecta la repetición: la misma intención en 3 sesiones o más.
2. Se guarda como `receta` en la bóveda, con cuándo aplica y la forma que mejor funcionó. Si hay una skill, la nota la nombra.
3. Al próximo pedido parecido, el hook del pedido la encuentra en el índice y la sugiere.
4. Si la receta es de otra persona, llega igual.

- [ ] Molle: repo de plugin del equipo con `/insights` y los hooks de build101
- [ ] Molle: formato de `receta`, con `cuando:` (qué pedido la dispara) y `skill:` si existe
- [ ] Nico: hook en `UserPromptSubmit` que sugiera como máximo una receta por sesión
- [ ] Los dos: probarlo con una tarea real que alguno repita

## 6. Pitch (los dos)

Los agentes de un equipo trabajan solos, se pisan y redescubren lo mismo. build101 los conecta, guarda lo que aprenden y mide si el equipo usa mejor a Claude.

1. **El problema (30 s):** algo que nos pasó. Dos sesiones de Nico trabajaron en la misma carpeta y una le cambió la rama a la otra sin que nadie se enterara.
2. **Demo en vivo (90 s):** dos sesiones en el mismo repo.
    - Una va a tocar un archivo que la otra está editando y recibe el aviso.
    - Una pregunta con `ask_team` y el agente de la otra persona contesta.
    - Una se equivoca y le llega la nota que otro guardó.
    - Todo aparece en vivo en el tablero.
3. **Los números (40 s):** tres cifras nuestras, medidas con la herramienta.
4. **Qué sigue (20 s):** la bóveda como memoria del equipo y el análisis que dice qué construir.

## Preguntas abiertas

- **Molle:** ¿Hermes va en la VPS de build101 o en una aparte? ¿La bóveda es una sola para el equipo o una por proyecto?
- **Molle:** ¿Hermes con ChatGPT es la idea final, o lo pasamos a Claude para usar una sola suscripción?
- **Beto:** ¿ya está el repo plantilla de Hermes para pasarle a Molle?
- **Beto y Alberto:** ¿cuándo es el pitch y cuánto dura?
- **Alberto:** ¿el tablero de claude.ai sigue, o lo reemplaza la vista de nuestra app?
