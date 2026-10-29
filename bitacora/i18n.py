"""UI strings in English and Spanish. English is the default."""
from __future__ import annotations

import locale
import os

LANGS = ("en", "es")
_lang = "en"

S: dict[str, tuple[str, str]] = {
    "terminal_help_intro": (
        "Pick a session in the list. The tabs show its activity, messages, files, log, result and details. Footer "
        "shortcuts act on the selected session.",
        "Elige una sesión en la lista. Las pestañas muestran su actividad, mensajes, archivos, log, resultado y "
        "detalles. Los atajos del pie actúan sobre la sesión seleccionada."),
    "terminal_help_questions": (
        "A “? question” status means the agent is waiting for you. Reply in its own window or terminal; Bitácora "
        "cannot answer it.",
        "El estado «? pregunta» indica que el agente espera tu respuesta. Contesta en su ventana o terminal; "
        "Bitácora no puede responder."),
    "terminal_help_questions_title": ("Waiting for an answer", "Esperando una respuesta"),
    "terminal_help_navigation_title": ("Navigation", "Navegación"),
    "terminal_help_monitor_title": ("Monitor", "Monitor"),
    "terminal_help_actions_title": ("Session actions", "Acciones de sesión"),
    "terminal_help_app_title": ("App", "Aplicación"),
    "terminal_help_resume_title": ("Resume a session", "Reanudar una sesión"),
    "terminal_help_key_column": ("Keys", "Teclas"),
    "terminal_help_action_column": ("Action", "Acción"),
    "terminal_help_sort": (
        "Click a table heading to cycle ascending, descending and default order. The palette (Ctrl+P) lists the same "
        "sort commands.",
        "Haz clic en un encabezado de la tabla para alternar orden ascendente, descendente y predeterminado. La "
        "paleta (Ctrl+P) muestra los mismos comandos de orden."),
    "terminal_help_sort_title": ("Table sorting", "Orden de la tabla"),
    "sort_click": ("Click a heading: ascending, descending, default",
                   "Clic en un encabezado: ascendente, descendente, predeterminado"),
    "sort_step": ("Sort by the previous or next column", "Ordenar por la columna anterior o siguiente"),
    "desktop_reader_closed": ("The session reader is closed.", "El lector de sesiones está cerrado."),
    "desktop_invalid_request": ("Invalid request.", "Solicitud inválida."),
    "desktop_preferences_failed": ("Settings could not be saved.", "No se pudo guardar la configuración."),
    "desktop_process_changed": ("The process changed or ended; nothing was stopped.",
                                "El proceso cambió o terminó; no se detuvo nada."),
    "desktop_stop_expired": ("The confirmation expired. Select the session again.",
                             "La confirmación venció. Selecciona la sesión de nuevo."),
    "desktop_file_tail": ("Showing the latest part of this file.", "Se muestra la parte más reciente del archivo."),
    "desktop_file_head": ("Showing the first part of this file.", "Se muestra la primera parte del archivo."),
    "desktop_window_missing": (
        "The desktop app needs bitacora[window] and the Microsoft Edge WebView2 Runtime.",
        "La app de escritorio necesita bitacora[window] y Microsoft Edge WebView2 "
        "Runtime.",
    ),
    "desktop_window_failed": ("Could not open the window: {error}", "No se pudo abrir la ventana: {error}"),
    # statuses
    "st_asking": ("? question", "? pregunta"),
    "st_starting": ("● starting", "● iniciando"),
    "st_running": ("● running", "● en curso"),
    "st_your_turn": ("◌ your turn", "◌ te toca"),
    "st_done": ("✓ done", "✓ terminada"),
    "st_failed": ("✗ failed", "✗ fallida"),
    "st_limited": ("✗ limit reached", "✗ límite alcanzado"),
    "st_stopped": ("✗ stopped", "✗ detenida"),
    "st_quiet": ("◷ quiet", "◷ en silencio"),
    "st_unknown": ("—", "—"),
    # table columns
    "col_status": ("Status", "Estado"),
    "col_agent": ("Agent", "Agente"),
    "col_name": ("Name", "Nombre"),
    "col_now": ("Now", "Ahora"),
    "col_repo": ("Folder", "Carpeta"),
    "col_model": ("Model", "Modelo"),
    "col_mode": ("Mode", "Modo"),
    "col_dur": ("Duration", "Duración"),
    "col_last": ("Last activity", "Última actividad"),
    "col_cmds": ("Cmds", "Cmds"),
    "col_files": ("Files", "Arch."),
    "col_tok": ("Total", "Total"),
    "col_total_help": (
        "Cumulative total; its counting basis depends on the provider.",
        "Total acumulado; la forma de contar depende del proveedor.",
    ),
    "col_ctx": ("Ctx", "Ctx"),
    "col_pid": ("PID", "PID"),
    "provider_claude": ("Claude Code", "Claude Code"),
    "provider_codex": ("Codex", "Codex"),
    "provider_claude_short": ("claude", "claude"),
    "provider_codex_short": ("codex", "codex"),
    "origin_terminal": ("Terminal", "Terminal"),
    "origin_background": ("Background", "Segundo plano"),
    "origin_desktop": ("Desktop app", "App de escritorio"),
    "origin_ide": ("IDE", "IDE"),
    "origin_subagent": ("Subagent", "Subagente"),
    "origin_other": ("Other", "Otro"),
    "origin_short_terminal": ("Terminal", "Terminal"),
    "origin_short_background": ("Background", "Segundo plano"),
    "origin_short_desktop": ("Desktop", "Escritorio"),
    "origin_short_ide": ("IDE", "IDE"),
    "origin_short_subagent": ("Subagent", "Subagente"),
    "origin_short_other": ("Other", "Otro"),
    "sort_by": ("Sort by {column}", "Ordenar por {column}"),
    "sort_ascending": ("sorted ascending", "orden ascendente"),
    "sort_descending": ("sorted descending", "orden descendente"),
    "fcol_kind": ("Kind", "Tipo"),
    "fcol_n": ("Times", "Veces"),
    "fcol_ts": ("Last", "Última"),
    "fcol_path": ("File", "Archivo"),
    # tabs
    "tab_act": ("1 Activity", "1 Actividad"),
    "tab_msg": ("2 Messages", "2 Mensajes"),
    "tab_files": ("3 Files", "3 Archivos"),
    "tab_log": ("4 Log", "4 Log"),
    "tab_res": ("5 Result", "5 Resultado"),
    "tab_info": ("6 Details", "6 Detalles"),
    # key bindings
    "key_quit": ("Quit", "Salir"),
    "key_live": ("Live only", "Solo en vivo"),
    "key_verbose": ("Full detail", "Detalle completo"),
    "key_follow": ("Auto-scroll", "Auto-scroll"),
    "key_maximize": ("Maximize", "Maximizar"),
    "desktop_resize_list": ("Drag to resize the list", "Arrastra para ajustar la altura de la lista"),
    "inf_name": ("Name", "Nombre"),
    "inf_now": ("Now", "Ahora"),
    "key_kill": ("Stop", "Detener"),
    "key_result": ("Result", "Resultado"),
    "key_log": ("Log", "Log"),
    "key_copy": ("Copy resume command", "Copiar comando para reanudar"),
    "key_theme": ("Theme", "Tema"),
    "key_refresh": ("Refresh", "Refrescar"),
    "key_list": ("Back to list", "Ir a la lista"),
    "key_sort_reset": ("Reset sort", "Restablecer orden"),
    "key_jump_question": ("Jump to the oldest question", "Ir a la pregunta más antigua"),
    "ft_move": ("move", "mover"),
    "ft_detail": ("detail", "detalle"),
    "ft_tabs": ("tabs", "pestañas"),
    "ft_list": ("list", "lista"),
    "ft_jump": ("jump", "saltar"),
    "ft_on": ("on", "sí"),
    "ft_off": ("off", "no"),
    "ft_full": ("full screen", "pantalla completa"),
    "win_title": ("Bitácora · {n} running", "Bitácora · {n} en curso"),
    # HUD
    "hud_runs": ("Sessions", "Sesiones"),
    "hud_quota": ("Quota", "Cuota"),
    "hud_selected": ("Selected", "Seleccionada"),
    "hud_now": ("Now", "Ahora"),
    "hud_n_running": ("{n} running", "{n} en curso"),
    "hud_n_ask": ("{n} questions", "{n} preguntas"),
    "hud_n_idle": ("{n} your turn", "{n} te toca"),
    "hud_n_done": ("{n} done", "{n} terminadas"),
    "hud_n_cut": ("{n} stopped", "{n} detenidas"),
    "hud_n_starting": ("{n} starting", "{n} iniciando"),
    "hud_n_quiet": ("{n} quiet", "{n} en silencio"),
    "hud_n_failed": ("{n} failed", "{n} fallidas"),
    "hud_n_limited": ("{n} limit reached", "{n} límites alcanzados"),
    "hud_n_ask_one": ("{n} question", "{n} pregunta"),
    "picker_counts_one": ("{n} session · {live} live", "{n} sesión · {live} en vivo"),
    "n_killed_one": ("Stopped PID {pid} ({n} process).", "Se detuvo el PID {pid} ({n} proceso)."),
    "hud_n_done_one": ("{n} done", "{n} terminada"),
    "hud_n_cut_one": ("{n} stopped", "{n} detenida"),
    "hud_n_failed_one": ("{n} failed", "{n} fallida"),
    "hud_n_limited_one": ("{n} limit reached", "{n} límite alcanzado"),
    "hud_window": ("last {h}h", "últimas {h}h"),
    "hud_live_only": ("live only", "solo en vivo"),
    "hud_none": ("none", "ninguna"),
    "hud_searching": ("looking for sessions…", "buscando sesiones…"),
    "hud_empty": (
        "No sessions in this view · s to choose another",
        "No hay sesiones en esta vista · s para elegir otra",
    ),
    "hud_context": ("context", "contexto"),
    "hud_commands": ("commands", "comandos"),
    "hud_files": ("files touched", "archivos tocados"),
    "quota_unknown": ("quota ?", "cuota ?"),
    "quota_window_unknown": ("?", "?"),
    "quota_value_unknown": ("?", "?"),
    "quota_reset": ("reset", "restablecida"),
    "quota_limit_reset": ("limit reached · reset", "límite alcanzado · se restableció"),
    "quota_five_hour": ("5h", "5h"),
    "quota_week": ("week", "semana"),
    "quota_renews": ("renews {t}", "renueva {t}"),
    "quota_limit": ("limit reached", "límite alcanzado"),
    "quota_limit_when": (
        "limit reached · available again {when}",
        "límite alcanzado · disponible de nuevo {when}",
    ),
    "quota_limit_until": (
        "limit reached · {type} · available again {when}",
        "límite alcanzado · {type} · disponible de nuevo {when}",
    ),
    "quota_limit_type": ("limit reached · {type}", "límite alcanzado · {type}"),
    "quota_week_opus": ("Opus weekly", "semana de Opus"),
    "quota_week_sonnet": ("Sonnet weekly", "semana de Sonnet"),
    "quota_unrecorded": (
        "Claude Code does not record usage totals",
        "Claude Code no registra el consumo total",
    ),
    "act_wait_file": ("waiting for session data…", "esperando los datos de la sesión…"),
    "act_your_turn": ("your turn · last activity {age} ago", "te toca · última actividad hace {age}"),
    "act_no_proc": ("no running process", "sin proceso en ejecución"),
    "act_starting": ("starting…", "arrancando…"),
    "act_thinking": ("thinking", "pensando"),
    "act_running": ("running ", "ejecutando "),
    "act_ran": ("ran ", "ejecutó "),
    "act_edited": ("edited ", "editó "),
    "act_wrote": ("wrote: ", "escribió: "),
    "act_ago": (" · {age} ago", " · hace {age}"),
    # events
    "ev_thinking": ("◇ thinking", "◇ pensando"),
    "ev_steps": ("{n} steps", "{n} pasos"),
    "ev_hidden": ("(reasoning not available)", "(razonamiento no disponible)"),
    "ev_read": ("reads", "lee"),
    "ev_search": ("searches", "busca"),
    "ev_list": ("lists", "lista"),
    "ev_add": ("new", "nuevo"),
    "ev_update": ("edits", "edita"),
    "ev_delete": ("deletes", "borra"),
    "ev_question": (" ? QUESTION ", " ? PREGUNTA "),
    "ev_prompt": ("▶ prompt  ", "▶ prompt  "),
    "ev_turn_started": ("turn started", "turno iniciado"),
    "ev_turn_complete": ("turn complete", "turno completo"),
    "ev_aborted": ("turn aborted ({why})", "turno abortado ({why})"),
    "ev_lines_before": ("… {n} lines before", "… {n} líneas antes"),
    "ev_lines_more": ("… {n} more lines", "… {n} líneas más"),
    "ev_earlier": ("… {n} earlier events not shown", "… {n} eventos anteriores sin mostrar"),
    # detail panes
    "no_session_file": (
        "Session details are not available yet.",
        "Los detalles de la sesión aún no están disponibles.",
    ),
    "no_messages": ("_No messages yet._", "_Sin mensajes todavía._"),
    "no_log": (
        "No log file. Bitácora finds one when the agent output is redirected to a file "
        "(for example > run.log).",
        "Sin archivo de log. Bitácora lo encuentra cuando la salida del agente se "
        "redirige a un archivo (por ejemplo > run.log).",
    ),
    "res_last_msg": (
        "_No result file yet ({out}); this is the last message of the turn._",
        "_Aún no hay archivo de resultado ({out}); este es el último mensaje del turno._",
    ),
    "res_none": (
        "_No result yet. Result file: {out}._",
        "_Aún no hay resultado. Archivo de resultado: {out}._",
    ),
    "res_no_out": ("not set", "sin definir"),
    "no_selection": ("No session selected.", "Sin sesión seleccionada."),
    "not_running": ("(no running process)", "(sin proceso en ejecución)"),
    "inf_status": ("Status", "Estado"),
    "inf_status_source": ("Status source", "Origen del estado"),
    "status_source_process": ("process", "proceso"),
    "status_source_transcript": ("transcript", "transcripción"),
    "desktop_more_actions": ("More actions", "Más acciones"),
    "key_live_short": ("Live", "En vivo"),
    "quota_week_short": ("wk", "sem"),
    "quota_limit_compact": ("limit · {when}", "límite · {when}"),
    "inf_question": ("Open question", "Pregunta abierta"),
    "inf_agent": ("Agent", "Agente"),
    "inf_session": ("Session ID", "ID de sesión"),
    "inf_folder": ("Working folder", "Carpeta de trabajo"),
    "inf_model": ("Model", "Modelo"),
    "inf_effort": ("Reasoning effort", "Esfuerzo de razonamiento"),
    "inf_mode": ("Raw access mode", "Modo de acceso sin procesar"),
    "inf_started": ("Started", "Inicio"),
    "inf_duration": ("Duration", "Duración"),
    "inf_commands": ("Commands", "Comandos"),
    "inf_failed": ("Failed commands", "Comandos fallidos"),
    "inf_files": ("Changed files", "Archivos modificados"),
    "inf_tokens_basis": ("What this total counts", "Qué cuenta este total"),
    "inf_origin_raw": ("Origin (raw)", "Origen (sin procesar)"),
    "inf_effort_source": ("Effort source", "Origen del esfuerzo"),
    "inf_access_kind": ("Access kind", "Tipo de acceso"),
    "inf_context": ("Context used", "Contexto usado"),
    "usage_processed": ("Processed", "Procesados"),
    "usage_reported": ("Reported", "Reportados"),
    "usage_processed_basis": (
        "Cumulative prompt and output tokens across unique Claude Code messages, including cache reads and writes.",
        "Tokens acumulados de entrada y salida en mensajes únicos de Claude Code, incluidos los de lectura "
        "y escritura de caché.",
    ),
    "usage_reported_basis": (
        "Codex's cumulative total_token_usage.total_tokens value as reported by the provider.",
        "El valor acumulado total_token_usage.total_tokens de Codex, según lo informa el proveedor.",
    ),
    "context_window_assumed": ("context window assumed from model", "ventana de contexto inferida del modelo"),
    "effort_source_transcript": ("Transcript", "Transcripción"),
    "effort_source_launch": ("Launch settings", "Configuración de inicio"),
    "effort_source_unknown": ("Unknown", "Desconocido"),
    "access_read_only": ("Read only", "Solo lectura"),
    "access_workspace_write": ("Workspace write", "Escritura en el espacio de trabajo"),
    "access_prompt": ("Prompt", "Confirmación"),
    "access_auto_edit": ("Auto edit", "Edición automática"),
    "access_plan": ("Plan", "Plan"),
    "access_full_access": ("Full access", "Acceso total"),
    "access_other": ("Other", "Otro"),
    "inf_result": ("Result file", "Archivo de resultado"),
    "inf_log": ("Log", "Log"),
    "inf_file": ("Session file", "Archivo de sesión"),
    "inf_pid": ("PID", "PID"),
    "inf_command": ("Command", "Comando"),
    "inf_resume": ("Resume", "Retomar"),
    "inf_reader": ("Reader", "Lector"),
    # notifications
    "n_paused": ("Auto-scroll paused (f to resume)", "Auto-scroll pausado (f para reanudar)"),
    "n_live_on": ("Showing live sessions only", "Mostrando solo sesiones en vivo"),
    "n_live_off": (
        "Showing live sessions and the last {h} h",
        "Mostrando las sesiones en vivo y las de las últimas {h} h",
    ),
    "n_verbose_on": (
        "Full detail: command output, file changes and full prompts",
        "Detalle completo: salida de comandos, cambios en archivos y prompts completos",
    ),
    "n_verbose_off": ("Compact view", "Vista compacta"),
    "n_follow_on": ("Auto-scroll on", "Auto-scroll activado"),
    "n_follow_off": ("Auto-scroll paused", "Auto-scroll pausado"),
    "n_theme": ("Theme: {name}", "Tema: {name}"),
    "n_not_running": (
        "The selected session has no running process.",
        "La sesión seleccionada no tiene un proceso en ejecución.",
    ),
    "n_killed": ("Stopped PID {pid} ({n} processes).", "Se detuvo el PID {pid} ({n} procesos)."),
    "n_kill_failed": ("Could not stop the process: {e}", "No se pudo detener el proceso: {e}"),
    "n_no_file": ("No {what} for this session.", "No hay {what} para esta sesión."),
    "what_result": ("result", "resultado"),
    "what_log": ("log", "log"),
    "n_no_sid": ("This session cannot be resumed yet.", "Esta sesión todavía no se puede reanudar."),
    "n_copied": ("Resume command copied to the clipboard.", "Comando para retomar copiado al portapapeles."),
    "n_copy_failed": ("Could not copy: {e}", "No pude copiar: {e}"),
    "n_done": ("{label} finished", "{label} terminó"),
    "n_stopped": ("{label} stopped", "{label} se detuvo"),
    "n_ask": ("{label} is waiting for an answer", "{label} espera una respuesta"),
    "n_no_question": ("No session is waiting for an answer.", "Ninguna sesión espera una respuesta."),
    "n_config": ("Config error, using defaults: {e}", "Error en la config, uso valores por defecto: {e}"),
    # kill dialog
    "kill_q": (
        "Stop «{label}» (PID {pid}) and the processes it started?",
        "¿Detener «{label}» (PID {pid}) y los procesos que inició?",
    ),
    "kill_yes": ("Yes, stop (y)", "Sí, detener (s)"),
    "kill_no": ("Cancel (n)", "Cancelar (n)"),
    # views
    "view_background": ("Background runs", "En segundo plano"),
    "view_background_d": (
        "sessions started with codex exec, claude -p or an SDK",
        "sesiones iniciadas con codex exec, claude -p o un SDK",
    ),
    "view_everything": ("All", "Todas"),
    "view_everything_d": ("every session from every agent", "todas las sesiones de todos los agentes"),
    "view_interactive": ("Interactive", "Interactivas"),
    "view_interactive_d": ("desktop apps, IDEs and terminal chats", "apps de escritorio, IDEs y chats en terminal"),
    "view_live": ("Live", "En vivo"),
    "view_live_d": ("sessions with a running process", "sesiones con un proceso en ejecución"),
    "view_custom_d": ("from your config.toml", "de tu config.toml"),
    "tagline": ("a logbook for your coding agents", "la bitácora de tus agentes de código"),
    "setup_first": (
        "Choose the view Bitácora opens with. You can change it later with s.",
        "Elige la vista con la que abre Bitácora. Puedes cambiarla después con s.",
    ),
    "setup_title": ("Home", "Inicio"),
    "setup_hint": (
        "↑↓ move · ←→ change option · Enter save · Esc close",
        "↑↓ mover · ←→ cambiar opción · Enter guardar · Esc cerrar",
    ),
    "set_lang": ("Language", "Idioma"),
    "set_hours": ("History window", "Ventana de historial"),
    "set_notify": ("Notifications", "Avisos"),
    "set_theme": ("Theme", "Tema"),
    "lang_auto": ("System", "Sistema"),
    "hours_n": ("last {h} h", "últimas {h} h"),
    "on": ("on", "activados"),
    "off": ("off", "apagados"),
    "key_home": ("Home", "Inicio"),
    "palette": ("palette", "paleta"),
    "cmd_home": ("Home and setup", "Inicio y configuración"),
    "cmd_home_d": ("Views, language, history window, notifications and theme",
                   "Vistas, idioma, ventana de historial, avisos y tema"),
    "cmd_view": ("View", "Vista"),
    "cmd_view_d": ("Show only the sessions this view selects", "Mostrar solo las sesiones de esta vista"),
    "cmd_live": ("Live sessions only", "Solo sesiones en vivo"),
    "cmd_live_d": ("Toggle the live-only filter (a)", "Alternar el filtro de sesiones en vivo (a)"),
    "cmd_verbose": ("Full detail", "Detalle completo"),
    "cmd_verbose_d": ("Command output, diffs and full prompts (v)",
                      "Salida de comandos, diffs y prompts completos (v)"),
    "cmd_follow": ("Auto-scroll", "Auto-scroll"),
    "cmd_follow_d": ("Pause or resume following new events (f)", "Pausar o reanudar el seguimiento de eventos (f)"),
    "cmd_lang": ("Language", "Idioma"),
    "cmd_lang_d": ("Redraws the interface in this language", "Redibuja la interfaz en este idioma"),
    "cmd_sort_column": ("Sort by {column}", "Ordenar por {column}"),
    "cmd_sort_column_d": ("Cycle ascending, descending and default order",
                          "Alternar orden ascendente, descendente y predeterminado"),
    "cmd_sort_reset": ("Reset table sorting", "Restablecer orden de la tabla"),
    "cmd_sort_reset_d": (
        "Return to questions, live sessions and newest starts",
        "Volver a preguntas, sesiones en vivo e inicios recientes",
    ),
    "n_saved": ("Setup saved", "Configuración guardada"),
    "picker_counts": ("{n} sessions · {live} live", "{n} sesiones · {live} en vivo"),
    "picker_found": ("Found: {where}", "Encontré: {where}"),
    "picker_nothing": (
        "No agent sessions found. Bitácora checks ~/.codex and ~/.claude; set "
        "CODEX_HOME or CLAUDE_CONFIG_DIR if your sessions are elsewhere, or try "
        "bitacora --demo.",
        "No se encontraron sesiones de agentes. Bitácora busca en ~/.codex y ~/.claude; "
        "define CODEX_HOME o CLAUDE_CONFIG_DIR si tus sesiones están en otra ruta, o "
        "prueba bitacora --demo.",
    ),
    "picker_loading": ("Reading sessions…", "Leyendo sesiones…"),
    # desktop window
    "desktop_tab_activity": ("Activity", "Actividad"),
    "desktop_tab_messages": ("Messages", "Mensajes"),
    "desktop_tab_files": ("Files", "Archivos"),
    "desktop_tab_log": ("Log", "Log"),
    "desktop_tab_result": ("Result", "Resultado"),
    "desktop_tab_details": ("Details", "Detalles"),
    "desktop_tabs_label": ("Session detail", "Detalle de la sesión"),
    "desktop_open_monitor": ("Open monitor", "Abrir monitor"),
    "desktop_help": ("Help", "Ayuda"),
    "desktop_help_intro": ("Bitácora follows the coding-agent sessions on this computer. Pick a session in the list; "
                           "the tabs below show what it did and the buttons above them act on it.",
                           "Bitácora sigue las sesiones de agentes de código de este equipo. Elige una sesión en la "
                           "lista; las pestañas de abajo muestran qué hizo y los botones de arriba actúan sobre ella."),
    "desktop_help_question": ("Sessions marked “? question” are waiting for an answer; the strip above the list shows "
                              "the oldest first. Bitácora cannot answer them: reply in the agent's own window or "
                              "terminal.",
                              "Las sesiones marcadas «? pregunta» esperan una respuesta; la franja sobre la lista "
                              "muestra primero la más antigua. Bitácora no puede responderlas: contesta en la ventana "
                              "o terminal del propio agente."),
    "desktop_help_copy": ("Copy resume puts the agent's own resume command on the clipboard. Paste it in a terminal "
                          "to continue the session; copying does not reopen or answer anything.",
                          "Copiar reanudar pone en el portapapeles el comando del propio agente para retomar la "
                          "sesión. Pégalo en una terminal para continuar; copiar no reabre ni responde nada."),
    "desktop_help_mouse": ("Everything works with the mouse: click a row, a tab, a switch or a button. Keys are "
                           "shortcuts for the same actions.",
                           "Todo funciona con el ratón: haz clic en una fila, una pestaña, un interruptor o un botón. "
                           "Las teclas son atajos para lo mismo."),
    "desktop_help_keys": ("Keyboard", "Teclado"),
    "desktop_help_scroll": ("scroll the detail", "desplazar el detalle"),
    "desktop_close": ("Close", "Cerrar"),
    "desktop_cancel": ("Cancel", "Cancelar"),
    "desktop_stop": ("Stop", "Detener"),
    "desktop_stop_title": ("Stop this session?", "¿Detener esta sesión?"),
    "desktop_stop_body": (
        "This stops «{label}» (PID {pid}) and the processes it started. Unsaved work "
        "may be lost.",
        "Esto detiene «{label}» (PID {pid}) y los procesos que inició. Lo que no se "
        "haya guardado puede perderse.",
    ),
    "desktop_stop_confirm": ("Stop session", "Detener sesión"),
    "desktop_stop_sent": ("Stop requested.", "Detención solicitada."),
    "desktop_copy_title": ("Resume command", "Comando para retomar"),
    "desktop_copy_manual": ("The clipboard is not available. Select the command and copy it yourself.",
                            "El portapapeles no está disponible. Selecciona el comando y cópialo a mano."),
    "desktop_copy_note": ("Paste it in a terminal to continue the session. Copying does not reopen the agent or "
                          "answer it.",
                          "Pégalo en una terminal para continuar la sesión. Copiar no reabre el agente ni le "
                          "responde."),
    "desktop_copy_again": ("Copy", "Copiar"),
    "desktop_action_failed": ("That action did not work.", "Esa acción no funcionó."),
    "desktop_unavailable": ("Not available for this session", "No disponible para esta sesión"),
    "desktop_reader_ready": ("updated {age} ago", "actualizado hace {age}"),
    "desktop_reader_stale": ("Session data has not refreshed for {age}; the list may be out of date.",
                             "Los datos no se actualizan desde hace {age}; la lista puede estar desactualizada."),
    "desktop_reader_error": ("Sessions could not be read: {error}", "No se pudieron leer las sesiones: {error}"),
    "desktop_reader_partial": ("Some sessions could not be read: {providers}.",
                               "No se pudieron leer algunas sesiones: {providers}."),
    "desktop_reader_short_stale": ("out of date", "desactualizado"),
    "desktop_reader_short_error": ("read error", "error de lectura"),
    "desktop_reader_short_partial": ("partial", "parcial"),
    "desktop_bridge_error": (
        "Cannot read session data. Retrying…",
        "No se pueden leer los datos de las sesiones. Reintentando…",
    ),
    "desktop_detail_loading": ("Loading…", "Cargando…"),
    "desktop_detail_missing": ("This session is no longer available.", "Esta sesión ya no está disponible."),
    "desktop_page_expired": ("This page expired. Load the older messages again.",
                             "Esta página venció. Vuelve a cargar los mensajes anteriores."),
    "desktop_detail_stale": ("detail not refreshed", "detalle sin actualizar"),
    "desktop_empty_live": (
        "No live sessions in this view right now.",
        "Ahora no hay sesiones en vivo en esta vista.",
    ),
    "desktop_show_all": ("Show all", "Mostrar todas"),
    "desktop_change_view": ("Change view", "Cambiar vista"),
    "desktop_waiting_for": ("waiting for {age}", "espera desde hace {age}"),
    "desktop_oldest": ("oldest", "la más antigua"),
    "desktop_question_banner": ("Waiting for an answer:", "Pregunta pendiente:"),
    "desktop_question_hint": ("Answer it in the agent's own window or terminal.",
                              "Respóndela en la ventana o terminal del propio agente."),
    "desktop_font_size": ("Text size", "Tamaño del texto"),
    "desktop_font_n": ("{n} px", "{n} px"),
    "desktop_setup_hint": (
        "Click a view or use ↑↓ · ←→ changes a setting · Enter opens the monitor · Esc "
        "goes back",
        "Haz clic en una vista o usa ↑↓ · ←→ cambia un ajuste · Enter abre el monitor · "
        "Esc vuelve",
    ),
    "desktop_saving_failed": ("Settings could not be saved: {e}", "No se pudo guardar la configuración: {e}"),
    "desktop_plain": ("Plain text", "Texto plano"),
    "desktop_jump_latest": ("Jump to latest message", "Ir al mensaje más reciente"),
    "desktop_earlier_messages": ("… {n} earlier messages not shown", "… {n} mensajes anteriores sin mostrar"),
    "desktop_load_older": ("Load older messages", "Cargar mensajes anteriores"),
    "desktop_files_empty": ("No file changes yet.", "Todavía no hay cambios de archivos."),
    "desktop_prev": ("Previous", "Anterior"),
    "desktop_next": ("Next", "Siguiente"),
    "tui_live_short": ("Live", "En vivo"),
    "tui_window_short": ("{h}h", "{h}h"),
    "tui_view_all": ("All", "Todas"),
    "tui_view_background": ("Background", "Segundo plano"),
    "tui_view_interactive": ("Interactive", "Interactivas"),
    "tui_view_live": ("Live", "En vivo"),
    "tui_quota_limit_short": ("limit {type} \u00b7 {when}", "l\u00edmite {type} \u00b7 {when}"),
    "tui_quota_limit_generic": ("limit reached", "l\u00edmite alcanzado"),
    "tui_quota_renew_short": ("\u21bb {when}", "\u21bb {when}"),
}


def detect() -> str:
    for var in ("BITACORA_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var, "").lower()
        if v:
            return "es" if v.startswith("es") else "en"
    try:
        loc = (locale.getlocale()[0] or "").lower()
    except ValueError:
        loc = ""
    return "es" if loc.startswith(("es", "spanish")) else "en"


def set_lang(lang: str | None) -> str:
    global _lang
    _lang = lang if lang in LANGS else detect()
    return _lang


def lang() -> str:
    return _lang


def t(key: str, **kw) -> str:
    if kw.get("n") == 1 and key + "_one" in S:  # a count of one uses the singular form when there is one
        key += "_one"
    pair = S.get(key)
    if pair is None:
        return key
    text = pair[LANGS.index(_lang)]
    return text.format(**kw) if kw else text
