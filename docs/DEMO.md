# Demo de build101: guion para grabar

Cuatro minutos, en video, corriendo mientras Beto habla. El video muestra todo el producto con un solo hilo:
dos desarrolladores con sus agentes en el mismo repo, primero sin build101 y después con build101. Lo que
el jurado pruebe con su propio agente queda para las preguntas.

## La historia

Cualquier equipo que trabaja con agentes tiene tres problemas, y ninguno es nuestro en particular:

1. **Cada agente arranca de cero.** Lo que el agente de Ana descubrió ayer, el de Bruno lo vuelve a
   descubrir hoy, con los mismos 3 intentos fallidos.
2. **Los agentes no se ven entre sí.** Dos agentes tocan el mismo archivo, o uno cambia de rama debajo
   del otro, y nadie se entera hasta el conflicto.
3. **Nadie sabe si el equipo usa bien la IA.** No hay forma de ver qué se repite, qué se aprende y qué
   vale la pena construir una vez para todos.

build101 resuelve los tres sin que nadie cambie cómo trabaja: los hooks de Claude Code mandan lo que hace
cada agente y le traen lo que el equipo ya sabe, justo antes de que lo necesite.

## El repo de la demo

Un repo chico y genérico, `tienda-api` (pedidos y pagos), no el nuestro, con dos trampas plantadas:

- **Trampa de conocimiento:** el endpoint de pagos falla con un error engañoso (`401 invalid signature`)
  cuando falta el header `Idempotency-Key`. Al agente le cuesta varios intentos descubrirlo.
- **Trampa de choque:** Ana y Bruno reciben tareas que tocan el mismo archivo, `app/pagos.py`.

Personas: **Ana** (sesión 1) y **Bruno** (sesión 2), cada una con su key. Se graba todo en una sola
máquina, con dos terminales una al lado de la otra y el tablero en la otra mitad de la pantalla.

## El guion (4:00)

Cada bloque dice lo que dice Beto y lo que se ve en el video. En la grabación los tiempos muertos se
cortan: la espera del archivista, el pull de la bóveda y el agente pensando.

| Tiempo | Beto dice | El video muestra |
| --- | --- | --- |
| 0:00 a 0:30 | El problema: cada agente arranca de cero y no ve a los demás | **Sin build101.** El agente de Bruno se traba con el `401`: tres intentos, minutos perdidos. Mientras tanto, Ana edita `app/pagos.py` y Bruno lo pisa. Contador en pantalla: "3 intentos, 6 min" |
| 0:30 a 0:50 | "Instalamos build101: un plugin, un comando" | `claude plugin install build101@build101` y el doctor en verde. El tablero muestra a Ana y Bruno como bots, cada uno con su modelo |
| 0:50 a 1:20 | **Los agentes se ven.** "Ahora Bruno sabe quién más está en ese archivo antes de tocarlo" | Bruno va a editar `app/pagos.py`: su agente recibe "Ana editó este archivo hace 2 min y está en: reembolsos". En el tablero, Files in play marca el archivo con los dos. El agente de Bruno le pregunta al de Ana con `ask_team`, y la sesión puente de Ana muestra la pregunta y manda la respuesta |
| 1:20 a 2:10 | **El equipo aprende.** "Ana se trabó con el 401 y lo resolvió. Esto ya no se pierde" | Ana cierra la sesión. `learn.py` saca la lección del transcript (síntoma, causa, arreglo). Entra en la cola, el archivista la escribe y Obsidian muestra el nodo nuevo en el grafo, enlazado al componente Pagos |
| 2:10 a 2:50 | "Mañana Bruno pide algo parecido, y su agente ya lo sabe" | Bruno: "agregá pagos con tarjeta guardada". Antes de tocar `app/pagos.py`, su agente recibe la nota y lo hace bien al primer intento. Contador: "1 intento, 40 s" contra "3 intentos, 6 min". En la terminal: "el equipo ya tiene una receta para esto" |
| 2:50 a 3:30 | **Se mide.** "¿Sirve de verdad? Lo medimos" | Tablero: "Does the team learn?" (notas entregadas, % de sesiones que recibieron alguna, lecciones descartadas con su motivo) y el holdout. "How the team works with Claude" (L, K, F) y "What to build once for everyone": lo que encontraron 2 personas o más en su `/insights` |
| 3:30 a 4:00 | Dónde la IA es irremplazable, y el cierre | Las tres piezas de IA resaltadas en el diagrama, y el cierre: "Cada sesión que cierra deja al equipo más rápido" |

## Dónde la IA es irremplazable (para que Beto lo diga explícito)

- **Sacar la lección de una sesión desordenada.** Un transcript tiene cientos de pasos. Separar "el error
  real era el header que faltaba" de los intentos fallidos es entender, no buscar texto.
- **Curar la memoria.** El archivista decide si algo le sirve a otro, lo une con lo que ya había, lo enlaza
  y descarta el ruido con motivo. Sin eso, la bóveda se vuelve un basurero.
- **Aplicar la nota al caso nuevo.** La nota habla de pagos con `Idempotency-Key`; el agente de Bruno la
  adapta a tarjetas guardadas.

Y dónde **no** usamos IA, a propósito: avisar de un choque o encontrar la nota que aplica es determinístico
y tarda milisegundos. Un hook tiene 4 segundos y no puede fallar.

## Control de calidad (20% de la rúbrica)

- **Holdout:** al 10% de las sesiones a las que una nota les aplicaba no se les muestra. Comparamos
  intentos y errores con nota y sin nota.
- **Descartes con motivo:** el archivista rechaza lo que no sirve y el tablero lo muestra.
- **Cada nota llega una vez por sesión**, marcada como dato del equipo y no como orden.

## Qué hay que preparar

1. El repo `tienda-api` con las dos trampas, y las tareas de Ana y Bruno escritas de antemano.
2. Dos keys (ana, bruno) y una bóveda limpia para la demo, o una carpeta `demo/` dentro de la bóveda.
3. La bandeja corriendo en la máquina que graba: `BANDEJA_ARCHIVISTA=claude BANDEJA_INTERVAL=10`.
4. Obsidian abierto con la vista de grafo.
5. Grabar cada bloque por separado, con dos tomas buenas de cada uno, y editar con los contadores.

## Para las preguntas

- Que el jurado se sume con su agente: un QR con los dos comandos de instalación y una key temporal.
- Si preguntan "¿y si la nota está mal?": el holdout, el `[!warning] Contradicción` del archivista y que
  cualquiera puede editar la nota en Obsidian.
- Si preguntan por la privacidad: del transcript solo salen las lecciones, sin secretos. El contenido
  de los archivos nunca sale de la máquina.
