# Plugin build101

Todo lo que una sesión de Claude Code necesita para trabajar con el equipo, en cualquier repo y con un solo comando:

- **Hooks**: cada sesión le cuenta al equipo en qué está, recibe los mensajes del equipo y las notas de la bóveda que aplican (al iniciar, al pedir algo, antes de tocar un archivo, después de un error), y al cerrar deja sus lecciones y su medición.
- **MCP `build101`**: `share_discovery`, `ask_team`, `answer_team`, `team_status` y `team_knowledge`.
- **Skills**: `/insights` analiza tus sesiones y las suma al tablero y a la bóveda. `/puente` deja una sesión escuchando al equipo.

## Instalar

Una vez por máquina:

1. Cloná hackatonbuild101 y apuntá `BUILD101_HOME` al clon. Pedile tu key a Nico.

   ```sh
   # Mac / Linux, en ~/.zshrc
   export BUILD101_HOME=~/Repos/hackatonbuild101
   export BUILD101_KEY=<tu key>
   ```

   En Windows, con barras normales: `setx BUILD101_HOME C:/Users/vos/code/hackatonbuild101` y `setx BUILD101_KEY <tu key>`.

2. Abrí una terminal nueva y, en Claude Code:

   ```
   /plugin marketplace add hackaton2026bnicks/hackatonbuild101
   /plugin install build101@build101
   ```

3. Reiniciá Claude Code y probá el cable con `python3 "$BUILD101_HOME/agent/claude-code/doctor.py"`.

Para actualizar: `git pull` en el clon (los scripts corren desde ahí) y `/plugin marketplace update build101`.

## Cómo funciona

Los hooks y el MCP del plugin llaman a `scripts/run.sh`, que corre el script de `agent/claude-code/` del clon con el
Python que encuentre (`BUILD101_PYTHON`, `python3` o `python`). Si falta `BUILD101_HOME` o `BUILD101_KEY`, no hace
nada y la sesión sigue normal. Dentro del propio clon de hackatonbuild101 tampoco: ahí `.claude/settings.json` y
`.mcp.json` ya corren lo mismo. Por eso, en ese repo, el MCP del plugin aparece apagado en `/mcp`.
