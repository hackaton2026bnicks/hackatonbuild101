# Guía de uso de build101

build101 conecta tus sesiones de Claude Code con las del resto del equipo: cada agente sabe en qué están
los demás, recibe lo que el equipo ya aprendió cuando le sirve, y deja lo que aprende para el próximo.
Esta guía va de la instalación al uso diario. El detalle técnico de cada pieza está en los enlaces.

## 1. Instalar (una vez por máquina)

1. Cloná `hackatonbuild101` y pedile tu key a Nico.
2. Definí tres variables y abrí una terminal nueva:

   ```sh
   # Mac / Linux, en ~/.zshrc
   export BUILD101_HOME=~/Repos/hackatonbuild101
   export BUILD101_KEY=b101_...
   export BUILD101_PYTHON=python3
   ```

   En Windows: `setx BUILD101_HOME C:/Users/vos/code/hackatonbuild101` y `setx BUILD101_KEY b101_...`.

3. Instalá el plugin del equipo:
   - En Claude Code de terminal: `/plugin marketplace add hackaton2026bnicks/hackatonbuild101` y después
     `/plugin install build101@build101`.
   - En la extensión de VS Code `/plugin` no existe. Desde una terminal:
     `claude plugin marketplace add hackaton2026bnicks/hackatonbuild101 && claude plugin install build101@build101`.

4. Reiniciá Claude Code y corré el doctor:

   ```sh
   python3 "$BUILD101_HOME/agent/claude-code/doctor.py"
   ```

   Revisa la key, el server, los hooks, los acentos, el MCP, la bóveda y si el server vio tus sesiones.
   Cada problema viene con su arreglo.

Para actualizar: `git pull` en el clon y `/plugin marketplace update build101` (o
`claude plugin marketplace update build101 && claude plugin update build101@build101`).

El plugin trae los hooks, el MCP y las skills ([plugin/README.md](../plugin/README.md)). Dentro del propio
clon no hace falta: `.claude/settings.json` y `.mcp.json` ya hacen lo mismo.

## 2. Qué pasa mientras trabajás

No tenés que hacer nada distinto. Los hooks avisan al server y le traen al agente lo que le sirve, en el
momento justo. Todo llega marcado `[build101 · ...]` y el agente lo trata como dato, no como orden.

| Momento | Qué recibe tu agente |
| --- | --- |
| Abrís una sesión | Quién más trabaja en el repo y en qué, los últimos mensajes del equipo y los criterios de la bóveda |
| Pedís algo | Si el equipo ya tiene una **receta** para eso, se la pasa al agente y vos ves en la terminal "el equipo ya tiene una receta para esto" (y la skill, si hay una). También los gotchas que aplican al pedido |
| Antes de tocar un archivo | Las notas de la bóveda que lo citan. Si otra sesión lo editó en los últimos 15 minutos, quién es y en qué está. No se bloquea nada |
| Después de un error | El gotcha que lo describe y quién del equipo trabajó con ese host hace poco |
| Lanzás un subagente | El contexto de la sesión que lo lanzó |
| Dos sesiones tuyas en la misma carpeta | Un aviso antes de cambiar de rama, hacer `pull` o `stash` |
| Cerrás o compactás | Se mide la sesión, se buscan sus lecciones y se mandan a la bóveda (ver 4) |

Cada nota llega una sola vez por sesión. Un 10% de las veces una nota que aplicaba se guarda a propósito
sin mostrarse, para medir si sirven.

## 3. Hablar con el equipo

Tu agente tiene estas tools del MCP `build101`. Podés pedírselas con tus palabras ("preguntale a Molle...").

| Tool | Para qué |
| --- | --- |
| `share_discovery` | Contar algo que aprendiste. `discovery` (por defecto) también va a la bóveda. `note` es para avisos de estado ("ya está desplegado") que no se guardan. `alert` es para algo urgente |
| `ask_team` | Preguntar algo que otro puede saber. El agente sigue trabajando y la respuesta llega en un turno siguiente |
| `answer_team` | Contestar una pregunta que te llegó como `[build101 · question #N]` |
| `team_status` | Quién trabaja en el repo, en qué archivos, y las preguntas sin contestar |
| `team_knowledge` | Buscar en la bóveda o leer una nota entera |

