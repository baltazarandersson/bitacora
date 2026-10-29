# Bitácora

[English](https://github.com/baltazarandersson/bitacora/blob/main/README.md) · **Español**

Un monitor local para agentes de código. Sigue corridas en segundo plano y sesiones interactivas de Codex y Claude Code.

Bitácora tiene una app de escritorio para Windows y una interfaz de terminal para Windows, Linux y macOS. Ambas comparten el lector de sesiones y mantienen actividad, mensajes, archivos, logs, resultados, cuotas y configuración.

![App de escritorio de Bitácora con sesiones de la demo en loop: corridas en vivo, salida de comandos y cambios en archivos](https://raw.githubusercontent.com/baltazarandersson/bitacora/main/docs/readme-es.gif)

## Por qué

Cuando le delegas trabajo a agentes en segundo plano, aparecen preguntas que los agentes no te van a responder:

- **¿Hizo lo que dice?** Una corrida termina con "tests pasan". Bitácora muestra los comandos que ejecutó con su código de salida y los archivos que editó, así auditas la traza y no el resumen.
- **¿Sigue dentro de su ventana de contexto?** Las corridas largas se compactan o se cortan antes de fallar. Cada corrida muestra el tamaño de su última llamada al modelo frente a su ventana.
- **¿Me está esperando?** Una corrida que pregunta a las 2 a. m. queda parada hasta que la notes. Las preguntas suben arriba y disparan un aviso.
- **¿Cuál de las cinco se atascó?** Un orquestador que lanza corridas en paralelo te deja cinco procesos y cinco prompts. Bitácora empareja cada proceso con su sesión y marca las que dejaron de avanzar.
- **¿Llegó a un límite?** Ves el estado de la sesión y la ventana de cuota, no un archivo de resultado truncado.
- **¿Es seguro detenerla?** Detener verifica que el proceso siga siendo el de esa sesión, así un PID reutilizado nunca mata otra cosa.

Bitácora lee los archivos que los agentes ya escriben. No lanza agentes ni busca en tu historial: es el panel que dejas abierto mientras tus agentes trabajan.

## Instalación

```bash
uvx bitacora
```

Si prefieres dejarlo instalado: `pipx install bitacora` o `uv tool install bitacora`. Necesitas Python 3.11 o más nuevo.

Para probarlo sin tener ningún agente instalado:

```bash
uvx bitacora --demo
```

El build de Windows de 1.2 distribuye un único ejecutable público, `bitacora.exe`, que abre la app de escritorio sin Python ni consola. Puedes descargarlo desde la [última página de releases](https://github.com/baltazarandersson/bitacora/releases/latest). Requiere Microsoft Edge WebView2 Runtime. No está firmado, así que SmartScreen podría avisarte la primera vez.

Si instalas con Python, agrega `pip install "bitacora[window]"` y ejecuta `bitacora --window`. El escritorio de 1.2 apunta a Windows; la terminal sigue disponible en los tres sistemas.

## Qué muestra

- Cada sesión con su estado: pregunta, iniciando, en curso, en silencio, te toca, terminada, fallida, límite alcanzado, detenida o desconocida.
- La actividad de cada sesión: comandos con su código de salida, archivos editados con líneas agregadas y quitadas, mensajes, y cuánto pensó el agente antes de cada paso.
- Pestañas para los mensajes, los archivos tocados, el log de la corrida, su archivo de resultado y todos los datos de la sesión.
- El contexto de la llamada más reciente al modelo, como porcentaje cuando se conoce la ventana y, si no, como cantidad de tokens. Total es acumulativo: Claude Code cuenta los tokens procesados de prompt y salida, incluido el uso de caché; Codex muestra el total informado por el agente. Las cuotas muestran el porcentaje de uso o el estado restablecido, y los datos desconocidos siguen como desconocidos.
- Un aviso cuando una corrida termina, se corta o te pregunta algo.
- Acciones sobre la corrida seleccionada: detenerla (te pide confirmación), abrir su resultado o su log (los formatos de texto admitidos se abren en la aplicación del sistema), copiar el comando que la retoma.

Un turno interactivo completado muestra te toca incluso después de cerrar su proceso; un turno sin terminar y sin actividad reciente muestra en silencio. Las preguntas siguen pendientes mientras haya un proceso en ejecución o durante seis horas desde la última actividad de la transcripción. Monokai es el tema predeterminado.

## Uso

```bash
bitacora                      # sesiones en vivo y las últimas 24 horas
bitacora --view background    # arranca en una vista y saltea la pantalla de inicio
bitacora --live               # solo sesiones en vivo
bitacora --hours 72           # una ventana de historial más larga
bitacora --lang en            # interfaz en inglés
bitacora --window             # app de escritorio Windows (dependencias opcionales)
bitacora --window --demo      # app de escritorio con sesiones generadas
bitacora --diagnostics        # contadores, latencias y memoria locales en JSON, y sale
```

![Interfaz de terminal con sesiones generadas de la demo](https://raw.githubusercontent.com/baltazarandersson/bitacora/main/docs/screenshot-es.svg)

La primera vez, Bitácora abre su pantalla de inicio: un faro que barre con su luz un mar de código que cae, bajo un cielo que sigue tu hora local, con una fase lunar aproximada de noche y el sol cruzando de día. Cada vista aparece con la cantidad de sesiones que mostraría con los datos de tu propia máquina, así sabes qué estás eligiendo antes de elegirlo. Puedes volver a esa pantalla cuando quieras con `s` para cambiar la vista, el idioma, la ventana de historial, los avisos o el tema. La cabecera se adapta al ancho disponible. En terminales muy bajas, la línea de cuota se oculta automáticamente; su información completa permanece en Detalles. La escena de inicio se reduce antes de que los ajustes necesiten desplazarse.

![Pantalla de inicio con conteos sintéticos de la demo, ajustes y un faro sobre código que cae](https://raw.githubusercontent.com/baltazarandersson/bitacora/main/docs/home-es.svg)

![App de escritorio de Windows con sesiones generadas de la demo](https://raw.githubusercontent.com/baltazarandersson/bitacora/main/docs/desktop-es.jpg)

En la app de escritorio, las preguntas tienen un acceso directo sobre la lista de sesiones. Selecciona una sesión para usar sus botones Resultado, Log, Copiar comando para reanudar y Detener, o recorrer las seis pestañas. Detener permanece visible; las acciones secundarias pasan al menú `⋯` cuando falta espacio. La ventana mínima es de 800×560 y el panel de la sesión seleccionada se compacta para conservar la lista en tamaños pequeños. Ayuda muestra los atajos. La configuración también permite cambiar el tamaño del texto. `r` y `F5` actualizan los datos del escritorio; `Ctrl+R` restablece el orden. Los encabezados son botones accesibles con teclado que alternan entre orden ascendente, descendente y predeterminado, y anuncian la dirección. Estado y Nombre permanecen fijos al desplazar la tabla horizontalmente, y las etiquetas completas siguen disponibles en ayudas emergentes. El faro usa la misma escena de la terminal, dibujada directamente en la ventana. Copiar comando para reanudar copia un comando para una terminal; no abre ni responde al agente automáticamente.

Ambas interfaces ordenan primero por preguntas, luego por sesiones en vivo y después por las más recientes. Ambas tablas conservan sus catorce columnas con desplazamiento horizontal. Detalles conserva toda la información técnica, incluido si el estado de una sesión en curso viene de un proceso o de la transcripción. `m` alterna Texto plano en Mensajes y Resultado en ambas interfaces.

| Tecla | Acción |
| --- | --- |
| `↑` `↓` | Moverse entre sesiones |
| `Enter` / `Esc` | Entrar al panel de detalle / volver a la lista |
| `1`–`6` | Actividad, Mensajes, Archivos, Log, Resultado, Detalles |
| `PgUp` `PgDn`, `Shift+↑↓` | Desplazar el detalle desde cualquier lado |
| `v` | Detalle completo: salida de comandos, cambios en archivos y prompts completos |
| `a` | Solo sesiones en vivo |
| `r` | Actualizar los datos de las sesiones |
| `f` | Pausar o reanudar el auto-scroll |
| `s` | Inicio: vistas y preferencias |
| `m` | Texto plano en Mensajes y Resultado |
| `w` | Ir a la pregunta más antigua |
| `g` | Maximizar la lista; pulsa de nuevo para volver a la sesión seleccionada y los paneles de detalle |
| `Ctrl+P` | Paleta de la terminal: cambiar de vista o idioma, ordenar columnas, alternar filtros |
| `Ctrl+R` | Restablecer la tabla al orden predeterminado |
| Clic en un encabezado | Ordenar ascendente, luego descendente, y luego volver al orden predeterminado |
| `<` `>` | Ordenar por la columna anterior o siguiente, ascendente |
| `?` / `F1` | Ayuda |
| `k` | Detener la corrida seleccionada y sus hijos (pide confirmación) |
| `o` / `l` | Abrir el archivo de resultado / el log |
| `c` | Copiar comando para reanudar |
| `t` | Siguiente tema |
| `q` | Salir |

### Vistas

| Vista | Muestra |
| --- | --- |
| En segundo plano | Sesiones iniciadas con `codex exec`, `claude -p` o un SDK |
| Todas | Todas las sesiones de todos los agentes |
| Interactivas | Apps de escritorio, IDEs y chats en terminal |
| En vivo | Sesiones con un proceso en ejecución, actividad reciente en la transcripción o una pregunta dentro de esa ventana de seis horas |

Puedes sumar las tuyas en `config.toml`. `bitacora --paths` te dice dónde está ese archivo.

```toml
lang = "es"          # en, es, o no lo pongas para seguir al sistema
hours = 24           # ventana de historial, hasta 72 horas; las sesiones en vivo aparecen sin importar su antigüedad
notify = true        # aviso y campana cuando una corrida termina, se corta o pregunta

[views.codex-bg]
name = "Codex en segundo plano"
providers = ["codex"]          # codex, claude
kind = "background"            # background, interactive o all
live_only = false
```

Lo que guardas en la pantalla de configuración tiene prioridad sobre `config.toml`, y los flags de la línea de comandos tienen prioridad sobre los dos.

## Cómo funciona

Bitácora solo lee archivos que los agentes ya escriben.

- **Codex**: los rollouts en `~/.codex/sessions` (o `$CODEX_HOME`), los nombres de los threads en `session_index.jsonl`, y los procesos `codex exec` / `codex` con sus flags (`-m`, `-c`, `-C`, `-s`, `-o`, `resume <id>`).
- **Claude Code**: las transcripciones en `~/.claude/projects` (o `$CLAUDE_CONFIG_DIR`) y los procesos `claude` con `--resume`, `--session-id`, `--model`, `-p`.

Cada proceso vivo se empareja con su sesión por id cuando la línea de comandos lo trae, y si no, por carpeta de trabajo y hora de inicio. El log sale de una redirección `> run.log` en la línea de comandos del shell padre. Bitácora guarda el nombre, el resultado y el log de cada sesión en su propio archivo, así una corrida terminada los sigue mostrando.

Ambas interfaces leen las sesiones en un proceso aparte. La app de escritorio usa HTML, CSS y JavaScript locales con un puente acotado a Python; no ejecuta un servidor de terminal. Los errores del lector y los datos desactualizados se muestran, y los cambios de eventos se sincronizan sin duplicar la actividad.

## Privacidad

Todo queda en tu máquina. Bitácora no hace pedidos a servicios externos. `bitacora --diagnostics` imprime contadores agregados, latencias y uso de memoria de la máquina local y sale; no incluye texto de mensajes, comandos ni rutas, y no envía nada por la red. La interfaz de escritorio y sus recursos se cargan localmente, sin servidor de aplicación HTTP ni WebSocket. Nunca escribe en los archivos de los agentes. Detener un agente requiere confirmación y verifica que la identidad del proceso siga coincidiendo. Sus archivos guardan tus preferencias, nombres de sesiones, rutas y líneas de comandos observadas; `bitacora --paths` te muestra dónde.
## Límites

- Ninguno de los dos formatos está documentado, y los dos cambian entre versiones. Esta versión se probó con Codex CLI 0.161 y Claude Code 2.1.
- La matriz de CI cubre la terminal en Windows, Linux y macOS con Python 3.11 y 3.13. El build de Windows también tiene una prueba de arranque de la app empaquetada con sesiones generadas. El escritorio en Linux/macOS queda fuera del soporte de 1.2.
- El razonamiento se guarda cifrado o no se guarda, así que la actividad muestra cuánto pensó el agente y no qué pensó (Codex incluye resúmenes si `model_reasoning_summary` está activo).
- La ventana de contexto de Claude Code se infiere de los IDs de modelo conocidos y se marca como supuesta en Detalles. Los modelos desconocidos muestran la cantidad de tokens de contexto en vez de un porcentaje.
- Las sesiones de subagentes todavía no se agrupan bajo su sesión madre.

## Desarrollo

```bash
git clone https://github.com/baltazarandersson/bitacora
cd bitacora
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # .venv\Scripts\pip en Windows
pytest
ruff check .
python -m bitacora --demo
python scripts/screenshot.py es    # regenera docs/*.svg a partir de la demo
```

El release valida que el tag coincida con la versión del paquete y espera la matriz de tests y los artefactos del paquete y Windows antes de publicar. Usa `python scripts/app_entry.py --demo` para desarrollar la interfaz de escritorio; ese comando requiere las dependencias opcionales de ventana.

Sumar un agente es escribir un provider en `bitacora/providers/`: dónde guarda sus sesiones, cómo se ven sus procesos y cómo convertir sus registros en eventos. El generador de la demo en `bitacora/demo.py` también sirve como fixture de los tests.

## Licencia

MIT. Bitácora es un proyecto independiente, sin relación con OpenAI ni Anthropic.
