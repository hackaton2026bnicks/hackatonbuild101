#!/bin/sh
# Corre un script de build101 (agent/claude-code/<script>) desde el clon en $BUILD101_HOME.
# Lo usan los hooks y el MCP del plugin, en cualquier repo. Dentro del propio clon no hace nada:
# ahí .claude/settings.json y .mcp.json ya corren lo mismo y cada evento llegaría dos veces.
# Uso: run.sh forward.py | learn.py | session_end.py | mcp_server.py
[ -n "$BUILD101_HOME" ] && [ -n "$BUILD101_KEY" ] || exit 0
home=$(cd "$BUILD101_HOME" 2>/dev/null && pwd -P) || exit 0
here=$(cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null && pwd -P)
[ "$here" = "$home" ] && exit 0
script="$home/agent/claude-code/$1"
[ -f "$script" ] || exit 0
# En Windows python3 puede ser el atajo de la Store, que existe pero no corre nada
for py in "$BUILD101_PYTHON" python3 python; do
  [ -n "$py" ] && "$py" -c "import sys; sys.exit(sys.version_info < (3, 9))" 2>/dev/null && exec "$py" "$script"
done
exit 0
