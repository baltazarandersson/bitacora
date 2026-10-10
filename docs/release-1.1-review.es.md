# Bitácora 1.1 — candidato para revisión

Nombre propuesto: **Bitácora 1.1 — Faro**. El release todavía no fue etiquetado ni publicado.

## Alcance confirmado

- App de escritorio para Windows, con una única entrada pública `bitacora.exe`.
- TUI desde el paquete Python o el repositorio en Windows, Linux y macOS; sin un EXE CLI público adicional.
- Núcleo Python compartido. La nueva presentación conserva el faro, el cielo por hora local, el mar de código y las paletas del proyecto.
- Todas las funciones actuales: actividad, mensajes, archivos, log, resultado, detalles, cuotas, vistas, configuración y acciones. La información técnica permanece accesible en una segunda capa.

## Decisiones implementadas

La app usa HTML/CSS/JavaScript integrados en pywebview y un puente Python limitado. Ya no sirve una TUI sobre HTTP/WebSocket. Las acciones identifican sesiones por claves del servicio; no aceptan rutas o PIDs arbitrarios. La detención exige confirmación de un solo uso, vencimiento y verificación de la hora de creación del proceso.

El monitor muestra preguntas pendientes y accesos directos a ellas. La cabecera se adapta sin un control de plegado y conserva la vista y la ventana de historial. La ventana mínima del escritorio es de 800×560; el panel de la sesión seleccionada se compacta antes de perder la lista. Detener permanece visible y las acciones secundarias pasan al menú `⋯` cuando falta espacio. Estado y Nombre permanecen fijos al desplazar la tabla horizontalmente; la barra de desplazamiento queda visible. Los encabezados son botones accesibles con teclado que anuncian el orden. En el escritorio, `r` y `F5` actualizan los datos y `Ctrl+R` restablece el orden. Inicio y Ayuda no dependen de memorizar atajos. El tamaño del texto es configurable, con fuentes locales y fallback deliberado. Ambas interfaces conservan un pie que cabe en el ancho disponible y muestra Maximizar y Ayuda en tamaños pequeños.

La TUI conserva su navegación por filas con teclado. Ambas tablas mantienen las catorce columnas y el desplazamiento horizontal; Detalles muestra toda la información técnica, incluido el origen del estado en curso (proceso o transcripción). Ambas listas ordenan primero las preguntas pendientes, luego las sesiones vivas y después los inicios más recientes. Un encabezado alterna entre ascendente, descendente y predeterminado; `<` y `>` recorren las columnas y `Ctrl+R` restablece el orden en ambas interfaces. La paleta `Ctrl+P` también ofrece esos comandos en la TUI. `w` lleva a la pregunta más antigua y `m` alterna Texto plano en Mensajes y Resultado. El subtítulo del faro queda fuera de la escena y la escena se reduce en terminales bajas para dar espacio a los ajustes.

Al cerrar en Windows, el lector se detiene y se une antes de salir. Si WebView2 todavía bloquea su perfil privado, un ayudante interno valida el marcador de propiedad, espera la salida de Bitácora y elimina solo ese perfil. Antes de borrar, el ayudante detiene únicamente los procesos de WebView2 de ese perfil; si alguno sigue vivo, deja el perfil intacto e informa `ProfileBusy`, en vez de borrar archivos a medias. El ayudante usa la misma entrada privada del EXE, sin agregar otro ejecutable público.

`python -m bitacora` inicia la TUI por diseño; el EXE de Windows abre el escritorio. Desde la raíz del repositorio en PowerShell, prueba el escritorio candidato con `.\dist\bitacora.exe --demo --view everything --lang es`, la TUI con `.\.venv\Scripts\python.exe -m bitacora --demo --view everything --lang es` y la versión con `.\.venv\Scripts\python.exe -m bitacora --version`. Para ejecutar la app de escritorio desde fuente: `.\.venv\Scripts\python.exe scripts\app_entry.py --demo --view everything --lang es`. Un `bitacora` global puede resolver a otra instalación.

Ambas interfaces leen sesiones mediante un proceso separado. Se corrigieron sincronización de eventos mutables, generaciones tras reescrituras, asociaciones Codex ambiguas y cachés ante reutilización de PID. Los errores transitorios reintentan y la salud del lector es visible. La detección incremental usa identidad, hashes y muestras; no promete detectar modificaciones adversariales que mantengan metadatos y todas las muestras.

La cuota semanal de un modelo Claude no desaparece por una respuesta de otro modelo. La migración persiste memoria antes de eliminar la copia antigua y conserva datos si falla la escritura. La demo mantiene activas sus sesiones sintéticas sin asociarlas a procesos reales.

El mínimo de Textual es 8.2.8, que se comprueba junto con Python 3.11. El workflow de release valida tag/versión, ejecuta la matriz de CI y espera ambos artefactos y el smoke del EXE. PyPI espera además a la creación de la release de GitHub.

## Verificación

Las verificaciones locales del candidato se registran en el informe de precommit. Los conteos de pruebas y los tamaños cambian al reconstruir: consulta `dist/SHA256SUMS.txt` para identificar los artefactos locales, sin confundirlos con los nuevos builds de auditoría. El único ejecutable público es `bitacora.exe`; el wheel y el sdist mantienen la interfaz de terminal. No hubo commit, tag ni push.

El smoke oculto del EXE exige `ok`, `functional_ok`, dieciséis corridas, dieciséis filas, lector listo y `cleanup: complete`. Comprueba selección, pestañas, orden, restablecimiento con Ctrl+R, orden con `<`/`>`, apertura y cierre del menú `⋯` y ajuste del layout con sesiones sintéticas. Las capturas de ambas interfaces usan datos de demo; la demo actual contiene dieciséis corridas. Los resultados locales no sustituyen la matriz remota ni la revisión del escalado real en los equipos de destino.

La escena del escritorio envía cada fila como tramos de estilo con una paleta por cuadro, en lugar de un objeto por celda: a 240 columnas el cuadro baja de unos 262 KB a 68 KB y su preparación de unos 31 ms a 10 ms. El ciclo pide cuadros a ritmo fijo de 66 ms sin superponer llamadas, y una respuesta que no llega en 2 segundos ya no detiene la escena.

La matriz remota de CI para Windows, Linux y macOS sigue pendiente; esta revisión local en Windows no la sustituye. El escalado real de pantalla y la primera advertencia de SmartScreen también requieren revisión en los equipos de destino.

## Límites del producto

- Los formatos de sesión no tienen un contrato público estable; una asociación incierta debe quedar sin afirmar.
- Copiar reanudación entrega un comando para la terminal; no abre ni responde automáticamente al agente.
- Las cuotas muestran lo registrado en las sesiones observadas, no una consulta independiente a la cuenta.
- Las sesiones de subagentes siguen sin agruparse bajo su tarea madre.
- La app de escritorio no promete soporte Linux/macOS en 1.1.

El próximo paso es revisar el candidato local. La publicación y el tag quedan pendientes de esa revisión y del resultado de CI.