**Sesión puente.** Una sesión que trabaja recibe mensajes solo cuando corre un hook. Para enterarte aunque
estés quieto, abrí otra sesión y escribí `/puente <tu nombre>`. Queda escuchando, te avisa lo que te importa
y te propone respuestas, que manda solo con tu ok. Diseño en [BRIDGE-SESSION.md](BRIDGE-SESSION.md).

## 4. La bóveda: cómo aprende el equipo

La memoria del equipo es [build101-vault](https://github.com/hackaton2026bnicks/build101-vault), una bóveda
de Obsidian. Para verla, cloná ese repo y abrilo en Obsidian con "Abrir carpeta como bóveda".

```
tu sesión ──cierre / share_discovery──> cola del server (/api/lessons)
                                              │
                          bandeja.py + archivista de Hermes (Mac de Molle)
                                              │
                                   build101-vault (Obsidian)
                                              │ cada 10 minutos
                               el server indexa conocimiento/
                                              │
cualquier sesión <── la nota que aplica (inicio, pedido, archivo, error)
```

- **Qué entra solo:** al cerrar o compactar una sesión, [learn.py](../agent/claude-code/learn.py) busca
  errores resueltos, correcciones y pedidos de "acordate", y propone hasta 3 lecciones. Cada
  `share_discovery` también entra. Lo que se repite en 3 sesiones o más se guarda como receta.
- **Quién decide:** el archivista. Une lo repetido, enlaza las notas y descarta lo que no le sirve a nadie.
- **Para guardar algo a mano:** pedile a tu agente que lo comparta con `share_discovery`, o escribí la nota
  en Obsidian y subila con git. Las reglas de cómo es una nota están en `reglas/AGENTS.md` de la bóveda.
- **Para que una nota llegue sola:** que tenga `aplica_cuando` con las palabras del síntoma o del error, y
  las rutas del repo entre backticks. Una receta con `skill: <nombre>` hace que se sugiera esa skill.

Para apagar las lecciones automáticas: `BUILD101_LEARN=0`. Nada del transcript sale de tu máquina: solo
las lecciones, limpias de secretos.

## 5. Medir cómo usás Claude: /insights

`/insights <tu nombre>` analiza tus sesiones (horas, cuánto rinde cada pedido, cuánto pasa por cosas
reutilizables, fricción) y encuentra oportunidades y sesiones para copiar. Tarda entre 15 y 30 minutos.
Antes de subir te muestra todo y podés sacar lo que no quieras compartir. Con tu ok, los números van al
tablero y los hallazgos a la bóveda. Método en [INSIGHTS-EQUIPO.md](INSIGHTS-EQUIPO.md) y
[DIK-INSIGHTS.md](DIK-INSIGHTS.md).

Además, cada sesión se mide sola cuando la cerrás.

## 6. El tablero

https://54-89-124-87.sslip.io/, con tu `BUILD101_KEY`.

| Sección | Qué contesta |
| --- | --- |
| Bots y "Play-by-play" | Qué está haciendo cada agente ahora, en palabras |
| How the team works with Claude | Sesiones, tiempo activo, acciones por pedido (L), uso de lo reutilizable (K), fricción (F) y patrones, a partir de los hooks |
| Does the team learn? | Notas en la bóveda, % de sesiones que recibieron alguna, notas más entregadas y lecciones guardadas |
| What to build once for everyone | Lo que encontraron 2 personas o más en sus /insights, las sesiones estrella y una fila por persona |
| Files in play y Team thread | Qué archivos se están tocando y los mensajes del equipo. Podés contestar preguntas desde ahí |

## 7. Si algo no anda

| Síntoma | Qué hacer |
| --- | --- |
| No aparecés en el tablero | Corré el doctor. Casi siempre falta `BUILD101_KEY` en la terminal desde la que abrís Claude Code |
| Acentos rotos ("anÃ¡lisis") | `git pull`: está arreglado desde el PR #22 |
| No te llega ninguna nota | Normal si nada aplica. Probá `team_knowledge` con una palabra, y mirá en el doctor si el server tiene la bóveda conectada |
| Un error 502 o un timeout de unos segundos | El server se está redesplegando. Reintentá |
| `/plugin` no existe | Estás en la extensión de VS Code: usá `claude plugin ...` desde una terminal |

Si sigue sin andar, preguntá con `ask_team` desde tu agente.
