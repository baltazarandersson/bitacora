"""UI strings in English and Spanish. English is the default."""
from __future__ import annotations

import locale
import os

LANGS = ("en", "es")
_lang = "en"

S: dict[str, tuple[str, str]] = {
    # statuses
    "st_ask": ("? question", "? pregunta"),
    "st_starting": ("● starting", "● iniciando"),
    "st_running": ("● running", "● corriendo"),
    "st_idle": ("◌ your turn", "◌ te toca"),
    "st_active": ("● active", "● activa"),
    "st_done": ("✓ done", "✓ terminada"),
    "st_aborted": ("✗ aborted", "✗ abortada"),
    "st_interrupted": ("✗ cut off", "✗ cortada"),
    "st_unknown": ("?", "?"),
    # table columns
    "col_status": ("Status", "Estado"),
    "col_agent": ("Agent", "Agente"),
    "col_name": ("Name", "Nombre"),
    "col_repo": ("Folder", "Carpeta"),
    "col_model": ("Model", "Modelo"),
    "col_mode": ("Mode", "Modo"),
    "col_dur": ("Duration", "Duración"),
    "col_last": ("Last", "Últ. act."),
    "col_cmds": ("Cmds", "Cmds"),
    "col_files": ("Files", "Arch."),
    "col_tok": ("Tokens", "Tokens"),
    "col_ctx": ("Ctx", "Ctx"),
    "col_pid": ("PID", "PID"),
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
    "tab_info": ("6 Info", "6 Info"),
    # key bindings
    "key_quit": ("Quit", "Salir"),
    "key_live": ("Live only", "Solo vivas"),
    "key_verbose": ("Detail", "Detalle"),
    "key_follow": ("Auto-scroll", "Auto-scroll"),
    "key_kill": ("Kill", "Matar"),
    "key_result": ("Result", "Resultado"),
    "key_log": ("Log", "Log"),
    "key_copy": ("Copy resume", "Copiar resume"),
    "key_theme": ("Theme", "Tema"),
    "key_refresh": ("Refresh", "Refrescar"),
    "key_list": ("Back to list", "Ir a la lista"),
    # HUD
    "hud_runs": ("Sessions", "Sesiones"),
    "hud_quota": ("Quota", "Cuota"),
    "hud_selected": ("Selected", "Seleccionada"),
    "hud_usage": ("Usage", "Consumo"),
    "hud_now": ("Now", "Ahora"),
    "hud_n_running": ("{n} running", "{n} corriendo"),
    "hud_n_ask": ("{n} waiting for an answer", "{n} esperan respuesta"),
    "hud_n_idle": ("{n} waiting for you", "{n} te esperan"),
    "hud_n_done": ("{n} done", "{n} terminadas"),
    "hud_n_cut": ("{n} cut off", "{n} cortadas"),
    "hud_view": ("view", "vista"),
    "hud_window": ("last {h}h", "últimas {h}h"),
    "hud_live_only": ("live only", "solo vivas"),
    "hud_verbose": ("full detail", "detalle completo"),
    "hud_paused": ("auto-scroll paused", "auto-scroll pausado"),
    "hud_none": ("none", "ninguna"),
    "hud_searching": ("looking for sessions…", "buscando sesiones…"),
    "hud_empty": ("nothing in this view · p to change it", "nada en esta vista · p para cambiarla"),
    "hud_folder": ("folder", "carpeta"),
    "hud_model": ("model", "modelo"),
    "hud_duration": ("duration", "duración"),
    "hud_tokens": ("tokens", "tokens"),
    "hud_context": ("context", "contexto"),
    "hud_commands": ("commands", "comandos"),
    "hud_failed": ("({n} failed)", "({n} fallaron)"),
    "hud_files": ("files touched", "archivos tocados"),
    "quota_unknown": ("quota ?", "cuota ?"),
    "quota_week": ("week", "semana"),
    "quota_renews": ("renews {t}", "renueva {t}"),
    # activity line
    "act_wait_file": ("waiting for the session file…", "esperando el archivo de sesión…"),
    "act_wait_answer": ("waiting for an answer to a question", "esperando respuesta a una pregunta"),
    "act_your_turn": ("waiting for you · last event {age} ago", "te toca · último evento hace {age}"),
    "act_no_proc": ("no process · last event {age} ago", "sin proceso · último evento hace {age}"),
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
    "ev_hidden": ("  (reasoning not stored)", "  (razonamiento no guardado)"),
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
    "no_session_file": ("I could not find this process's session file yet.",
                        "Todavía no encontré el archivo de sesión de este proceso."),
    "no_messages": ("_No messages yet._", "_Sin mensajes todavía._"),
    "no_log": ("No log file found (a `> run.log` redirect in the parent shell).",
               "No encontré archivo de log (una redirección `> run.log` en el shell padre)."),
    "res_last_msg": ("_(last message of the turn; {out} does not exist yet)_",
                     "_(último mensaje del turno; {out} no existe aún)_"),
    "res_none": ("_No result yet_ ({out})", "_Sin resultado todavía_ ({out})"),
    "res_no_out": ("no -o", "sin -o"),
    "no_selection": ("No session selected.", "Sin sesión seleccionada."),
    "not_running": ("(not running)", "(no corre)"),
    # info pane
    "inf_status": ("Status", "Estado"),
    "inf_question": ("Open question", "Pregunta abierta"),
    "inf_agent": ("Agent", "Agente"),
    "inf_session": ("Session", "Sesión"),
    "inf_folder": ("Folder (cwd)", "Carpeta (cwd)"),
    "inf_model": ("Model", "Modelo"),
    "inf_mode": ("Mode", "Modo"),
    "inf_started": ("Started", "Inicio"),
    "inf_duration": ("Duration", "Duración"),
    "inf_tokens": ("Tokens", "Tokens"),
    "inf_context": ("Context", "Contexto"),
    "inf_result": ("Result (-o)", "Resultado (-o)"),
    "inf_log": ("Log", "Log"),
    "inf_file": ("Session file", "Archivo de sesión"),
    "inf_pid": ("PID", "PID"),
    "inf_command": ("Command", "Comando"),
    "inf_resume": ("Resume", "Retomar"),
    # notifications
    "n_paused": ("Auto-scroll paused (f to resume)", "Auto-scroll pausado (f para reanudar)"),
    "n_live_on": ("Showing live sessions only", "Mostrando solo sesiones vivas"),
    "n_live_off": ("Showing live sessions + last {h}h", "Mostrando vivas + últimas {h}h"),
    "n_verbose_on": ("Detail: command output, diffs and full prompts",
                     "Detalle: salida de comandos, diffs y prompts completos"),
    "n_verbose_off": ("Compact view", "Vista compacta"),
    "n_follow_on": ("Auto-scroll on", "Auto-scroll activado"),
    "n_follow_off": ("Auto-scroll paused", "Auto-scroll pausado"),
    "n_theme": ("Theme: {name}", "Tema: {name}"),
    "n_not_running": ("The selected session is not running.", "La sesión seleccionada no está corriendo."),
    "n_killed": ("Killed PID {pid} ({n} processes).", "Matado PID {pid} ({n} procesos)."),
    "n_kill_failed": ("Could not kill it: {e}", "No pude matarlo: {e}"),
    "n_no_file": ("No {what} for this session.", "No hay {what} para esta sesión."),
    "what_result": ("result", "resultado"),
    "what_log": ("log", "log"),
    "n_no_sid": ("No session id yet.", "Todavía no hay id de sesión."),
    "n_copied": ("Resume command copied to the clipboard.", "Comando para retomar copiado al portapapeles."),
    "n_copy_failed": ("Could not copy: {e}", "No pude copiar: {e}"),
    "n_done": ("{label} finished", "{label} terminó"),
    "n_stopped": ("{label} stopped", "{label} se cortó"),
    "n_ask": ("{label} is waiting for an answer", "{label} espera una respuesta"),
    "n_config": ("Config error, using defaults: {e}", "Error en la config, uso valores por defecto: {e}"),
    # kill dialog
    "kill_q": ("Kill «{label}» (PID {pid}) and its children?", "¿Matar «{label}» (PID {pid}) y sus hijos?"),
    "kill_yes": ("Yes, kill (y)", "Sí, matar (s)"),
    "kill_no": ("Cancel (n)", "Cancelar (n)"),
    # views
    "view_background": ("Background runs", "En segundo plano"),
    "view_background_d": ("codex exec, claude -p and SDK runs", "codex exec, claude -p y corridas del SDK"),
    "view_everything": ("Everything", "Todo"),
    "view_everything_d": ("every session from every agent", "todas las sesiones de todos los agentes"),
    "view_interactive": ("Interactive", "Interactivas"),
    "view_interactive_d": ("desktop apps, IDEs and terminal chats", "apps de escritorio, IDEs y chats en terminal"),
    "view_live": ("Live only", "Solo vivas"),
    "view_live_d": ("whatever is running right now", "lo que está corriendo ahora"),
    "view_custom_d": ("from your config.toml", "de tu config.toml"),
    "tagline": ("a logbook for your coding agents", "la bitácora de tus agentes de código"),
    "setup_first": ("Welcome. Pick the view Bitácora starts in; you can change everything later with s.",
                    "Bienvenido. Elige con qué vista arranca Bitácora; puedes cambiar todo después con s."),
    "setup_title": ("Setup", "Configuración"),
    "setup_hint": ("↑↓ move · ←→ change · Enter save · Esc close",
                   "↑↓ mueve · ←→ cambia · Enter guarda · Esc cierra"),
    "set_lang": ("Language", "Idioma"),
    "set_hours": ("History window", "Ventana de historial"),
    "set_notify": ("Notifications", "Avisos"),
    "set_theme": ("Theme", "Tema"),
    "lang_auto": ("System", "Sistema"),
    "hours_n": ("last {h} h", "últimas {h} h"),
    "on": ("on", "activados"),
    "off": ("off", "apagados"),
    "key_home": ("Home", "Inicio"),
    "key_header": ("Header", "Cabecera"),
    "cmd_home": ("Home and setup", "Inicio y configuración"),
    "cmd_home_d": ("Views, language, history window, notifications and theme",
                   "Vistas, idioma, ventana de historial, avisos y tema"),
    "cmd_view": ("View", "Vista"),
    "cmd_view_d": ("Show only the sessions this view selects", "Mostrar solo las sesiones de esta vista"),
    "cmd_header": ("Fold or unfold the header", "Plegar o desplegar la cabecera"),
    "cmd_header_d": ("Also with h, or a click on the bitácora strip",
                     "También con h o un clic en la franja de bitácora"),
    "cmd_live": ("Live sessions only", "Solo sesiones vivas"),
    "cmd_live_d": ("Toggle the live-only filter (a)", "Alternar el filtro de vivas (a)"),
    "cmd_verbose": ("Full detail", "Detalle completo"),
    "cmd_verbose_d": ("Command output, diffs and full prompts (v)",
                      "Salida de comandos, diffs y prompts completos (v)"),
    "cmd_follow": ("Auto-scroll", "Auto-scroll"),
    "cmd_follow_d": ("Pause or resume following new events (f)", "Pausar o reanudar el seguimiento de eventos (f)"),
    "cmd_lang": ("Language", "Idioma"),
    "cmd_lang_d": ("Redraws the interface in this language", "Redibuja la interfaz en este idioma"),
    "n_saved": ("Setup saved", "Configuración guardada"),
    "picker_counts": ("{n} sessions · {live} live", "{n} sesiones · {live} vivas"),
    "picker_found": ("Found: {where}", "Encontré: {where}"),
    "picker_nothing": ("No agent sessions found. Bitácora reads ~/.codex and ~/.claude (set CODEX_HOME or "
                       "CLAUDE_CONFIG_DIR if yours live elsewhere). Try bitacora --demo.",
                       "No encontré sesiones de agentes. Bitácora lee ~/.codex y ~/.claude (define CODEX_HOME o "
                       "CLAUDE_CONFIG_DIR si están en otro lado). Prueba bitacora --demo."),
    "picker_loading": ("Reading sessions…", "Leyendo sesiones…"),
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
    pair = S.get(key)
    if pair is None:
        return key
    text = pair[LANGS.index(_lang)]
    return text.format(**kw) if kw else text
