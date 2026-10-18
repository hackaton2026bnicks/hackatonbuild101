#!/usr/bin/env bash
# Crea los perfiles "archivista" y "consultor" de Hermes sobre la bóveda del equipo
# (repo build101-vault, clonado al lado de este repo), usando la suscripción de ChatGPT
# (proveedor openai-codex) en vez de una API key.
# Correr después de instalar Hermes y clonar la bóveda. Se puede volver a correr sin problema.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
VAULT="${VAULT:-$(dirname "$REPO")/build101-vault}"
# gpt-5.5 no se acepta con cuentas de ChatGPT; gpt-6.1-sol es el que usa Codex CLI
MODEL="${MODEL:-gpt-6.1-sol}"
BIN="$HOME/.local/bin"

command -v hermes >/dev/null || {
  echo "Falta hermes. Instalalo con: curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
  exit 1
}
[ -f "$VAULT/reglas/AGENTS.md" ] || {
  echo "No encuentro la bóveda en $VAULT. Clonala ahí:"
  echo "  git clone https://github.com/hackaton2026bnicks/build101-vault.git \"$VAULT\""
  exit 1
}

# 1. Login con la suscripción de ChatGPT. La sesión OAuth de Codex vive en ~/.hermes/auth.json
#    y la comparten todos los perfiles, así que alcanza con hacerlo una vez.
if ! hermes auth list 2>/dev/null | grep -qi codex; then
  echo "==> Login con tu cuenta de ChatGPT (te muestra un link y un código)"
  hermes auth add openai-codex
fi

for p in archivista consultor; do
  echo "==> Perfil $p"
  hermes profile list 2>/dev/null | grep -qw "$p" || hermes profile create "$p"
  hermes -p "$p" config set model.provider openai-codex
  hermes -p "$p" config set model.default "$MODEL"
  hermes -p "$p" config set terminal.backend local
  hermes -p "$p" config set terminal.cwd "$VAULT"
  cp "$HERE/$p/SOUL.md" "$HOME/.hermes/profiles/$p/SOUL.md"
done

# 2. Atajos de una línea. Los dos traen lo último del equipo antes de correr;
#    guardar además commitea y sube lo que escribió el archivista.
#    El nombre sale de B101_NAME, después de git, después del usuario del sistema.
mkdir -p "$BIN"
cat > "$BIN/guardar" <<EOF
#!/usr/bin/env bash
# guardar "texto"   o   pbpaste | guardar
set -e
VAULT="$VAULT"
EOF
cat >> "$BIN/guardar" <<'EOF'
who="${B101_NAME:-$(git config user.name || true)}"; who="${who:-$USER}"
msg="${*:-$(cat)}"
git -C "$VAULT" pull --rebase --autostash -q || echo "(no pude traer cambios del equipo, sigo con lo local)"
hermes -p archivista -z "Autor: $who
$msg"
# Solo viajan las carpetas que escribe el archivista: nada más puede colarse en el commit.
git -C "$VAULT" add -- conocimiento privado
if ! git -C "$VAULT" diff --cached --quiet; then
  git -C "$VAULT" -c user.name="$who" commit -q -m "$who: $(printf '%s' "$msg" | head -c 60 | tr '\n' ' ')"
  git -C "$VAULT" push -q || echo "(no pude subir; queda commiteado local, se sube en el próximo guardar)"
fi
EOF
cat > "$BIN/preguntar" <<EOF
#!/usr/bin/env bash
# preguntar "¿por qué falla el forwarder en Python 3.9?"
VAULT="$VAULT"
EOF
cat >> "$BIN/preguntar" <<'EOF'
who="${B101_NAME:-$(git config user.name || true)}"; who="${who:-$USER}"
git -C "$VAULT" pull --rebase --autostash -q 2>/dev/null || true
exec hermes -p consultor -z "Pregunta: $who
$*"
EOF
chmod +x "$BIN/guardar" "$BIN/preguntar"

echo
echo "Listo. Bóveda: $VAULT (abrila en Obsidian con 'Abrir carpeta como bóveda')"
echo "Poné tu nombre corto en ~/.zshrc:  export B101_NAME=nico"
echo "Probá:"
echo "  guardar \"Los tests de server/api se saltean si no está TEST_DATABASE_URL\""
echo "  preguntar \"¿cómo corro los tests del server?\""
echo "Para charlar: archivista chat   /   consultor chat"
