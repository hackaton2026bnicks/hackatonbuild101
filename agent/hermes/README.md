# Hermes: archivista y consultor de la bóveda del equipo

Dos agentes de [Hermes](https://github.com/NousResearch/hermes-agent) que mantienen
[build101-vault](https://github.com/hackaton2026bnicks/build101-vault), una bóveda de
Obsidian con lo que el equipo aprende en la hackathon. Corren con la suscripción de
ChatGPT (proveedor `openai-codex`), sin API key.

- **archivista**: le pasás información y la guarda en notas Markdown enlazadas.
  Decide dónde va cada cosa, actualiza lo que ya existe y mantiene los índices.
- **consultor**: le preguntás y responde con lo que dice la bóveda, para tu caso,
  citando nota y autor. No escribe.

La bóveda es un repo aparte: Obsidian solo muestra una carpeta de markdowns, y git
es lo que la comparte. Es independiente de la KB del servidor (`server/api/kb.py`),
pero usa los mismos tipos de unidad que [docs/DIK-V2.md](../../docs/DIK-V2.md).

## Instalar

```sh
# 1. Hermes
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
source ~/.zshrc

# 2. La bóveda, al lado de este repo
git clone https://github.com/hackaton2026bnicks/build101-vault.git ../build101-vault

# 3. Los agentes
agent/hermes/setup.sh
echo 'export B101_NAME=<tu nombre corto>' >> ~/.zshrc
```

`setup.sh` hace el login con ChatGPT (si ya usás Codex CLI, toma `~/.codex/auth.json`),
crea los dos perfiles apuntando a la bóveda, copia sus `SOUL.md` y deja dos comandos.
Si la bóveda está en otro lado: `VAULT=/ruta/a/la/boveda agent/hermes/setup.sh`.

Para verla: instalá [Obsidian](https://obsidian.md) y abrí `build101-vault` con
"Abrir carpeta como bóveda".

## Usar

```sh
guardar "El forwarder falla en Python 3.9 por la sintaxis str | None; hay que usar Optional"
pbpaste | guardar
preguntar "¿qué tengo que saber antes de tocar el forwarder?"
```

`guardar` trae lo último del equipo, le pasa el texto al archivista y commitea y sube
lo que escribió. Solo sube `conocimiento/` y `privado/`, así nada más se puede colar. `preguntar` trae lo último y le pregunta al consultor. Si editás
notas a mano en Obsidian, subilas con git (o con el plugin Obsidian Git).

`archivista chat` y `consultor chat` abren una charla de ida y vuelta, pero no
sincronizan: hacé `git pull` antes y commit y push después.

## Bandeja: lecciones que dejan las sesiones

Al cerrar una sesión, `agent/claude-code/learn.py` saca de 0 a 3 lecciones y las deja
en la cola del server (`POST /api/lessons`). Hermes no corre en todas las máquinas, así
que alguien con Hermes y la bóveda deja corriendo la bandeja:

```sh
python3 agent/hermes/bandeja.py          # una vuelta cada 2 minutos (BANDEJA_INTERVAL)
python3 agent/hermes/bandeja.py --once   # una vuelta y sale
```

Por cada lección pendiente le pasa al archivista el texto con su autor, sube lo que
escribió (como `guardar`) y la marca en el server con la nota que quedó, o como
descartada con el motivo. Si el archivista falla, la reintenta; a los 3 intentos la
descarta. Usa `BUILD101_KEY` y `BUILD101_URL`, igual que los hooks.

## Cambiar el comportamiento

Editá `archivista/SOUL.md` o `consultor/SOUL.md` y volvé a correr `setup.sh`.
Las reglas que leen los dos (carpetas, tipos, formato, qué no entra) están en
`reglas/AGENTS.md` de la bóveda: esa carpeta la editan solo humanos. Otro modelo: `MODEL=gpt-5.6-sol agent/hermes/setup.sh`.

Que el consultor no escriba lo dice su `SOUL.md`; Hermes no se lo impide.
