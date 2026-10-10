/* Bitácora desktop interface.

   All data arrives through window.pywebview.api (docs/desktop-contract.md); there is no other backend. Labels come
   from bootstrap().strings. Session content (labels, events, messages, logs, results, paths, commands) is untrusted
   data: it is only ever placed with textContent or as a text node, never parsed as HTML, and nothing navigates. */
(function () {
  "use strict";

  // ------------------------------------------------------------------------------------------------ constants
  const TABS = [
    { id: "act", key: "desktop_tab_activity" },
    { id: "msg", key: "desktop_tab_messages" },
    { id: "files", key: "desktop_tab_files" },
    { id: "log", key: "desktop_tab_log" },
    { id: "res", key: "desktop_tab_result" },
    { id: "info", key: "desktop_tab_details" },
  ];
  const FOLLOW_TABS = ["act", "msg", "log"];
  const HOURS = [6, 12, 24, 48, 72];
  const FONT_SIZES = [12, 13, 14, 15, 16, 18, 20, 22];
  const DEFAULT_FONT = 14;
  const MAX_EVENTS = 300; // activity rows kept in the DOM; older ones are counted, not drawn
  const MAX_MESSAGES = 200;
  const MAX_LOG_LINES = 3000;
  const MAX_FILES = 2000;
  const POLL_MS = 1000;
  const POLL_HOME_MS = 1500;
  const POLL_HIDDEN_MS = 4000;
  const DETAIL_REFRESH_MS = 4000; // logs can grow without a new snapshot revision
  const SCENE_MS = 66; // the scene moves with time, so the rate only sets smoothness
  const SCENE_TIMEOUT_MS = 2000; // a frame that never arrives must not stop the scene for good
  const SCENE_STILL_MS = 30000;
  const HIDDEN_TOOLS = new Set(["wait", "write_stdin"]);
  const KINDS = new Set(["think", "msg", "cmd", "file", "user", "tool", "ask", "turn", "err", "note"]);
  const CMD_ICON = { read: ["▤", "ev_read"], search: ["⌕", "ev_search"], list_files: ["▦", "ev_list"] };
  const STATUS_CLASS = {
    asking: "st-asking", starting: "st-starting", running: "st-running", your_turn: "st-your-turn",
    done: "st-done", failed: "st-failed", limited: "st-limited", stopped: "st-stopped", quiet: "st-quiet",
    unknown: "st-unknown",
  };
  const LOG_ERR = /\b(error|failed|panic)\b/i;
  const DEFAULT_THEME = "monokai"; // themes.DEFAULT_THEME: used where nothing is saved or a saved name is not offered
  const DEFAULT_COLORS = {
    background: "#272822", foreground: "#d6d6d6", panel: "#2e2e2e", muted: "#d6d6d6", accent: "#66d9ef",
    primary: "#ae81ff", secondary: "#f82672", success: "#a5e22e", warning: "#fc971f", error: "#f82672",
  };
  const REDUCED_MOTION = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ------------------------------------------------------------------------------------------------ state
  const S = {
    ready: false,
    strings: {},
    prefs: {},
    views: [],
    themes: [],
    lang: "en",
    version: "",
    firstRunSession: false,
    screen: "boot",
    // snapshot
    loaded: false,
    revision: undefined,
    sortColumn: null,
    sortDirection: null,
    sortRevision: 0,
    reader: {},
    runs: [],
    visible: [],
    counts: {},
    quotas: {},
    prevNotices: [],
    bridgeError: false,
    // selection and detail
    selected: null,
    selIndex: 0,
    detail: null,
    detailKey: null,
    detailState: "none", // none | loading | ok | missing | error
    detailError: "",
    detailWarn: false,
    detailAt: 0,
    // view switches
    tab: "act",
    verbose: false,
    follow: true,
    liveOnly: false,
    plain: false,
    listMaximized: false,
    userScrollAt: 0,
    // panes
    paneSig: {},
    actState: null,
    // home
    homeHi: 0,
  };

  // ------------------------------------------------------------------------------------------------ helpers
  const $ = (id) => document.getElementById(id);
  const has = (o, k) => o !== null && typeof o === "object" && Object.prototype.hasOwnProperty.call(o, k);

  function t(key, params) {
    // A count of exactly one uses the key's "_one" form when it has one ("1 failed" / "1 fallida").
    if (params && params.n === 1 && has(S.strings, key + "_one")) key += "_one";
    const s = has(S.strings, key) ? String(S.strings[key]) : key;
    return params ? fill(s, params) : s;
  }

  function fill(s, params) {
    return s.replace(/\{(\w+)\}/g, (m, name) => (has(params, name) ? String(params[name]) : m));
  }

  function str(v) {
    return v === null || v === undefined ? "" : String(v);
  }

  function num(v) {
    return typeof v === "number" && Number.isFinite(v) ? v : null;
  }

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }

  function span(cls, text) {
    return el("span", cls, text);
  }

  function button(cls, text) {
    const b = el("button", cls, text);
    b.type = "button";
    return b;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function setText(node, text) {
    text = str(text);
    if (node.textContent !== text) node.textContent = text;
  }

  function fmtDur(sec) {
    sec = num(sec);
    if (sec === null || sec < 0) return "-";
    sec = Math.floor(sec);
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    if (h >= 48) return `${Math.floor(h / 24)}d${pad(h % 24)}h`;
    return h ? `${h}h${pad(m)}m` : m ? `${m}m${pad(s)}s` : `${s}s`;
  }

  function fmtAgo(ts) {
    ts = num(ts);
    return ts === null ? "-" : fmtDur(Math.max(0, Date.now() / 1000 - ts));
  }

  function fmtTokens(n) {
    n = num(n);
    if (!n) return "-";
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
    return String(n);
  }

  function fmtNum(n) {
    const v = Number(n);
    return Number.isFinite(v) ? String(v) : str(n);
  }

  function hhmm(ts) {
    ts = num(ts);
    if (ts === null) return "--:--:--";
    const d = new Date(ts * 1000);
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  function whenText(ts, mins) {
    const d = new Date(ts * 1000);
    const far = mins ? mins >= 1440 : ts - Date.now() / 1000 >= 86400;
    const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return far ? `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${hm}` : hm;
  }

  function firstLine(s, width) {
    s = str(s).trim();
    s = s ? s.split(/\r?\n/)[0] : "";
    const chars = Array.from(s);
    return chars.length <= width ? s : chars.slice(0, width - 1).join("") + "…";
  }

  function provClass(p) {
    return p === "codex" ? "agent-codex" : p === "claude" ? "agent-claude" : "agent-other";
  }

  function providerName(provider) {
    const key = str(provider).toLowerCase().replace(/_/g, "-");
    if (key === "claude" || key === "claude-code") return t("provider_claude");
    if (key === "codex") return t("provider_codex");
    return str(provider);
  }

  function usageBasis(run) {
    if (run && (run.usage_basis === "processed" || run.usage_basis === "reported")) return run.usage_basis;
    return run && str(run.provider).toLowerCase().startsWith("claude") ? "processed" : "reported";
  }

  function accessKindLabel(kind) {
    const value = str(kind);
    const allowed = new Set(["read-only", "workspace-write", "prompt", "auto-edit", "plan", "full-access", "other"]);
    return t("access_" + (allowed.has(value) ? value : "other").replace(/-/g, "_"));
  }

  function contextTokens(run) {
    return num(run && run.context_tokens) !== null ? num(run.context_tokens) : num(run && run.ctx_used);
  }

  function fmtContextTokens(value) {
    return value === 0 ? "0" : fmtTokens(value);
  }

  function statusText(st) {
    return has(S.strings, "st_" + st) ? t("st_" + st) : t("st_unknown");
  }

  function statusClass(st) {
    return has(STATUS_CLASS, st) ? STATUS_CLASS[st] : "st-unknown";
  }

  function activityText(run) {
    const a = run && run.activity;
    if (a && typeof a === "object") return str(a.text);
    return str(a);
  }

  // ------------------------------------------------------------------------------------------------ bridge
  function api() {
    return window.pywebview && window.pywebview.api;
  }

  async function call(name, ...args) {
    const a = api();
    if (!a || typeof a[name] !== "function") throw new Error(name);
    return await a[name](...args);
  }

  async function safeCall(name, ...args) {
    try {
      const res = await call(name, ...args);
      return res && typeof res === "object" ? res : { ok: false };
    } catch (e) {
      return { ok: false, message: str(e && e.message ? e.message : e) };
    }
  }

  function failMsg(res) {
    return (res && (str(res.message) || str(res.error))) || t("desktop_action_failed");
  }

  // ------------------------------------------------------------------------------------------------ theme
  const hexOk = (c) => typeof c === "string" && /^#[0-9a-f]{6}$/i.test(c);
  const rgb = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
  const toHex = (a) => "#" + a.map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join("");

  function mix(a, b, k) {
    k = Math.max(0, Math.min(1, k));
    const A = rgb(a);
    const B = rgb(b);
    return toHex(A.map((v, i) => v + (B[i] - v) * k));
  }

  function luminance(c) {
    const w = [0.2126, 0.7152, 0.0722];
    return rgb(c).reduce((sum, v, i) => {
      v /= 255;
      return sum + w[i] * (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
    }, 0);
  }

  function contrast(a, b) {
    const x = luminance(a);
    const y = luminance(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  }

  // The color itself if it reads on the background; otherwise pulled toward the text color until it does.
  function readable(c, bg, fg) {
    for (let k = 0; k <= 20; k++) {
      const cand = mix(c, fg, k / 20);
      if (contrast(cand, bg) >= 4.5) return cand;
    }
    return fg;
  }

  // The text color for a highlight: the darkest or lightest of the theme's own text colors, whichever reads better,
  // if it reaches 4.5:1; otherwise black or white, whichever is further from the highlight. Same rule as
  // bitacora/themes.py (ink_on), so the desktop and the terminal pick the same ink.
  function ink(bg, text) {
    const darkest = text.reduce((a, b) => (luminance(b) < luminance(a) ? b : a));
    const lightest = text.reduce((a, b) => (luminance(b) > luminance(a) ? b : a));
    const own = contrast(lightest, bg) > contrast(darkest, bg) ? lightest : darkest;
    if (contrast(own, bg) >= 4.5) return own;
    return contrast("#000000", bg) >= contrast("#ffffff", bg) ? "#000000" : "#ffffff";
  }

  // The still line under the header: the foreground blended into the background until it reads at 1.6:1, as a
  // decoration (themes.rule_color in Python).
  function ruleColor(bg, fg) {
    for (let step = 18; step <= 100; step++) {
      const cand = mix(bg, fg, step / 100);
      if (contrast(cand, bg) >= 1.6) return cand;
    }
    return fg;
  }

  function applyTheme(id) {
    const th = S.themes.find((x) => x && x.id === id) || S.themes.find((x) => x && x.id === DEFAULT_THEME) || S.themes[0];
    const c = Object.assign({}, DEFAULT_COLORS);
    if (th && th.colors) for (const k in c) if (hexOk(th.colors[k])) c[k] = th.colors[k].toLowerCase();
    const bg = c.background;
    const fg = c.foreground;
    const dark = rgb(bg).reduce((a, b) => a + b, 0) < 384;
    const surface = mix(bg, fg, dark ? 0.045 : 0.05);
    const vars = {
      "--bg": bg,
      "--fg": fg,
      "--panel": hexOk(c.panel) && c.panel !== bg ? c.panel : mix(bg, fg, 0.09),
      "--surface": surface,
      "--hover": mix(bg, fg, 0.1),
      "--line": mix(bg, fg, 0.15),
      "--line-strong": mix(bg, fg, 0.3),
      "--dim": readable(mix(fg, bg, 0.42), surface, fg),
      "--accent": c.accent,
      "--primary": c.primary,
      "--green": readable(c.success, surface, fg),
      "--red": readable(c.error, surface, fg),
      "--yellow": readable(c.warning, surface, fg),
      "--cyan": readable(c.secondary, surface, fg),
      "--purple": readable(c.primary, surface, fg),
      "--blue": readable(mix(c.secondary, fg, 0.35), surface, fg),
      "--violet": readable(mix(c.primary, fg, 0.3), surface, fg),
      "--claude": readable(c.accent, surface, fg),
      "--sel-bg": c.primary,
      "--sel-fg": ink(c.primary, [bg, fg]),
      "--ask-bg": c.warning,
      "--ask-fg": ink(c.warning, [bg, fg]),
      "--warn": c.warning,
      "--rule": ruleColor(bg, fg),
    };
    const root = document.documentElement.style;
    for (const k in vars) root.setProperty(k, vars[k]);
    root.setProperty("color-scheme", dark ? "dark" : "light");
  }

  function fontPx(v) {
    const n = Number(v);
    return Number.isFinite(n) && n >= 10 && n <= 32 ? n : DEFAULT_FONT;
  }

  function applyFont(v) {
    document.documentElement.style.fontSize = fontPx(v) + "px";
    requestAnimationFrame(fitPresentation);
  }

  // ------------------------------------------------------------------------------------------------ start
  let started = false;
  let waits = 0;

  function start() {
    if (started) return;
    const a = api();
    if (!a || typeof a.bootstrap !== "function") return;
    started = true;
    boot();
  }

  window.addEventListener("pywebviewready", start);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
  // The ready event may have fired before this script ran, or the methods may appear a moment after it.
  const waiter = setInterval(() => {
    if (started) clearInterval(waiter);
    else if (++waits > 300) {
      clearInterval(waiter);
      $("boot").classList.add("failed");
    } else start();
  }, 100);

  async function boot() {
    let b = null;
    let err = "";
    try {
      b = await call("bootstrap");
    } catch (e) {
      err = str(e && e.message ? e.message : e);
    }
    if (!b || typeof b !== "object" || !b.strings || typeof b.strings !== "object") {
      $("boot").classList.add("failed");
      setText($("boot-msg"), err || (b && (str(b.message) || str(b.error))) || "");
      return;
    }
    applyBoot(b, true);
    S.firstRunSession = !!b.first_run;
    buildStatic();
    wire();
    S.ready = true;
    $("boot").hidden = true;
    if (S.firstRunSession) showHome();
    else showMonitor();
    pollSnapshot();
  }

  function applyBoot(b, initial, keepPrefs) {
    const prevLang = S.lang;
    const prevStrings = S.strings;
    S.strings = b.strings && typeof b.strings === "object" ? b.strings : S.strings;
    // While a later save is still on its way, this answer predates it: keep the reader's newest choices.
    if (!keepPrefs) S.prefs = Object.assign({}, b.preferences || {});
    S.views = Array.isArray(b.views) ? b.views.filter((v) => v && typeof v.id === "string") : [];
    S.themes = Array.isArray(b.themes) ? b.themes.filter((x) => x && typeof x.id === "string") : [];
    S.lang = b.lang === "es" ? "es" : "en";
    S.version = str(b.version);
    document.documentElement.lang = S.lang;
    applyTheme(S.prefs.theme);
    applyFont(S.prefs.font_size);
    applyListHeight();
    scheduleAutoListHeight();
    if (initial) {
      S.liveOnly = !!b.live_only;
      return;
    }
    // Only a language change redraws every label and pane; other preferences keep the reader's place.
    if (S.lang !== prevLang || JSON.stringify(S.strings) !== JSON.stringify(prevStrings)) relabelAll();
    else {
      renderSettings();
      $("summary")._sig = null;
      $("view-list")._sig = null;
      if (S.screen === "home") renderHome();
      else if (S.screen === "monitor") renderSummary();
    }
  }

  // ------------------------------------------------------------------------------------------------ static labels
  function relabelStatic() {
    for (const node of document.querySelectorAll("[data-t]")) setText(node, t(node.dataset.t));
    for (const node of document.querySelectorAll("[data-t-label]")) {
      node.title = t(node.dataset.tLabel);
      node.setAttribute("aria-label", t(node.dataset.tLabel));
    }
    setText($("home-tagline"), `${t("tagline")}  ·  v${S.version}`);
    $("runs").setAttribute("aria-label", t("hud_runs"));
    const totalHead = document.querySelector('.sort-head[data-sort="tok"]');
    if (totalHead) totalHead.title = t("col_total_help");
    renderSortState();
  }

  function buildStatic() {
    relabelStatic();
    buildTabs();
    buildSettings();
    renderKeys();
    renderSwitches();
  }

  function relabelAll() {
    relabelStatic();
    buildTabs();
    renderSettings();
    renderKeys();
    renderSwitches();
    for (const row of rowMap.values()) row.sig = {};
    resetPanes();
    $("summary")._sig = null;
    $("quotas")._sig = null;
    $("attention")._sig = null;
    $("list-empty")._sig = null;
    $("view-list")._sig = null;
    if (S.screen === "home") renderHome();
    else if (S.screen === "monitor") {
      renderMonitor();
      renderActivePane(true);
    }
  }

  // ------------------------------------------------------------------------------------------------ wiring
  function wire() {
    document.addEventListener("keydown", onKeyDown);
    // Dropping a file would navigate the privileged window; links are never created, but refuse them anyway.
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("drop", (e) => e.preventDefault());
    document.addEventListener("click", (e) => {
      if (e.target && e.target.closest && e.target.closest("a[href]")) e.preventDefault();
    }, true);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        if (S.screen === "home") sceneStart();
        pollSnapshot();
      }
    });
    let resizeTimer = 0;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => { scene.metrics = null; scheduleAutoListHeight(); }, 150);
    });

    // home
    $("btn-open").addEventListener("click", () => leaveHome(viewAt(S.homeHi)));
    $("btn-home-help").addEventListener("click", showHelp);
    $("btn-home-quit").addEventListener("click", quit);
    $("view-list").addEventListener("keydown", onViewListKey);

    // monitor
    $("btn-actions").addEventListener("click", showActionMenu);
    $("list-wrap").addEventListener("scroll", updateListEdge);
    $("btn-live").addEventListener("click", toggleLive);
    for (const button of document.querySelectorAll(".sort-head")) {
      button.addEventListener("click", () => cycleSort(button.dataset.sort));
    }
    $("list-resize").addEventListener("pointerdown", startListResize);
    $("list-resize").addEventListener("pointermove", moveListResize);
    $("list-resize").addEventListener("pointerup", finishListResize);
    $("list-resize").addEventListener("pointercancel", cancelListResize);
    $("btn-home").addEventListener("click", showHome);
    $("btn-help").addEventListener("click", showHelp);
    $("btn-verbose").addEventListener("click", toggleVerbose);
    $("btn-follow").addEventListener("click", toggleFollow);
    $("btn-plain").addEventListener("click", togglePlain);
    $("btn-latest").addEventListener("click", () => {
      if (!S.follow) toggleFollow();
    });
    for (const b of document.querySelectorAll("#sel-actions [data-act]")) {
      b.addEventListener("click", () => doAction(b.dataset.act));
    }
    for (const tab of TABS) {
      const pane = $("pane-" + tab.id);
      const mark = () => { S.userScrollAt = Date.now(); };
      pane.addEventListener("wheel", mark, { passive: true });
      pane.addEventListener("pointerdown", mark);
      pane.addEventListener("keydown", mark);
      pane.addEventListener("scroll", onPaneScroll);
    }
    $("overlay").addEventListener("mousedown", (e) => {
      if (e.target === $("overlay")) closeDialog();
    });
  }

  // ------------------------------------------------------------------------------------------------ screens
  function showHome() {
    if (!S.ready) return;
    S.screen = "home";
    $("monitor").hidden = true;
    $("home").hidden = false;
    const idx = S.views.findIndex((v) => v.id === S.prefs.view);
    S.homeHi = idx >= 0 ? idx : 0;
    $("view-list")._sig = null;
    renderHome();
    renderSettings();
    $("view-list").focus({ preventScroll: true });
    scene.metrics = null;
    sceneStart();
  }

  function showMonitor() {
    S.screen = "monitor";
    sceneStop();
    $("home").hidden = true;
    $("monitor").hidden = false;
    applyListHeight();
    renderListMaximized();
    renderMonitor();
    renderTabs();
    renderSwitches();
    renderActivePane(true);
    fitPresentation();
    scheduleAutoListHeight();
    fitPresentation();
    focusList();
    pollSnapshot();
  }

  function viewAt(i) {
    const v = S.views[i];
    return v ? v.id : null;
  }

  function leaveHome(viewId) {
    const id = viewId || (S.firstRunSession ? viewAt(S.homeHi) : null);
    if (id && (id !== S.prefs.view || S.firstRunSession)) savePrefs({ view: id });
    S.firstRunSession = false;
    showMonitor();
  }

  // ------------------------------------------------------------------------------------------------ snapshot
  let snapBusy = false;
  let snapQueued = false;
  let snapTimer = 0;

  function scheduleSnapshot(ms) {
    clearTimeout(snapTimer);
    snapTimer = setTimeout(pollSnapshot, ms);
  }

  async function pollSnapshot() {
    if (!S.ready) return;
    if (snapBusy) {
      // A change made while a poll is in flight (a new window, a view) must not wait for the next timer.
      snapQueued = true;
      return;
    }
    clearTimeout(snapTimer);
    snapBusy = true;
    snapQueued = false;
    const sortRevision = S.sortRevision;
    let snap = null;
    try {
      snap = await call("get_snapshot", S.sortColumn, S.sortDirection);
    } catch (e) {
      snap = null;
    }
    snapBusy = false;
    if (sortRevision !== S.sortRevision) {
      if (snap && Array.isArray(snap.notices)) showNotices(snap.notices);
      scheduleSnapshot(0);
      return;
    }
    if (!snap || typeof snap !== "object" || (snap.error && !Array.isArray(snap.runs))) {
      S.bridgeError = true;
      renderHealth();
    } else {
      S.bridgeError = false;
      applySnapshot(snap);
    }
    if (snapQueued) scheduleSnapshot(0);
    else scheduleSnapshot(document.hidden ? POLL_HIDDEN_MS : S.screen === "home" ? POLL_HOME_MS : POLL_MS);
  }

  function renderSortState() {
    for (const button of document.querySelectorAll(".sort-head")) {
      const key = button.dataset.sort;
      const active = S.sortColumn === key;
      const th = button.closest("th");
      const labelNode = button.querySelector("[data-t]");
      const label = labelNode ? t(labelNode.dataset.t) : key;
      const direction = active ? (S.sortDirection === "desc" ? "desc" : "asc") : null;
      const directionText = direction === "desc" ? t("sort_descending") : t("sort_ascending");
      th.setAttribute("aria-sort", direction === "desc" ? "descending" : direction ? "ascending" : "none");
      button.querySelector(".sort-arrow").textContent = direction === "desc" ? "↓" : direction ? "↑" : "";
      button.setAttribute("aria-label", active ? `${label}: ${directionText}` : t("sort_by", { column: label }));
      button.title = active ? `${label} · ${directionText}` : t("sort_by", { column: label });
    }
  }

  function resetSort() {
    S.sortColumn = null;
    S.sortDirection = null;
    S.sortRevision++;
    renderSortState();
    pollSnapshot();
  }

  function stepSort(delta) {
    const index = S.sortColumn === null ? (delta > 0 ? -1 : 0) : COLS.indexOf(S.sortColumn);
    S.sortColumn = COLS[(index + delta + COLS.length) % COLS.length];
    S.sortDirection = "asc";
    S.sortRevision++;
    renderSortState();
    pollSnapshot();
  }

  function cycleSort(column) {
    if (!COLS.includes(column)) return;
    if (S.sortColumn !== column) {
      S.sortColumn = column;
      S.sortDirection = "asc";
    } else if (S.sortDirection === "asc") {
      S.sortDirection = "desc";
    } else {
      S.sortColumn = null;
      S.sortDirection = null;
    }
    S.sortRevision++;
    renderSortState();
    pollSnapshot();
  }

  function applySnapshot(snap) {
    const revChanged = snap.revision !== S.revision;
    S.revision = snap.revision;
    S.reader = snap.reader && typeof snap.reader === "object" ? snap.reader : {};
    S.runs = Array.isArray(snap.runs) ? snap.runs.filter((r) => r && typeof r.key === "string") : [];
    S.counts = snap.counts && typeof snap.counts === "object" ? snap.counts : {};
    S.quotas = snap.quotas && typeof snap.quotas === "object" ? snap.quotas : {};
    S.loaded = true;
    showNotices(Array.isArray(snap.notices) ? snap.notices : []);
    updateTitle();
    if (S.screen === "home") renderHome();
    else if (S.screen === "monitor") {
      renderMonitor();
      if (!S.selected) renderActivePane(false);
      else if (revChanged) requestDetail(0);
      else if (!detailBusy && Date.now() - S.detailAt > DETAIL_REFRESH_MS) requestDetail(0);
    }
  }

  // Notices describe transitions; show the ones that were not in the previous snapshot.
  function showNotices(list) {
    const keys = list.filter((n) => n && n.text).map((n) => str(n.kind) + "\u0000" + str(n.text));
    const prev = S.prevNotices.slice();
    S.prevNotices = keys;
    if (S.prefs.notify === false) return;
    list.forEach((n, i) => {
      if (!n || !n.text) return;
      const at = prev.indexOf(keys[i]);
      if (at >= 0) {
        prev.splice(at, 1);
        return;
      }
      const kinds = { asking: "warn", done: "ok", stopped: "error", warning: "warn", error: "error" };
      toast(str(n.text), has(kinds, n.kind) ? kinds[n.kind] : "info");
    });
  }

  function counts() {
    const c = S.counts || {};
    if (!S.liveOnly && typeof c.running === "number") return c;
    const shown = S.runs.filter((run) => !S.liveOnly || run.live);
    const out = { running: 0, asking: 0, starting: 0, your_turn: 0, done: 0, failed: 0,
      limited: 0, stopped: 0, quiet: 0, unknown: 0, total: shown.length, live: 0 };
    for (const r of shown) {
      const k = r.status;
      if (has(out, k)) out[k]++;
      if (r.live) out.live++;
    }
    return out;
  }

  function updateTitle() {
    const n = num(counts().running) || 0;
    const title = n ? t("win_title", { n }) : "Bitácora";
    if (document.title !== title) document.title = title;
  }

  function currentRun() {
    return S.visible.find((r) => r.key === S.selected) || null;
  }

  function viewName(id) {
    const v = S.views.find((x) => x.id === id);
    if (!v) return str(id);
    const name = str(v.name || v.id);
    return has(S.strings, name) ? t(name) : name;
  }

  function readerTrouble() {
    return S.bridgeError || S.reader.state === "stale" || S.reader.state === "error";
  }

  // ------------------------------------------------------------------------------------------------ monitor
  function renderMonitor() {
    renderList();
    renderSummary();
    renderHealth();
    renderQuotas();
    renderAttention();
    renderSelected();
    const c = counts();
    const running = (num(c.running) || 0) > 0;
    $("monitor").classList.toggle("working", !S.loaded || S.reader.state === "loading" || running);
    // The divider's beam: it sweeps while a session in this view runs, amber while one waits for an answer.
    $("monitor").classList.toggle("running", running);
    $("monitor").classList.toggle("asking", running && (num(c.asking) || 0) > 0);
    fitPresentation();
  }

  function renderSummary() {
    const box = $("summary"), c = counts();
    const sig = JSON.stringify([c, S.prefs.view, S.prefs.hours, S.liveOnly, S.lang, askRuns().length]);
    if (box._sig === sig) return;
    box._sig = sig;
    clear(box);
    const labels = {running: ["●", "hud_n_running", "c-green"], asking: ["?", "hud_n_ask", "c-yellow"],
      starting: ["◌", "hud_n_starting", "c-cyan"], your_turn: ["○", "hud_n_idle", "c-blue"],
      done: ["✓", "hud_n_done", "c-green"], failed: ["✗", "hud_n_failed", "c-red"],
      limited: ["!", "hud_n_limited", "c-yellow"], stopped: ["■", "hud_n_cut", "dim"],
      quiet: ["·", "hud_n_quiet", "c-purple"]};
    for (const [key, [symbol, label, cls]] of Object.entries(labels)) {
      const n = num(c[key]) || 0;
      if (key === "asking" && askRuns().length) continue;
      if (!n && key !== "running") continue;
      const count = span("sum-count " + cls + (key === "running" || key === "asking" ? " urgent" : ""));
      count.title = t(label, {n});
      count.append(span("count-full", t(label, {n})), span("count-short", symbol + n));
      box.appendChild(count);
    }
    const view = span("sum-view");
    const vb = button("linkish b");
    const shortKeys = {everything: "tui_view_all", background: "tui_view_background",
      interactive: "tui_view_interactive", live: "tui_view_live"};
    const name = viewName(S.prefs.view);
    vb.append(span("view-full", name), span("view-short", shortKeys[S.prefs.view] ? t(shortKeys[S.prefs.view]) : name));
    vb.title = name + " · " + t("cmd_home");
    vb.addEventListener("click", showHome);
    const window = span("sum-window");
    window.append(span("window-full", t("hours_n", {h: fmtNum(S.prefs.hours)})),
      span("window-short", t("tui_window_short", {h: fmtNum(S.prefs.hours)})));
    view.append(vb, span("dim", "·"), window);
    box.appendChild(view);
  }

  // Adapt in priority order, measuring the actual language, font and available width.
  function fitHeader() {
    const bar = $("topbar"), summary = $("summary");
    if (!bar || !bar.clientWidth) return;
    bar.classList.remove("symbol-counts", "dot-health", "minimal-counts", "short-actions");
    const fits = () => summary.scrollWidth <= summary.clientWidth + 1 && bar.scrollWidth <= bar.clientWidth + 1;
    for (const cls of ["symbol-counts", "dot-health", "minimal-counts", "short-actions"]) {
      if (fits()) break;
      bar.classList.add(cls);
    }
  }

  function fitHeroActions() {
    const head = document.querySelector(".hero-head"), actions = $("sel-actions");
    if (!head || !head.clientWidth) return;
    const extra = Array.from(actions.querySelectorAll('[data-act]:not([data-act="stop"])'));
    for (const b of extra) b.hidden = false;
    $("btn-actions").hidden = true;
    const font = fontPx(S.prefs.font_size);
    // Leave readable room for the identity before assigning secondary actions.
    const identity = $("sel-status").scrollWidth + font * 31;
    if (actions.getBoundingClientRect().width + identity > head.clientWidth) {
      for (const b of extra) b.hidden = true;
      $("btn-actions").hidden = false;
    }
  }

  function showActionMenu() {
    const run = currentRun();
    if (!run) return;
    const actions = [["open_result", "key_result"], ["open_log", "key_log"], ["copy_resume", "key_copy"]];
    openDialog({title: t("desktop_more_actions"), buttons: actions.map(([act, key], i) => ({
      label: t(key), disabled: !(run.actions && run.actions[act]), focus: i === 0,
      run(close) { close(); doAction(act); }
    })).concat([{label: t("desktop_close"), cancel: true}])});
  }

  function fitPresentation() {
    fitKeys();
    fitHeader();
    fitHeroActions();
    const tools = document.querySelector(".tabbar");
    tools.classList.remove("short-tools");
    if (tools.scrollWidth > tools.clientWidth) tools.classList.add("short-tools");
    fitRunColumns();
    updateAutoListHeight();
    updateListEdge();
    const footer = $("keys");
    const bottom = (footer && footer.getClientRects().length ? footer.getBoundingClientRect().height : 0) + 8;
    const safeTop = S.screen === "monitor" ? tools.getBoundingClientRect().bottom
      : document.querySelector(".home-actions").getBoundingClientRect().bottom;
    $("toasts").style.bottom = bottom + "px";
    $("toasts").style.maxHeight = Math.max(24, window.innerHeight - safeTop - bottom - 4) + "px";
  }

  function updateListEdge() {
    const wrap = $("list-wrap"), edge = $("list-edge");
    const r = wrap.getBoundingClientRect();
    edge.hidden = $("monitor").hidden || wrap.scrollLeft + wrap.clientWidth >= wrap.scrollWidth - 1;
    edge.style.top = r.top + "px";
    edge.style.right = (window.innerWidth - r.right) + "px";
    edge.style.height = Math.max(0, r.height - 12) + "px";
  }

  function renderHealth() {
    const h = $("health");
    const r = S.reader || {};
    let cls = "ok";
    let text = "";
    let banner = "";
    let bad = false;
    if (S.bridgeError) {
      cls = "bad";
      text = t("desktop_reader_short_error");
      banner = t("desktop_bridge_error");
      bad = true;
    } else if (!S.loaded || r.state === "loading") {
      cls = "busy";
      text = t("picker_loading");
    } else if (r.state === "error") {
      cls = "bad";
      text = t("desktop_reader_short_error");
      banner = t("desktop_reader_error", { error: str(r.error) || "?" });
      bad = true;
    } else if (r.state === "stale") {
      cls = "warn";
      text = t("desktop_reader_short_stale");
      banner = t("desktop_reader_stale", { age: fmtDur(r.age) });
    } else if (num(r.age) !== null) {
      text = t("desktop_reader_ready", { age: fmtDur(r.age) });
    }
    h.className = "health " + cls;
    h.title = banner || text;
    h.setAttribute("aria-label", banner || text);
    setText(h, text);
    for (const id of ["banner", "home-banner"]) {
      const b = $(id);
      b.hidden = !banner;
      b.classList.toggle("bad", bad);
      setText(b, banner);
    }
    $("list-wrap").classList.toggle("stale", readerTrouble());
  }

  // ---- quotas
  function resetExpired(ts) {
    ts = num(ts);
    return ts !== null && ts <= Date.now() / 1000;
  }

  function quotaNameLabel(name, window) {
    const labels = { "5h": "quota_five_hour", week: "quota_week", weekly: "quota_week",
      "weekly Opus": "quota_week_opus", "weekly Sonnet": "quota_week_sonnet" };
    const nameKey = typeof name === "string" && Object.prototype.hasOwnProperty.call(labels, name) ? labels[name] : "";
    const windowKey = typeof window === "string" && Object.prototype.hasOwnProperty.call(labels, window) ? labels[window] : "";
    const key = nameKey || windowKey;
    return key ? t(key) : "";
  }

  function quotaWindowName(window) {
    return quotaNameLabel(window.name) || t("quota_window_unknown");
  }

  function quotaWindowText(window) {
    if (resetExpired(window.resets_at)) return t("quota_reset");
    const pct = num(window.used_percent);
    return pct === null ? t("quota_value_unknown") : Math.round(pct) + "%";
  }

  function quotaLimitText(limit) {
    const name = quotaNameLabel(limit.name, limit.window);
    if (resetExpired(limit.resets_at)) return t("quota_limit_reset");
    const when = num(limit.resets_at) !== null ? whenText(limit.resets_at) : str(limit.reset_text);
    if (name && when) return t("quota_limit_until", { type: name, when });
    if (when) return t("quota_limit_when", { when });
    if (name) return t("quota_limit_type", { type: name });
    return t("quota_limit");
  }

  function bar(frac) {
    const b = span("bar");
    if (frac === null) {
      b.classList.add("unknown");
      return b;
    }
    frac = Math.max(0, Math.min(1, frac));
    b.classList.add(frac < 0.6 ? "low" : frac < 0.85 ? "mid" : "high");
    const f = span("fill");
    f.style.width = (frac * 100).toFixed(1) + "%";
    b.appendChild(f);
    return b;
  }

  function renderQuotas() {
    const box = $("quotas");
    const q = S.quotas || {};
    const sig = JSON.stringify(q) + S.lang + Math.floor(Date.now() / 60000);
    if (box._sig === sig) return;
    box._sig = sig;
    clear(box);
    const provs = Object.keys(q);
    if (!provs.length) {
      box.appendChild(span("dim", t("quota_unknown")));
      return;
    }
    for (const prov of provs) {
      const rate = q[prov] && typeof q[prov] === "object" ? q[prov] : {};
      const item = span("quota");
      const detail = [];
      item.appendChild(span("q-prov", prov));
      const wins = Array.isArray(rate.windows) ? rate.windows.filter((w) => w && typeof w === "object") : [];
      if (rate.limit && typeof rate.limit === "object") {
        const limit = rate.limit;
        const name = quotaNameLabel(limit.name, limit.window) || t("quota_window_unknown");
        const when = num(limit.resets_at) !== null ? whenText(limit.resets_at) : str(limit.reset_text);
        const text = resetExpired(limit.resets_at) ? name + " " + t("tui_quota_limit_generic")
          : when ? name + " " + t("quota_limit_compact", {when}) : name + " " + t("tui_quota_limit_generic");
        item.appendChild(span("c-yellow b", text));
      }
      else if (!wins.length) item.appendChild(span("dim", t("quota_unknown")));
      for (const w of wins) {
        const wi = span("q-win");
        wi.appendChild(span("b", quotaWindowName(w) === t("quota_week") ? t("quota_week_short") : quotaWindowName(w)));
        if (resetExpired(w.resets_at)) {
          wi.appendChild(span("dim", t("quota_reset")));
        } else {
          const pct = num(w.used_percent);
          wi.appendChild(bar(pct === null ? null : pct / 100));
          wi.appendChild(span(pct === null ? "dim" : "", pct === null ? "?" : Math.round(pct) + "%"));
        }
        if (!resetExpired(w.resets_at) && num(w.resets_at) !== null) {
          const when = whenText(w.resets_at);
          wi.appendChild(span("dim q-renew", "↻" + when));
          detail.push(quotaWindowName(w) + " " + quotaWindowText(w) + " · " + t("quota_renews", {t: whenText(w.resets_at, 1440)}));
        } else detail.push(quotaWindowName(w) + " " + quotaWindowText(w));
        item.appendChild(wi);
      }
      item.title = [providerName(prov), rate.reporting === "limit-only" ? t("quota_unrecorded") : "",
        rate.limit ? quotaLimitText(rate.limit) : "", ...detail].filter(Boolean).join(" · ");
      box.appendChild(item);
    }
  }

  // ---- questions waiting for an answer, oldest first
  function askRuns() {
    return S.runs
      .filter((r) => r.status === "asking")
      .sort((a, b) => (num(a.last_ts) ?? Infinity) - (num(b.last_ts) ?? Infinity));
  }

  function renderAttention() {
    const box = $("attention");
    const asks = askRuns();
    box.hidden = !asks.length;
    if (!asks.length) {
      box._sig = null;
      return;
    }
    const sig = JSON.stringify([asks.map((r) => [r.key, r.label]), S.selected, S.lang]);
    if (box._sig !== sig) {
      box._sig = sig;
      clear(box);
      box.appendChild(span("attn-head", "? " + t("hud_n_ask", { n: asks.length })));
      asks.slice(0, 3).forEach((r, i) => {
        const chip = button("chip" + (r.key === S.selected ? " sel" : ""));
        chip.appendChild(span("chip-label", firstLine(r.label, 60) || "-"));
        chip.title = str(r.label);
        const age = span("dim chip-age");
        age.dataset.key = r.key;
        chip.appendChild(age);
        if (i === 0 && asks.length > 1) chip.appendChild(span("tag", t("desktop_oldest")));
        chip.addEventListener("click", () => revealRun(r.key));
        box.appendChild(chip);
      });
      if (asks.length > 3) box.appendChild(span("dim", "+" + (asks.length - 3)));
    }
    for (const age of box.querySelectorAll(".chip-age")) {
      const r = asks.find((x) => x.key === age.dataset.key);
      if (r) {
        const last = num(r.last_ts);
        age.hidden = last === null;
        if (last !== null) setText(age, t("desktop_waiting_for", {age: fmtAgo(last)}));
      }
    }
  }

  function revealRun(key) {
    if (!S.visible.some((r) => r.key === key) && S.liveOnly) {
      S.liveOnly = false;
      renderSwitches();
      renderKeys();
      renderMonitor();
    }
    if (S.visible.some((r) => r.key === key)) selectRun(key, { focusList: true });
  }

  function jumpToQuestion() {
    const asks = askRuns();
    if (asks.length) revealRun(asks[0].key);
  }

  // ---- run list
  const rowMap = new Map();
  let rowSeq = 0;
  const COLS = ["status", "agent", "name", "model", "repo", "now", "last", "dur", "ctx", "tok", "cmds", "files", "mode", "pid"];

  function makeRow(key) {
    const tr = el("tr");
    tr.id = "run-" + ++rowSeq;
    tr.setAttribute("role", "row");
    tr.setAttribute("aria-selected", "false");
    const cells = {};
    for (const c of COLS) {
      const td = el("td", "c-" + c);
      td.setAttribute("role", "gridcell");
      tr.appendChild(td);
      cells[c] = td;
    }
    tr.addEventListener("click", () => selectRun(key, { focusList: true }));
    tr.addEventListener("dblclick", focusPane);
    return { tr, cells, sig: {} };
  }

  function setCell(row, col, text, cls, title, cap) {
    const td = row.cells[col];
    const full = "c-" + col + (cls ? " " + cls : "");
    const sig = text + "\u0000" + full + "\u0000" + (title || "") + "\u0000" + (cap || "");
    if (row.sig[col] === sig) return;
    row.sig[col] = sig;
    if (cap) td.replaceChildren(el("span", "cell-clip", text));
    else td.textContent = text;
    td.className = full;
    td.title = title || "";
  }

  function updateRow(row, r) {
    const modelName = str(r.model) || "-";
    const model = r.effort ? modelName + " " + str(r.effort) : modelName;
    const act = activityText(r);
    const nowCls = "now" + (r.status === "asking" ? " ask" : r.status === "your_turn" ? " idle" : !r.live ? " dead" : "");
    const ctxUsed = contextTokens(r);
    const ctxWindow = num(r.ctx_window);
    let ctx = "-";
    let ctxCls = "dim";
    if (ctxWindow && ctxWindow > 0) {
      if (ctxUsed !== null) {
        const frac = ctxUsed / ctxWindow;
        ctx = `${(100 * frac).toFixed(0)}%`;
        ctxCls = frac < 0.6 ? "ctx-low" : frac < 0.85 ? "ctx-mid" : "ctx-high";
      } else ctx = t("quota_value_unknown");
    } else if (ctxUsed !== null) ctx = fmtContextTokens(ctxUsed);
    const cmdCount = num(r.n_cmds);
    const failures = num(r.n_fail);
    const cmds = cmdCount === null ? "-" : `${String(cmdCount)}${failures ? ` ${String(failures)}\u2717` : ""}`;
    const fileCount = num(r.n_files);
    const pid = num(r.pid);
    setCell(row, "status", statusText(r.status), statusClass(r.status));
    setCell(row, "agent", str(r.agent) || "-", provClass(r.provider));
    setCell(row, "name", firstLine(r.label, 60) || "-", "name" + (r.live ? " live" : ""), str(r.label), 60);
    setCell(row, "model", model, "");
    setCell(row, "repo", str(r.repo) || "-", "", str(r.cwd));
    setCell(row, "now", firstLine(act, 40) || "-", nowCls, act, 40);
    setCell(row, "last", fmtAgo(r.last_ts), "");
    setCell(row, "dur", fmtDur(r.duration), "");
    setCell(row, "ctx", str(r.context_text) || ctx, ctxCls);
    const basis = usageBasis(r);
    setCell(row, "tok", str(r.tokens_text) || fmtTokens(r.tokens), "", t("usage_" + basis + "_basis"));
    setCell(row, "cmds", cmds, "");
    setCell(row, "files", fileCount === null ? "-" : String(fileCount), "");
    setCell(row, "mode", str(r.sandbox) || "-", "");
    setCell(row, "pid", pid && pid > 0 ? String(pid) : "-", "");
    row.tr.classList.toggle("ask", r.status === "asking");
  }

  function renderList() {
    const runs = S.liveOnly ? S.runs.filter((r) => r.live) : S.runs;
    S.visible = runs;
    const body = $("runs-body");
    const keep = new Set(runs.map((r) => r.key));
    for (const [k, row] of rowMap) {
      if (!keep.has(k)) {
        row.tr.remove();
        rowMap.delete(k);
      }
    }
    let cursor = body.firstChild;
    for (const r of runs) {
      let row = rowMap.get(r.key);
      if (!row) {
        row = makeRow(r.key);
        rowMap.set(r.key, row);
      }
      updateRow(row, r);
      if (row.tr !== cursor) body.insertBefore(row.tr, cursor);
      else cursor = cursor.nextSibling;
    }
    if (!S.selected || !keep.has(S.selected)) {
      // The selection left the list: stay at the same position rather than jumping to the top.
      const i = Math.max(0, Math.min(S.selIndex || 0, runs.length - 1));
      selectRun(runs.length ? runs[i].key : null, { scroll: false });
    } else {
      S.selIndex = runs.findIndex((r) => r.key === S.selected);
      markSelected();
    }
    renderEmpty(runs);
    fitRunColumns();
    scheduleAutoListHeight();
  }

  function fitRunColumns() {
    const table = $("runs");
    const wrap = $("list-wrap");
    if (!table.getClientRects().length) return;
    const sig = JSON.stringify([wrap.clientWidth, fontPx(S.prefs.font_size), S.lang,
      Array.from(table.rows, (row) => Array.from(row.cells, (cell) => cell.textContent))]);
    if (table._fitSig === sig) return;
    table._fitSig = sig;
    let group = table.querySelector("colgroup");
    if (!group) {
      group = el("colgroup");
      for (const key of COLS) group.appendChild(el("col"));
      table.prepend(group);
    }
    // Measure the natural table before assigning spare room to the two text columns.
    for (const col of group.children) col.style.width = "";
    table.style.minWidth = "0";
    table.style.width = "max-content";
    const widths = Array.from(table.tHead.rows[0].cells, (cell) => Math.ceil(cell.getBoundingClientRect().width));
    const extra = Math.max(0, wrap.clientWidth - widths.reduce((sum, width) => sum + width, 0));
    widths[COLS.indexOf("name")] += Math.ceil(extra / 2);
    widths[COLS.indexOf("now")] += Math.floor(extra / 2);
    Array.from(group.children).forEach((col, i) => { col.style.width = widths[i] + "px"; });
    table.style.width = widths.reduce((sum, width) => sum + width, 0) + "px";
    table.style.setProperty("--status-width", widths[0] + "px");
  }

  window.addEventListener("resize", fitRunColumns);

  function renderEmpty(runs) {
    const box = $("list-empty");
    let mode = "";
    if (!runs.length) {
      if (!S.loaded || S.reader.state === "loading") mode = "loading";
      else if (S.liveOnly && S.runs.length) mode = "live";
      else mode = "empty";
    }
    $("runs").hidden = !!mode;
    box.hidden = !mode;
    const sig = mode + S.lang;
    if (!mode || box._sig === sig) return;
    box._sig = sig;
    clear(box);
    if (mode === "loading") box.appendChild(span("", t("hud_searching")));
    else if (mode === "live") {
      box.appendChild(span("", t("desktop_empty_live")));
      const b = button("btn small", t("desktop_show_all"));
      b.addEventListener("click", toggleLive);
      box.appendChild(b);
    } else {
      box.appendChild(span("", t("hud_empty")));
      const b = button("btn small", t("desktop_change_view"));
      b.addEventListener("click", showHome);
      box.appendChild(b);
    }
  }

  function markSelected() {
    let active = "";
    for (const [k, row] of rowMap) {
      const on = k === S.selected;
      if (row.tr.classList.contains("sel") !== on) {
        row.tr.classList.toggle("sel", on);
        row.tr.setAttribute("aria-selected", String(on));
      }
      if (on) active = row.tr.id;
    }
    $("runs").setAttribute("aria-activedescendant", active);
  }

  function selectRun(key, opts) {
    opts = opts || {};
    const changed = key !== S.selected;
    S.selected = key;
    const idx = S.visible.findIndex((r) => r.key === key);
    if (idx >= 0) S.selIndex = idx;
    markSelected();
    const row = key ? rowMap.get(key) : null;
    if (row && opts.scroll !== false) row.tr.scrollIntoView({ block: "nearest" });
    if (changed) {
      S.detail = null;
      S.detailKey = key;
      S.detailState = key ? "loading" : "none";
      S.detailWarn = false;
      resetPanes();
      renderActivePane(true);
      if (key) requestDetail(120); // settles while an arrow key is held
    }
    renderSelected();
    renderAttention();
    if (opts.focusList) $("runs").focus({ preventScroll: true });
  }

  function move(delta) {
    const runs = S.visible;
    if (!runs.length) return;
    let i = runs.findIndex((r) => r.key === S.selected);
    i = i < 0 ? 0 : Math.max(0, Math.min(runs.length - 1, i + delta));
    selectRun(runs[i].key, { focusList: true });
  }

  function pageRows() {
    const wrap = $("list-wrap");
    const row = $("runs-body").firstChild;
    const h = row ? row.getBoundingClientRect().height || 20 : 20;
    return Math.max(1, Math.floor(wrap.clientHeight / h) - 2);
  }

  // ---- selected session summary and actions
  function openQuestion(run) {
    if (!S.detail || S.detailKey !== run.key) return "";
    const info = Array.isArray(S.detail.info) ? S.detail.info : [];
    const row = info.find((x) => x && x.label === t("inf_question"));
    if (row && row.value) return str(row.value);
    const evs = Array.isArray(S.detail.events) ? S.detail.events : [];
    for (let i = evs.length - 1; i >= 0; i--) {
      if (evs[i] && evs[i].kind === "ask") return str(evs[i].detail || evs[i].text);
    }
    return "";
  }

  function pair(box, label, value, cls, title) {
    const s = span("hero-pair");
    s.appendChild(span("k", label));
    const v = span("v" + (cls ? " " + cls : ""));
    if (value && typeof value === "object" && value.nodeType) v.appendChild(value);
    else setText(v, str(value));
    if (title) v.title = title;
    s.appendChild(v);
    box.appendChild(s);
    return s;
  }

  function renderSelected() {
    const run = currentRun();
    const sec = $("selected");
    sec.classList.toggle("empty", !run);
    const meta = $("sel-meta");
    const usage = $("sel-usage");
    const now = $("sel-now");
    clear(meta);
    clear(usage);
    clear(now);
    const q = $("sel-question");
    for (const b of document.querySelectorAll("#sel-actions [data-act]")) {
      const ok = !!(run && run.actions && run.actions[b.dataset.act]);
      b.disabled = !ok;
      b.title = ok ? "" : t("desktop_unavailable");
    }
    if (!run) {
      setText($("sel-status"), "");
      setText($("sel-agent"), "");
      setText($("sel-repo"), "");
      $("sel-label").className = "sel-label dim";
      setText($("sel-label"), !S.loaded ? t("hud_searching") : S.visible.length ? t("no_selection") : "");
      $("sel-label").title = "";
      q.hidden = true;
      sec.classList.remove("has-question");
      scheduleAutoListHeight();
      return;
    }
    const st = $("sel-status");
    st.className = "st status-badge " + statusClass(run.status);
    setText(st, statusText(run.status));
    st.title = statusText(run.status);
    $("sel-label").className = "sel-label";
    setText($("sel-label"), firstLine(run.label, 60) || "-");
    $("sel-label").title = str(run.label);
    const ag = $("sel-agent");
    ag.className = "agent " + provClass(run.provider);
    setText(ag, str(run.agent_full) || "-");
    ag.title = str(run.agent_full);

    const folder = $("sel-repo");
    setText(folder, str(run.repo) || "-");
    folder.title = str(run.cwd) || str(run.repo);

    const modelName = str(run.model) || "-";
    const model = run.effort ? modelName + " " + str(run.effort) : modelName;
    pair(meta, t("col_model"), model, "", model);
    pair(meta, t("col_mode"), str(run.sandbox) || "-", "", str(run.sandbox));
    pair(meta, t("inf_started"), str(run.started_text) || "-", "", str(run.started_text));
    pair(meta, t("col_dur"), fmtDur(run.duration), "", fmtDur(run.duration));
    pair(meta, t("col_last"), fmtAgo(run.last_ts), "", fmtAgo(run.last_ts));

    const basis = usageBasis(run);
    const basisHelp = t("usage_" + basis + "_basis");
    pair(usage, t("usage_" + basis), str(run.tokens_text) || fmtTokens(run.tokens), "", basisHelp);
    const used = contextTokens(run);
    const win = num(run.ctx_window);
    const contextText = str(run.context_text) || (win ? used !== null ? `${Math.round((100 * used) / win)}%` : t("quota_value_unknown")
      : used !== null ? fmtContextTokens(used) : "-");
    if (win && win > 0) {
      const contextValue = span("context-value");
      contextValue.appendChild(bar(used === null ? null : used / win));
      contextValue.appendChild(span("context-text", contextText));
      pair(usage, t("hud_context"), contextValue, "", contextText);
    } else pair(usage, t("hud_context"), contextText, "", contextText);
    const cmdCount = num(run.n_cmds);
    const failures = num(run.n_fail);
    const cmdValue = cmdCount === null ? "-" : String(cmdCount);
    const cmds = pair(usage, t("hud_commands"), cmdValue, "", `${cmdValue}${failures ? ` ${String(failures)}\u2717` : ""}`);
    if (failures) cmds.lastChild.appendChild(span("c-red", ` ${String(failures)}\u2717`));
    pair(usage, t("hud_files"), num(run.n_files) === null ? "-" : String(run.n_files), "", String(run.n_files ?? "-"));

    const act = activityText(run);
    const nowValue = span("now-value " + (run.status === "asking" ? "c-yellow b" : run.status === "your_turn" ? "c-blue" : run.live ? "" : "dim"));
    if (run.live && (run.status === "running" || run.status === "starting")) nowValue.appendChild(span("live-dot", "\u25cf "));
    nowValue.appendChild(document.createTextNode(act || "-"));
    nowValue.title = act;
    pair(now, t("hud_now"), nowValue, "", act);

    sec.classList.toggle("has-question", run.status === "asking");
    q.hidden = run.status !== "asking";
    if (!q.hidden) {
      const text = openQuestion(run);
      const sig = run.key + "\u0000" + text + S.lang;
      if (q._sig !== sig) {
        q._sig = sig;
        clear(q);
        const body = el("div", "q-body");
        body.appendChild(span("b c-yellow q-label", "? " + t("desktop_question_banner") + " "));
        if (text) body.appendChild(span("q-text", text));
        q.appendChild(body);
        q.appendChild(el("div", "q-hint", t("desktop_question_hint")));
      }
    }
    scheduleAutoListHeight();
  }

  // ---- switches, tabs, footer
  function switchContent(btn, label, key, on) {
    clear(btn);
    btn.title = label;
    btn.setAttribute("aria-label", label);
    btn.appendChild(span("sw-label", label));
    btn.appendChild(span("sw-state " + (on ? "on" : "off"), on ? "● " + t("ft_on") : "○ " + t("ft_off")));
    btn.appendChild(el("kbd", null, key));
    btn.setAttribute("aria-pressed", String(on));
    btn.classList.toggle("on", on);
  }

  function renderSwitches() {
    switchContent($("btn-live"), t("key_live_short"), "a", S.liveOnly);
    switchContent($("btn-verbose"), t("key_verbose"), "v", S.verbose);
    switchContent($("btn-follow"), t("key_follow"), "f", S.follow);
    switchContent($("btn-plain"), t("desktop_plain"), "m", S.plain);
    $("btn-plain").hidden = !(S.tab === "msg" || S.tab === "res");
    $("btn-verbose").hidden = S.tab !== "act";
    $("btn-latest").hidden = S.follow || !FOLLOW_TABS.includes(S.tab);
    const warn = $("detail-warn");
    warn.hidden = !S.detailWarn;
    setText(warn, S.detailWarn ? t("desktop_detail_stale") : "");
    requestAnimationFrame(fitPresentation);
  }

  function buildTabs() {
    const box = $("tabs");
    clear(box);
    box.setAttribute("aria-label", t("desktop_tabs_label"));
    TABS.forEach((tab, i) => {
      const b = button("tab");
      b.id = "tab-" + tab.id;
      b.setAttribute("role", "tab");
      b.setAttribute("aria-controls", "pane-" + tab.id);
      b.appendChild(el("kbd", null, String(i + 1)));
      b.appendChild(span("tab-label", t(tab.key)));
      b.addEventListener("click", () => setTab(tab.id));
      box.appendChild(b);
      $("pane-" + tab.id).setAttribute("aria-labelledby", "tab-" + tab.id);
    });
    renderTabs();
  }

  function renderTabs() {
    for (const tab of TABS) {
      const on = tab.id === S.tab;
      const b = $("tab-" + tab.id);
      if (b) {
        b.setAttribute("aria-selected", String(on));
        b.tabIndex = on ? 0 : -1;
        b.classList.toggle("on", on);
      }
      $("pane-" + tab.id).hidden = !on;
    }
  }

  function setTab(id, focusTab) {
    if (!TABS.some((x) => x.id === id)) return;
    S.tab = id;
    renderTabs();
    renderSwitches();
    renderActivePane(true);
    if (focusTab) $("tab-" + id).focus();
  }

  function renderKeys() {
    const box = $("keys");
    clear(box);
    const groups = [
      ["nav", [["↑↓", "ft_move", "list"], ["Enter", "ft_detail", "detail"], ["1–6", "ft_tabs", null], ["Esc", "ft_list", "list"]]],
      ["switch", [["f", "key_follow", "follow", S.follow], ["a", "key_live", "live", S.liveOnly],
        ["v", "key_verbose", "verbose", S.verbose], ["g", "key_maximize", "maximize", S.listMaximized]]],
      ["act", [["o", "key_result", "open_result"], ["l", "key_log", "open_log"], ["k", "desktop_stop", "stop"],
        ["c", "key_copy", "copy_resume"]]],
      ["app", [["s", "key_home", "home"], ["?", "desktop_help", "help"], ["F11", "ft_full", "full"], ["q", "key_quit", "quit"]]],
    ];
    groups.forEach(([name, items], gi) => {
      const g = span("kgroup g-" + name);
      if (gi) g.appendChild(span("sep", "│"));
      for (const [key, label, cmd, state] of items) {
        const h = cmd ? button("khint") : span("khint");
        if (cmd) {
          h.dataset.cmd = cmd;
          h.tabIndex = -1;
          h.addEventListener("click", () => runCommand(cmd));
        }
        h.appendChild(span("kkey", key));
        h.appendChild(span("klabel", t(label)));
        if (state !== undefined) h.appendChild(span("kstate " + (state ? "on" : "off"), state ? "● " + t("ft_on") : "○ " + t("ft_off")));
        g.appendChild(h);
      }
      box.appendChild(g);
    });
    requestAnimationFrame(fitKeys);
  }

  function fitKeys() {
    const box = $("keys");
    if (!box || !box.clientWidth) return;
    for (const group of box.querySelectorAll(".kgroup")) group.style.display = "";
    for (const hint of box.querySelectorAll(".khint")) hint.style.display = "";
    box.classList.remove("compact-keys");
    // Keep the maximize switch visible: its footer hint is also its mouse control.
    const available = box.clientWidth;
    const needed = () => {
      const style = getComputedStyle(box);
      const groups = Array.from(box.querySelectorAll('.kgroup')).filter(g => g.getBoundingClientRect().width > 0);
      return groups.reduce((sum, g) => sum + g.getBoundingClientRect().width, 0)
        + Math.max(0, groups.length - 1) * (parseFloat(style.columnGap) || 0)
        + (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
    };
    for (const name of ["act", "switch", "nav"]) {
      if (needed() <= available) break;
      const group = box.querySelector(".g-" + name);
      if (group && name === "switch") {
        for (const hint of group.querySelectorAll('.khint:not([data-cmd="maximize"])')) hint.style.display = "none";
      } else if (group) group.style.display = "none";
    }
    if (needed() > available) box.classList.add("compact-keys");
  }
  window.addEventListener("resize", fitPresentation);

  function runCommand(cmd) {
    const commands = {
      list: focusList, detail: focusPane, follow: toggleFollow, live: toggleLive, verbose: toggleVerbose,
      maximize: toggleListMaximized,
      plain: togglePlain, open_result: () => doAction("open_result"), open_log: () => doAction("open_log"),
      copy_resume: () => doAction("copy_resume"), stop: () => doAction("stop"), home: showHome, help: showHelp,
      full: fullscreen, quit, theme: nextTheme, refresh,
    };
    if (commands[cmd]) commands[cmd]();
  }

  function focusList() {
    const table = $("runs");
    if (!table.hidden) table.focus({ preventScroll: true });
  }

  function focusPane() {
    $("pane-" + S.tab).focus({ preventScroll: true });
  }

  function toggleLive() {
    S.liveOnly = !S.liveOnly;
    toast(S.liveOnly ? t("n_live_on") : t("n_live_off", { h: fmtNum(S.prefs.hours) }));
    renderSwitches();
    renderKeys();
    renderMonitor();
  }

  function toggleVerbose() {
    S.verbose = !S.verbose;
    toast(S.verbose ? t("n_verbose_on") : t("n_verbose_off"));
    renderSwitches();
    renderKeys();
    renderActivePane(true);
  }

  function toggleFollow() {
    S.follow = !S.follow;
    toast(S.follow ? t("n_follow_on") : t("n_follow_off"));
    renderSwitches();
    renderKeys();
    if (S.follow && FOLLOW_TABS.includes(S.tab)) scrollEnd($("pane-" + S.tab));
  }

  function togglePlain() {
    S.plain = !S.plain;
    renderSwitches();
    renderActivePane(true);
  }

  function applyListHeight(value) {
    const stored = value === undefined ? S.prefs.list_height : value;
    const monitor = $("monitor");
    const handle = $("list-resize");
    const raw = stored === null || stored === undefined || stored === "" ? null : Number(stored);
    if (raw === null || !Number.isFinite(raw)) {
      if (monitor) {
        monitor.classList.add("list-auto");
        monitor.style.removeProperty("--list-height");
      }
      scheduleAutoListHeight();
      return null;
    }
    const height = Math.max(0.15, Math.min(0.85, raw));
    if (monitor) {
      monitor.classList.remove("list-auto");
      monitor.style.setProperty("--list-height", String(height));
    }
    if (handle) handle.setAttribute("aria-valuenow", String(Math.round(height * 100)));
    return height;
  }

  function scheduleAutoListHeight() {
    requestAnimationFrame(updateAutoListHeight);
  }

  function updateAutoListHeight() {
    const monitor = $("monitor");
    if (!monitor || monitor.hidden || S.listMaximized) return;
    const list = $("list-wrap"), table = $("runs"), hero = $("selected");
    const row = $("runs-body").firstElementChild;
    const font = fontPx(S.prefs.font_size);
    const rowHeight = row ? row.getBoundingClientRect().height : font * 1.45;
    const headerHeight = table.tHead ? table.tHead.getBoundingClientRect().height : font * 1.6;
    const minimum = Math.ceil(headerHeight + rowHeight * 4 + 14);
    list.style.minHeight = minimum + "px";
    const total = Math.max(0, monitor.clientHeight - $("rule").getBoundingClientRect().bottom);
    const tabs = monitor.querySelector(".tabbar");
    const footer = $("keys").getBoundingClientRect().height;
    const handle = $("list-resize").getBoundingClientRect().height;
    const paneMinimum = font * 2;
    monitor.classList.remove("hero-compact");
    let fixed = hero.getBoundingClientRect().height + tabs.getBoundingClientRect().height + footer + handle + 10;
    if (total - fixed < minimum + paneMinimum || window.innerHeight < 700 || window.innerWidth / font < 68) {
      monitor.classList.add("hero-compact");
      fixed = hero.getBoundingClientRect().height + tabs.getBoundingClientRect().height + footer + handle + 10;
    }
    if (monitor.classList.contains("list-auto")) {
      const maxList = Math.max(minimum, total - fixed - paneMinimum);
      const desired = headerHeight + rowHeight * 8 + 14;
      monitor.style.setProperty("--list-auto-height", Math.round(Math.min(maxList, Math.max(total * 0.5, desired))) + "px");
    }
    updateListEdge();
  }

  function renderListMaximized() {
    $("monitor").classList.toggle("list-maximized", S.listMaximized);
  }

  let resizePreview = null;

  function resizeListAt(clientY) {
    const monitor = $("monitor");
    const list = $("list-wrap");
    const panes = $("panes");
    const listRect = list.getBoundingClientRect();
    const available = listRect.height + panes.getBoundingClientRect().height;
    if (!monitor.clientHeight || !available) return;
    resizePreview = Math.max(0.15, Math.min(0.85, (clientY - listRect.top) / available));
    applyListHeight(resizePreview);
  }

  function startListResize(e) {
    if (e.button !== 0 || S.listMaximized) return;
    e.preventDefault();
    resizePreview = applyListHeight();
    e.currentTarget.classList.add("dragging");
    e.currentTarget.setPointerCapture(e.pointerId);
    resizeListAt(e.clientY);
  }

  function moveListResize(e) {
    if (resizePreview !== null) resizeListAt(e.clientY);
  }

  function finishListResize() {
    if (resizePreview === null) return;
    const value = resizePreview;
    resizePreview = null;
    $("list-resize").classList.remove("dragging");
    const current = S.prefs.list_height;
    if (current === null || current === undefined || Math.abs(value - Number(current)) > 0.001) savePrefs({ list_height: value });
  }

  function cancelListResize() {
    if (resizePreview === null) return;
    resizePreview = null;
    $("list-resize").classList.remove("dragging");
    applyListHeight();
  }

  let listFocus = null;
  function toggleListMaximized() {
    if (!S.listMaximized) listFocus = document.activeElement;
    S.listMaximized = !S.listMaximized;
    renderListMaximized();
    if (S.listMaximized) focusList();
    else if (listFocus && listFocus.getClientRects().length) listFocus.focus({ preventScroll: true });
    else focusList();
    renderKeys();
    scheduleAutoListHeight();
  }



  function nextTheme() {
    if (!S.themes.length) return;
    const i = S.themes.findIndex((x) => x.id === S.prefs.theme);
    const th = S.themes[(i + 1) % S.themes.length];
    savePrefs({ theme: th.id });
    toast(t("n_theme", { name: str(th.name || th.id) }));
  }

  function refresh() {
    pollSnapshot();
    if (S.screen === "monitor" && S.selected) requestDetail(0);
  }

  function fullscreen() {
    safeCall("toggle_fullscreen");
  }

  function quit() {
    safeCall("quit");
  }

  // ------------------------------------------------------------------------------------------------ detail
  let detailBusy = false;
  let detailAgain = false;
  let detailTimer = 0;

  function requestDetail(delay) {
    clearTimeout(detailTimer);
    detailTimer = setTimeout(fetchDetail, delay || 0);
  }

  async function fetchDetail() {
    if (detailBusy) {
      detailAgain = true;
      return;
    }
    const key = S.selected;
    if (!key) return;
    detailBusy = true;
    let res;
    let failed = false;
    try {
      res = await call("get_detail", key);
    } catch (e) {
      failed = true;
      res = str(e && e.message ? e.message : e);
    }
    detailBusy = false;
    S.detailAt = Date.now();
    if (key === S.selected) applyDetail(key, res, failed);
    if (detailAgain) {
      detailAgain = false;
      fetchDetail();
    }
  }

  function applyDetail(key, res, failed) {
    if (failed || !res || typeof res !== "object") {
      if (S.detail && S.detailKey === key) S.detailWarn = true;
      else {
        S.detailState = "error";
        S.detailError = typeof res === "string" ? res : "";
      }
    } else if (res.error) {
      S.detail = null;
      S.detailState = "missing";
      S.detailError = str(res.error);
      S.detailWarn = false;
    } else {
      S.detail = res;
      S.detailKey = key;
      S.detailState = "ok";
      S.detailWarn = false;
    }
    renderSwitches();
    renderActivePane(false);
    renderSelected();
  }

  function resetPanes() {
    S.paneSig = {};
    S.actState = null;
    for (const tab of TABS) clear($("pane-" + tab.id));
  }

  function scrollEnd(pane) {
    pane.scrollTop = pane.scrollHeight;
  }

  function onPaneScroll(e) {
    const pane = e.currentTarget;
    if (pane.id !== "pane-" + S.tab || !FOLLOW_TABS.includes(S.tab)) return;
    if (Date.now() - S.userScrollAt > 1200) return; // only the user's own scrolling changes the switch
    const atEnd = pane.scrollHeight - pane.scrollTop - pane.clientHeight <= 6;
    if (S.follow && !atEnd) {
      S.follow = false;
      toast(t("n_paused"));
    } else if (!S.follow && atEnd) S.follow = true;
    else return;
    renderSwitches();
    renderKeys();
  }

  function scrollPane(how) {
    const pane = $("pane-" + S.tab);
    S.userScrollAt = Date.now();
    if (how === "home") pane.scrollTop = 0;
    else if (how === "end") pane.scrollTop = pane.scrollHeight;
    else pane.scrollBy(0, how * Math.max(40, pane.clientHeight * (Math.abs(how) > 1 ? 0.3 : 0.12)));
  }

  // Draw the active pane only; the other panes catch up when they are opened.
  function renderActivePane(switched) {
    const pane = $("pane-" + S.tab);
    let changed;
    if (!S.selected) changed = paneMessage(pane, S.loaded ? t("no_selection") : t("hud_searching"));
    else if (S.detailState === "missing") {
      changed = paneMessage(pane, t("desktop_detail_missing"), S.detailError);
    } else if (!S.detail) {
      changed = paneMessage(pane, S.detailState === "error" ? t("desktop_bridge_error") : t("desktop_detail_loading"));
    } else {
      changed = RENDER[S.tab](pane, S.detail);
    }
    if ((changed || switched) && S.follow && FOLLOW_TABS.includes(S.tab)) scrollEnd(pane);
  }

  function paneMessage(pane, text, extra) {
    const sig = "message\u0000" + text + "\u0000" + str(extra);
    if (S.paneSig[S.tab] === sig) return false;
    S.paneSig[S.tab] = sig;
    if (S.tab === "act") S.actState = null;
    clear(pane);
    const box = el("div", "pane-msg", text);
    if (extra) box.appendChild(el("div", "dim", extra));
    pane.appendChild(box);
    return true;
  }

  // Rebuild a pane, keeping the reader's place unless the pane is following new content.
  function rebuild(pane, build) {
    const top = pane.scrollTop;
    clear(pane);
    build();
    if (!(S.follow && FOLLOW_TABS.includes(S.tab))) pane.scrollTop = top;
  }

  const RENDER = {
    act: renderActivity,
    msg: renderMessages,
    files: renderFiles,
    log: renderLog,
    res: renderResult,
    info: renderInfo,
  };

  // ---- activity: keyed by event sequence, so only new or changed events touch the DOM
  function visibleEvent(ev, verbose) {
    if (verbose) return true;
    return !(ev.kind === "think" || ev.kind === "note" || (ev.kind === "tool" && HIDDEN_TOOLS.has(ev.sub)));
  }

  function renderActivity(pane, d) {
    const evs = Array.isArray(d.events) ? d.events.filter((e) => e && typeof e === "object") : [];
    const base = [S.selected, S.verbose, S.lang].join("\u0000");
    const quick = [base, evs.length, evs.length && evs[0].seq, JSON.stringify(evs.slice(-2)), d.events_total].join("\u0000");
    if (S.paneSig.act === quick) return false;
    S.paneSig.act = quick;
    let st = S.actState;
    if (!st || st.base !== base) {
      clear(pane);
      st = S.actState = { base, nodes: new Map(), note: el("div", "ev-earlier"), feed: el("div", "feed") };
      pane.appendChild(st.note);
      pane.appendChild(st.feed);
    }
    const shown = [];
    evs.forEach((ev, i) => {
      if (!visibleEvent(ev, S.verbose)) return;
      const prev = i ? evs[i - 1] : null;
      shown.push({ ev, think: !S.verbose && prev && prev.kind === "think" ? prev : null, i });
    });
    // Older events are counted honestly: those cut here and those Python did not send.
    const sentLess = Math.max(0, (num(d.events_total) || 0) - evs.length);
    const cut = Math.max(0, shown.length - MAX_EVENTS);
    const list = cut ? shown.slice(cut) : shown;
    const hidden = cut + sentLess;
    st.note.hidden = !hidden && evs.length > 0;
    setText(st.note, !evs.length ? t("no_session_file") : hidden ? t("ev_earlier", { n: hidden }) : "");

    const fresh = new Map();
    let cursor = st.feed.firstChild;
    for (const item of list) {
      const id = num(item.ev.seq) !== null && item.ev.seq > 0 ? "s" + item.ev.seq : "i" + item.i;
      const sig = JSON.stringify(item.ev) + "\u0000" + (item.think ? item.think.dur : "");
      let node = st.nodes.get(id);
      if (!node || node._sig !== sig || fresh.has(id)) {
        const made = renderEvent(item.ev, S.verbose, item.think);
        made._sig = sig;
        if (node && node.parentNode === st.feed && !fresh.has(id)) {
          st.feed.replaceChild(made, node);
          if (cursor === node) cursor = made;
        }
        node = made;
      }
      if (node !== cursor) st.feed.insertBefore(node, cursor);
      else cursor = cursor.nextSibling;
      fresh.set(id, node);
    }
    while (cursor) {
      const next = cursor.nextSibling;
      st.feed.removeChild(cursor);
      cursor = next;
    }
    st.nodes = fresh;
    return true;
  }

  function block(text, cls, maxLines, tail) {
    let lines = str(text).replace(/\s+$/, "").split(/\r?\n/);
    const wrap = el("div", "ev-block " + (cls || ""));
    let cut = 0;
    if (maxLines && lines.length > maxLines) {
      cut = lines.length - maxLines;
      lines = tail ? lines.slice(-maxLines) : lines.slice(0, maxLines);
    }
    if (cut && tail) wrap.appendChild(el("div", "ev-cut", t("ev_lines_before", { n: cut })));
    wrap.appendChild(el("pre", "ev-pre", lines.map((l) => (l.length > 1000 ? l.slice(0, 1000) + "…" : l)).join("\n")));
    if (cut && !tail) wrap.appendChild(el("div", "ev-cut", t("ev_lines_more", { n: cut })));
    return wrap;
  }

  function diffBlock(text, maxLines) {
    const lines = str(text).split(/\r?\n/);
    const wrap = el("div", "ev-block diff");
    const pre = el("pre", "ev-pre");
    for (const ln of lines.slice(0, maxLines)) {
      const cls = ln.startsWith("+") ? "d-add" : ln.startsWith("-") ? "d-del" : ln.startsWith("@@") ? "d-hunk" : "d-ctx";
      pre.appendChild(span(cls, (ln.length > 1000 ? ln.slice(0, 1000) + "…" : ln) + "\n"));
    }
    wrap.appendChild(pre);
    if (lines.length > maxLines) wrap.appendChild(el("div", "ev-cut", t("ev_lines_more", { n: lines.length - maxLines })));
    return wrap;
  }

  function renderEvent(ev, verbose, think) {
    const kind = KINDS.has(ev.kind) ? ev.kind : "tool";
    const row = el("div", "ev ev-" + kind);
    row.appendChild(span("ev-time", hhmm(ev.ts)));
    const badge = span("ev-badge");
    if (think && (num(think.dur) || 0) >= 1) badge.textContent = "◇" + fmtDur(think.dur);
    row.appendChild(badge);
    const body = el("div", "ev-body");
    row.appendChild(body);
    const text = str(ev.text);
    const detail = str(ev.detail);
    const add = (cls, s) => body.appendChild(span(cls, s));
    const dur = num(ev.dur) || 0;

    if (kind === "think") {
      add("c-violet it", t("ev_thinking"));
      const info = [];
      if ((num(ev.count) || 1) > 1) info.push(t("ev_steps", { n: ev.count }));
      if (dur >= 1) info.push(fmtDur(dur));
      if (info.length) add("dim it", "  " + info.join(" · "));
      if (detail) body.appendChild(block(detail, "think"));
      else if (dur >= 1) add("dim", t("ev_hidden"));
    } else if (kind === "cmd") {
      const ok = ev.ok !== false;
      const [icon, verb] = has(CMD_ICON, ev.sub) ? CMD_ICON[ev.sub] : ["❯", ""];
      add("c-cyan b", icon + " ");
      if (verb) add("c-cyan", t(verb) + " ");
      let lines = text.split(/\r?\n/);
      if (!verbose) lines = [firstLine(lines.map((l) => l.trim()).join(" "), 150)];
      add(ok ? "" : "b", verbose ? lines[0].slice(0, 300) : lines[0]);
      const d = dur >= 0.05 ? ` ${dur.toFixed(1)}s` : "";
      if (ok) add("c-green", "  ✓" + d);
      else {
        const code = ev.code !== null && ev.code !== undefined && ev.code !== "" && ev.code !== "error" ? ` exit ${str(ev.code)}` : "";
        add("c-red b", "  ✗" + code + d);
      }
      if (lines.length > 1) body.appendChild(block(lines.slice(1).join("\n"), "cmdx"));
      if (detail && (verbose || !ok)) body.appendChild(block(detail, ok ? "out" : "err", verbose ? 14 : 5, true));
    } else if (kind === "file") {
      const sub = str(ev.sub);
      const what = ["add", "update", "delete"].includes(sub) ? t("ev_" + sub) : sub;
      add("c-purple b", "✎ ");
      add("c-purple", what + " ");
      add("b", text);
      if (num(ev.plus)) add("c-green", "  +" + ev.plus);
      if (num(ev.minus)) add("c-red", " −" + ev.minus);
      if (verbose && detail) body.appendChild(diffBlock(detail, 40));
    } else if (kind === "msg") {
      const lines = (detail || text).trim().split(/\r?\n/);
      add("c-fg b", "● " + (lines[0] || ""));
      if (lines.length > 1) body.appendChild(block(lines.slice(1).join("\n"), "msg", verbose ? 0 : 10));
    } else if (kind === "user") {
      add("c-blue b", t("ev_prompt"));
      add("c-blue", text);
      if (verbose && detail) body.appendChild(block(detail, "user", 40));
    } else if (kind === "ask") {
      add("ask-badge", t("ev_question"));
      add("c-yellow b", "  " + (detail || text));
    } else if (kind === "turn") {
      const key = "ev_turn_" + str(ev.sub);
      add("", "──── " + (ev.sub && has(S.strings, key) ? t(key) : text) + " ────");
    } else if (kind === "err") {
      add("c-red b", "✗ ");
      add("c-red", ev.sub === "aborted" ? t("ev_aborted", { why: text }) : text);
      if (verbose && detail) body.appendChild(block(detail, "err", 14, true));
    } else if (kind === "note") {
      add("dim", "· " + text);
    } else {
      add("c-yellow", "ƒ ");
      add("c-yellow", text);
      if (verbose && detail) body.appendChild(block(detail, "out", 8, true));
    }
    return row;
  }

  // ---- messages
  function renderMessages(pane, d) {
    const msgs = Array.isArray(d.messages) ? d.messages.filter((m) => m && typeof m === "object") : [];
    const sig = [S.selected, S.plain, S.lang, msgs.length, JSON.stringify(msgs[msgs.length - 1] || null)].join("\u0000");
    if (S.paneSig.msg === sig) return false;
    S.paneSig.msg = sig;
    rebuild(pane, () => {
      if (!msgs.length) {
        const n = el("div", "pane-msg");
        n.appendChild(richNote("no_messages"));
        pane.appendChild(n);
        return;
      }
      const cut = Math.max(0, msgs.length - MAX_MESSAGES);
      if (cut) pane.appendChild(el("div", "ev-earlier", t("desktop_earlier_messages", { n: cut })));
      for (const m of msgs.slice(cut)) {
        const art = el("article", "msg");
        art.appendChild(el("div", "msg-ts", hhmm(m.ts)));
        art.appendChild(textBody(str(m.text)));
        pane.appendChild(art);
      }
    });
    return true;
  }

  function textBody(text) {
    if (S.plain) return el("pre", "plain", text);
    const box = el("div", "prose");
    box.appendChild(md(text));
    return box;
  }

  // ---- files
  function renderFiles(pane, d) {
    const files = Array.isArray(d.files) ? d.files.filter((f) => f && typeof f === "object") : [];
    const sig = [S.selected, S.lang, JSON.stringify(files)].join("\u0000");
    if (S.paneSig.files === sig) return false;
    S.paneSig.files = sig;
    rebuild(pane, () => {
      if (!files.length) {
        pane.appendChild(el("div", "pane-msg", t("desktop_files_empty")));
        return;
      }
      const sorted = files.slice().sort((a, b) => (num(b.ts) || 0) - (num(a.ts) || 0)).slice(0, MAX_FILES);
      const table = el("table", "files");
      const head = el("tr");
      for (const label of [t("fcol_kind"), "+", "−", t("fcol_n"), t("fcol_ts"), t("fcol_path")]) head.appendChild(el("th", null, label));
      const thead = el("thead");
      thead.appendChild(head);
      table.appendChild(thead);
      const tbody = el("tbody");
      for (const f of sorted) {
        const kind = str(f.kind);
        const tr = el("tr");
        tr.appendChild(el("td", "c-purple", ["add", "update", "delete"].includes(kind) ? t("ev_" + kind) : kind));
        tr.appendChild(el("td", "c-green", "+" + (num(f.plus) || 0)));
        tr.appendChild(el("td", "c-red", "−" + (num(f.minus) || 0)));
        tr.appendChild(el("td", null, num(f.count) === null ? "-" : String(f.count)));
        tr.appendChild(el("td", "dim", hhmm(f.ts)));
        tr.appendChild(el("td", "path", str(f.path)));
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      pane.appendChild(table);
      if (files.length > MAX_FILES) pane.appendChild(el("div", "ev-cut", t("ev_lines_more", { n: files.length - MAX_FILES })));
    });
    return true;
  }

  // ---- log
  function renderLog(pane, d) {
    const log = d.log && typeof d.log === "object" ? d.log : {};
    const text = str(log.text);
    const sig = [S.selected, S.lang, str(log.path), !!log.available, text.length, text.slice(-256)].join("\u0000");
    if (S.paneSig.log === sig) return false;
    S.paneSig.log = sig;
    rebuild(pane, () => {
      if (log.path) pane.appendChild(el("div", "pane-path", str(log.path)));
      if (!log.available || !text) {
        const n = el("div", "pane-msg");
        n.appendChild(richNote("no_log"));
        pane.appendChild(n);
        return;
      }
      let lines = text.replace(/\r?\n$/, "").split(/\r?\n/);
      if (lines.length > MAX_LOG_LINES) {
        pane.appendChild(el("div", "ev-cut", t("ev_lines_before", { n: lines.length - MAX_LOG_LINES })));
        lines = lines.slice(-MAX_LOG_LINES);
      }
      const pre = el("pre", "log");
      let cls = null;
      let buf = [];
      const flush = () => {
        if (buf.length) pre.appendChild(span(cls || "", buf.join("\n") + "\n"));
        buf = [];
      };
      for (const line of lines) {
        const c = LOG_ERR.test(line) ? "c-red" : line.startsWith("exec") || line.startsWith("$ ") ? "c-cyan" : "";
        if (c !== cls) {
          flush();
          cls = c;
        }
        buf.push(line);
      }
      flush();
      pane.appendChild(pre);
    });
    return true;
  }

  // ---- result
  function renderResult(pane, d) {
    const r = d.result && typeof d.result === "object" ? d.result : {};
    const sig = [S.selected, S.plain, S.lang, JSON.stringify(r)].join("\u0000");
    if (S.paneSig.res === sig) return false;
    S.paneSig.res = sig;
    rebuild(pane, () => {
      const text = str(r.text);
      const out = str(r.path) || t("res_no_out");
      if (r.available && text) {
        if (r.path) pane.appendChild(el("div", "pane-path", str(r.path)));
        pane.appendChild(textBody(text));
      } else if (text) {
        const n = el("div", "note");
        n.appendChild(richNote("res_last_msg", { out }));
        pane.appendChild(n);
        pane.appendChild(textBody(text));
      } else {
        const n = el("div", "pane-msg");
        n.appendChild(richNote("res_none", { out }));
        pane.appendChild(n);
      }
    });
    return true;
  }

  // ---- details
  function fallbackInfo(run) {
    if (!run) return [];
    const used = contextTokens(run);
    const win = num(run.ctx_window);
    let ctx = win !== null && win > 0 ? `${used ?? t("quota_value_unknown")} / ${win}` : used !== null ? String(used) : "-";
    if (run.context_window_assumed) ctx += " · " + t("context_window_assumed");
    const basis = usageBasis(run);
    return [
      [t("inf_status"), statusText(run.status)], [t("inf_agent"), str(run.agent_full) || providerName(run.provider)],
      [t("inf_status_source"), run.status_source === "process" || run.status_source === "transcript"
        ? t("status_source_" + run.status_source) : "-"],
      [t("inf_origin_raw"), str(run.origin_raw) || "-"], [t("inf_folder"), str(run.cwd) || "-"],
      [t("inf_model"), str(run.model) || "-"], [t("inf_effort"), str(run.effort) || "-"],
      [t("inf_effort_source"), t("effort_source_" + (run.effort_source === "transcript" || run.effort_source === "launch" ? run.effort_source : "unknown"))],
      [t("inf_access_kind"), accessKindLabel(run.access_kind)],
      [t("inf_mode"), str(run.sandbox) || "-"], [t("inf_duration"), fmtDur(run.duration)],
      [t("usage_" + basis), num(run.tokens) === null ? "-" : String(run.tokens)],
      [t("inf_tokens_basis"), t("usage_" + basis + "_basis")],
      [t("inf_context"), ctx], [t("inf_pid"), num(run.pid) === null ? t("not_running") : String(run.pid)],
    ].map(([label, value]) => ({ label, value }));
  }

  function renderInfo(pane, d) {
    let info = Array.isArray(d.info) ? d.info.filter((x) => x && typeof x === "object") : [];
    if (!info.length) info = fallbackInfo(d.run && typeof d.run === "object" ? d.run : currentRun());
    info = info.slice();
    for (const [provider, rate] of Object.entries(S.quotas)) {
      const windows = Array.isArray(rate.windows) ? rate.windows : [];
      const details = windows.map(w => [quotaWindowName(w), quotaWindowText(w),
        !resetExpired(w.resets_at) && num(w.resets_at) !== null ? t("quota_renews", {t: whenText(w.resets_at, 1440)}) : ""
      ].filter(Boolean).join(" · "));
      if (rate.limit) details.push(quotaLimitText(rate.limit));
      if (rate.reporting === "limit-only") details.push(t("quota_unrecorded"));
      info.push({label: t("hud_quota") + " · " + providerName(provider), value: details.join("; ") || t("quota_unknown")});
    }
    const sig = [S.selected, S.lang, JSON.stringify(info)].join("\u0000");
    if (S.paneSig.info === sig) return false;
    S.paneSig.info = sig;
    rebuild(pane, () => {
      const dl = el("dl", "info");
      for (const row of info) {
        dl.appendChild(el("dt", null, str(row.label)));
        const v = row.value;
        dl.appendChild(el("dd", null, v === null || v === undefined || v === "" ? "-" : typeof v === "object" ? JSON.stringify(v) : str(v)));
      }
      pane.appendChild(dl);
    });
    return true;
  }

  // ------------------------------------------------------------------------------------------------ markdown
  // A small, safe subset: every piece of text becomes a text node; nothing is parsed as HTML and links do not navigate.
  const LIST_RE = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
  const HR_RE = /^\s*([-*_])(\s*\1){2,}\s*$/;
  const FENCE_RE = /^\s*(```+|~~~+)\s*([\w+#.-]*)/;
  const INLINE_RE = /`([^`\n]+)`|\*\*([^*\n]+?)\*\*|__([^_\n]+?)__|(?<![\w*])\*([^*\n]+?)\*(?![\w*])|(?<![\w_])_([^_\n]+?)_(?![\w_])|\[([^\]\n]+)\]\(([^)\s]+)\)/g;

  function isBlockStart(line) {
    return FENCE_RE.test(line) || /^#{1,6}\s/.test(line) || /^\s*>/.test(line) || LIST_RE.test(line) ||
      HR_RE.test(line) || /^\s*\|/.test(line);
  }

  function md(src) {
    const frag = document.createDocumentFragment();
    const lines = str(src).replace(/\r\n?/g, "\n").split("\n");
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      let m;
      if (!line.trim()) {
        i++;
        continue;
      }
      if ((m = FENCE_RE.exec(line))) {
        const fence = m[1];
        const body = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith(fence)) body.push(lines[i++]);
        i++;
        const pre = el("pre", "md-code");
        pre.appendChild(el("code", null, body.join("\n")));
        frag.appendChild(pre);
        continue;
      }
      if ((m = /^(#{1,6})\s+(.*)$/.exec(line))) {
        const h = el("div", "md-h md-h" + Math.min(m[1].length, 4));
        inline(h, m[2].replace(/\s+#+\s*$/, ""));
        frag.appendChild(h);
        i++;
        continue;
      }
      if (HR_RE.test(line)) {
        frag.appendChild(el("hr", "md-hr"));
        i++;
        continue;
      }
      if (/^\s*>/.test(line)) {
        const body = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
        const q = el("blockquote", "md-quote");
        q.appendChild(md(body.join("\n")));
        frag.appendChild(q);
        continue;
      }
      if (/^\s*\|/.test(line)) {
        const body = [];
        while (i < lines.length && /^\s*\|/.test(lines[i])) body.push(lines[i++]);
        frag.appendChild(el("pre", "md-table", body.join("\n")));
        continue;
      }
      if ((m = LIST_RE.exec(line))) {
        const ordered = /\d/.test(m[2]);
        const list = el(ordered ? "ol" : "ul", "md-list");
        if (ordered && parseInt(m[2], 10) > 1) list.start = parseInt(m[2], 10);
        let li = null;
        while (i < lines.length) {
          const l = lines[i];
          const mm = LIST_RE.exec(l);
          if (mm && /\d/.test(mm[2]) === ordered) {
            li = el("li");
            inline(li, mm[3]);
            list.appendChild(li);
            i++;
          } else if (li && l.trim() && /^\s{2,}/.test(l) && !mm) {
            li.appendChild(el("br"));
            inline(li, l.trim());
            i++;
          } else break;
        }
        frag.appendChild(list);
        continue;
      }
      const p = el("p", "md-p");
      let first = true;
      while (i < lines.length && lines[i].trim() && (first || !isBlockStart(lines[i]))) {
        if (!first) p.appendChild(el("br"));
        inline(p, lines[i++]);
        first = false;
      }
      frag.appendChild(p);
    }
    return frag;
  }

  function inline(parent, text) {
    const s = str(text);
    const re = new RegExp(INLINE_RE.source, "g");
    let last = 0;
    let m;
    while ((m = re.exec(s))) {
      if (m.index > last) parent.appendChild(document.createTextNode(s.slice(last, m.index)));
      if (m[1] !== undefined) parent.appendChild(el("code", "md-ic", m[1]));
      else if (m[2] !== undefined || m[3] !== undefined) parent.appendChild(el("strong", null, m[2] ?? m[3]));
      else if (m[4] !== undefined || m[5] !== undefined) parent.appendChild(el("em", null, m[4] ?? m[5]));
      else {
        const link = span("md-link", m[6]);
        link.title = m[7];
        parent.appendChild(link);
        parent.appendChild(span("dim", " (" + m[7] + ")"));
      }
      last = re.lastIndex;
    }
    if (last < s.length) parent.appendChild(document.createTextNode(s.slice(last)));
  }

  // A translated note with light emphasis. The template is formatted first; values (paths) are filled in after, as
  // plain text, so their underscores or backticks never turn into markup.
  function richNote(key, params) {
    const frag = document.createDocumentFragment();
    inline(frag, has(S.strings, key) ? String(S.strings[key]) : key);
    if (params) {
      const walker = document.createTreeWalker(frag, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      for (const n of nodes) n.nodeValue = fill(n.nodeValue, params);
    }
    return frag;
  }

  // ------------------------------------------------------------------------------------------------ actions
  const busyActions = new Set();

  async function doAction(act) {
    const run = currentRun();
    if (!run) {
      toast(t("no_selection"), "warn");
      return;
    }
    if (!(run.actions && run.actions[act])) {
      const msg = act === "stop" ? t("n_not_running") : act === "copy_resume" ? t("n_no_sid")
        : t("n_no_file", { what: t(act === "open_result" ? "what_result" : "what_log") });
      toast(msg, "warn");
      return;
    }
    if (busyActions.has(act)) return;
    busyActions.add(act);
    try {
      if (act === "stop") await stopFlow(run);
      else if (act === "copy_resume") await copyFlow(run);
      else {
        const res = await safeCall(act, run.key);
        if (!res.ok) toast(failMsg(res), "error");
        else if (res.message) toast(str(res.message), "info");
      }
    } finally {
      busyActions.delete(act);
    }
  }

  async function stopFlow(run) {
    const res = await safeCall("request_stop", run.key);
    if (!res.ok || !res.token) {
      toast(failMsg(res), "error");
      return;
    }
    const token = res.token;
    const pid = num(res.pid) ?? num(run.pid) ?? "?";
    openDialog({
      title: t("desktop_stop_title"),
      danger: true,
      build(body) {
        body.appendChild(el("p", null, t("desktop_stop_body", { label: str(run.label) || "-", pid })));

      },
      buttons: [
        { label: t("desktop_cancel"), cancel: true, focus: true },
        {
          label: t("desktop_stop_confirm"),
          cls: "danger-fill",
          async run(close, btn) {
            for (const b of btn.parentNode.querySelectorAll("button")) b.disabled = true;
            const r = await safeCall("confirm_stop", token);
            close();
            if (r.ok) toast(str(r.message) || t("desktop_stop_sent"), "ok");
            else toast(failMsg(r), "error");
            pollSnapshot();
            requestDetail(400);
          },
        },
      ],
    });
  }

  async function copyFlow(run) {
    const res = await safeCall("copy_resume", run.key);
    if (!res.ok || typeof res.text !== "string" || !res.text) {
      toast((res && str(res.message)) || t("n_no_sid"), "warn");
      return;
    }
    const cmd = res.text;
    let status = null;
    let area = null;
    const show = (ok) => {
      if (!status) return;
      status.className = "copy-status " + (ok ? "ok" : "warn");
      setText(status, ok ? t("n_copied") : t("desktop_copy_manual"));
      if (!ok && area) {
        area.focus();
        area.select();
      }
    };
    openDialog({
      title: t("desktop_copy_title"),
      build(body) {
        status = el("p", "copy-status");
        area = el("textarea", "copy-text");
        area.readOnly = true;
        area.spellcheck = false;
        area.value = cmd;
        area.rows = Math.min(6, Math.max(2, Math.ceil(cmd.length / 60)));
        area.addEventListener("focus", () => area.select());
        body.appendChild(status);
        body.appendChild(area);
        body.appendChild(el("p", "dim", t("desktop_copy_note")));
      },
      buttons: [
        { label: t("desktop_copy_again"), run: async () => show(await copyText(cmd, area)) },
        { label: t("desktop_close"), cancel: true, focus: true },
      ],
    });
    show(await copyText(cmd, area));
  }

  async function copyText(text, area) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (e) {
      // fall through to the selection copy
    }
    try {
      const prev = document.activeElement;
      area.focus();
      area.select();
      const ok = document.execCommand("copy");
      if (prev && prev !== area && prev.focus) prev.focus();
      return !!ok;
    } catch (e) {
      return false;
    }
  }

  // ------------------------------------------------------------------------------------------------ dialogs
  let dialogState = null;

  function openDialog(opts) {
    if (dialogState) closeDialog();
    const dlg = $("dialog");
    clear(dlg);
    dlg.className = "dialog" + (opts.danger ? " danger" : "") + (opts.wide ? " wide" : "");
    const title = el("h2", "dialog-title", opts.title);
    title.id = "dialog-title";
    dlg.appendChild(title);
    const body = el("div", "dialog-body");
    dlg.appendChild(body);
    if (opts.build) opts.build(body);
    const bar = el("div", "dialog-buttons");
    let focus = null;
    for (const b of opts.buttons || []) {
      const btn = button("btn " + (b.cls || ""), b.label);
      btn.addEventListener("click", () => {
        if (b.cancel) closeDialog();
        else if (b.run) b.run(closeDialog, btn);
      });
      bar.appendChild(btn);
      btn.disabled = !!b.disabled;
      if (b.focus && !b.disabled) focus = btn;
    }
    dlg.appendChild(bar);
    dialogState = { prevFocus: document.activeElement };
    $("overlay").hidden = false;
    const first = opts.focusTop ? title : focus || dlg.querySelector("button:not(:disabled)");
    title.tabIndex = -1;
    first.focus({preventScroll: true});
    dlg.scrollTop = 0;
  }

  function closeDialog() {
    if (!dialogState) return;
    const st = dialogState;
    dialogState = null;
    $("overlay").hidden = true;
    clear($("dialog"));
    if (st.prevFocus && document.contains(st.prevFocus) && st.prevFocus.focus) st.prevFocus.focus({ preventScroll: true });
  }

  function trapTab(e) {
    const items = Array.from($("dialog").querySelectorAll("button:not(:disabled), textarea"));
    if (!items.length) return;
    const i = items.indexOf(document.activeElement);
    const next = e.shiftKey ? (i <= 0 ? items.length - 1 : i - 1) : (i + 1) % items.length;
    items[next].focus();
    e.preventDefault();
  }

  const HELP_KEYS = [
    ["↑ ↓   PgUp PgDn   Home End", "ft_move"],
    ["Enter", "ft_detail"],
    ["Esc", "ft_list"],
    ["1 – 6", "ft_tabs"],
    ["Shift + ↑ ↓   Shift + Home End", "desktop_help_scroll"],
    ["a", "key_live"],
    ["v", "key_verbose"],
    ["f", "key_follow"],
    ["g", "key_maximize"],
    ["m", "desktop_plain"],
    ["o", "key_result"],
    ["l", "key_log"],
    ["c", "key_copy"],
    ["k", "desktop_stop"],
    ["s   p", "key_home"],
    ["w", "key_jump_question"],
    ["< >", "sort_step"],
    ["Ctrl+R", "key_sort_reset"],
    ["t", "key_theme"],
    ["r F5", "key_refresh"],
    ["?   F1", "desktop_help"],
    ["F11", "ft_full"],
    ["q", "key_quit"],
  ];

  function showHelp() {
    if (!S.ready) return;
    openDialog({
      title: t("desktop_help"),
      wide: true,
      focusTop: true,
      build(body) {
        for (const key of ["desktop_help_intro", "desktop_help_mouse", "desktop_help_question", "desktop_help_copy"]) {
          body.appendChild(el("p", null, t(key)));
        }
        body.appendChild(el("h3", "help-sub", t("desktop_help_keys")));
        const table = el("table", "help-keys");
        for (const [keys, label] of HELP_KEYS) {
          const tr = el("tr");
          tr.appendChild(el("td", null, keys));
          tr.appendChild(el("td", null, t(label)));
          table.appendChild(tr);
        }
        body.appendChild(table);
      },
      buttons: [{ label: t("desktop_close"), cancel: true, focus: true }],
    });
  }

  // ------------------------------------------------------------------------------------------------ toasts
  function toast(text, kind) {
    if (!text) return;
    const box = $("toasts");
    const n = el("div", "toast " + (kind || "info"), text);
    n.addEventListener("click", () => n.remove());
    box.appendChild(n);
    fitPresentation();
    while (box.children.length > 3) box.firstChild.remove();
    setTimeout(() => n.remove(), kind === "error" || kind === "warn" ? 9000 : 5000);
  }

  // ------------------------------------------------------------------------------------------------ keyboard
  function onKeyDown(e) {
    if (!S.ready || e.defaultPrevented) return;
    if (dialogState) {
      if (e.key === "Escape") {
        e.preventDefault();
        closeDialog();
      } else if (e.key === "Tab") trapTab(e);
      return;
    }
    const k = e.key;
    if (k === "F11") {
      e.preventDefault();
      fullscreen();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && (k === "r" || k === "R")) {
      e.preventDefault();
      resetSort();
      return;
    }
    if (k === "F5") {
      e.preventDefault(); // a reload would drop the embedded page
      refresh();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && (k === "q" || k === "Q")) {
      e.preventDefault();
      quit();
      return;
    }
    if (k === "F1") {
      e.preventDefault();
      showHelp();
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = e.target && e.target.tagName;
    if (tag === "TEXTAREA" || tag === "INPUT" || tag === "SELECT") return;
    if (S.screen === "home") homeKey(e);
    else if (S.screen === "monitor") monitorKey(e);
  }

  function homeKey(e) {
    const target = e.target;
    switch (e.key) {
      case "Escape":
        leaveHome(null);
        break;
      case "Enter":
        if (target && target.tagName === "BUTTON") return;
        leaveHome(viewAt(S.homeHi));
        break;
      case "?":
        showHelp();
        break;
      case "q":
        quit();
        break;
      default:
        return;
    }
    e.preventDefault();
  }

  function monitorKey(e) {
    const target = e.target;
    const inPane = !!(target && target.closest && target.closest(".pane"));
    const onTab = !!(target && target.getAttribute && target.getAttribute("role") === "tab");
    const onButton = !!(target && target.tagName === "BUTTON");
    const k = e.key;
    if (e.shiftKey && ["ArrowUp", "ArrowDown", "Home", "End"].includes(k)) {
      scrollPane(k === "ArrowUp" ? -1 : k === "ArrowDown" ? 1 : k === "Home" ? "home" : "end");
      e.preventDefault();
      return;
    }
    if (/^[1-6]$/.test(k)) {
      setTab(TABS[Number(k) - 1].id);
      e.preventDefault();
      return;
    }
    switch (k) {
      case "ArrowUp":
      case "ArrowDown":
        if (inPane) return;
        move(k === "ArrowUp" ? -1 : 1);
        break;
      case "PageUp":
      case "PageDown":
        if (inPane) return;
        move((k === "PageUp" ? -1 : 1) * pageRows());
        break;
      case "Home":
      case "End":
        if (inPane) return;
        move(k === "Home" ? -1e9 : 1e9);
        break;
      case "ArrowLeft":
      case "ArrowRight": {
        if (!onTab) return;
        const i = TABS.findIndex((x) => x.id === S.tab);
        setTab(TABS[(i + (k === "ArrowLeft" ? -1 : 1) + TABS.length) % TABS.length].id, true);
        break;
      }
      case "Enter":
        if (inPane || onButton) return;
        focusPane();
        break;
      case "Escape":
        focusList();
        break;
      case "a": toggleLive(); break;
      case "v": toggleVerbose(); break;
      case "f": toggleFollow(); break;
      case "g": toggleListMaximized(); break;
      case "m": togglePlain(); break;
      case "w": jumpToQuestion(); break;
      case "<": stepSort(-1); break;
      case ">": stepSort(1); break;
      case "o": doAction("open_result"); break;
      case "l": doAction("open_log"); break;
      case "c": doAction("copy_resume"); break;
      case "k": doAction("stop"); break;
      case "s":
      case "p": showHome(); break;
      case "t": nextTheme(); break;
      case "r": refresh(); break;
      case "?": showHelp(); break;
      case "q": quit(); break;
      default:
        return;
    }
    e.preventDefault();
  }

  // ------------------------------------------------------------------------------------------------ home
  const SETTINGS = [
    { id: "lang", label: "set_lang", options: () => [["auto", t("lang_auto")], ["en", "English"], ["es", "Español"]] },
    {
      id: "hours",
      label: "set_hours",
      options: () => {
        const cur = Number(S.prefs.hours);
        const list = HOURS.slice();
        if (Number.isFinite(cur) && cur > 0 && !list.includes(cur)) list.push(cur);
        return list.sort((a, b) => a - b).map((h) => [h, t("hours_n", { h: fmtNum(h) })]);
      },
    },
    { id: "notify", label: "set_notify", options: () => [[true, t("on")], [false, t("off")]] },
    { id: "theme", label: "set_theme", options: () => S.themes.map((th) => [th.id, str(th.name || th.id)]) },
    {
      id: "font_size",
      label: "desktop_font_size",
      options: () => {
        const cur = fontPx(S.prefs.font_size);
        const list = FONT_SIZES.slice();
        if (!list.includes(cur)) list.push(cur);
        return list.sort((a, b) => a - b).map((n) => [n, t("desktop_font_n", { n })]);
      },
    },
  ];
  const setRows = {};

  function prefValue(def) {
    if (def.id === "font_size") return fontPx(S.prefs.font_size);
    if (def.id === "hours") return Number(S.prefs.hours);
    if (def.id === "notify") return S.prefs.notify !== false;
    return S.prefs[def.id];
  }

  function buildSettings() {
    const box = $("settings");
    clear(box);
    const rows = [];
    for (const def of SETTINGS) {
      const row = el("div", "set-row");
      row.tabIndex = 0;
      row.setAttribute("role", "group");
      const label = span("set-label");
      const prev = button("step", "◂");
      const value = span("set-value");
      const next = button("step", "▸");
      prev.tabIndex = -1;
      next.tabIndex = -1;
      value.setAttribute("aria-live", "polite");
      prev.addEventListener("click", () => { stepSetting(def, -1); row.focus(); });
      next.addEventListener("click", () => { stepSetting(def, 1); row.focus(); });
      value.addEventListener("click", () => { stepSetting(def, 1); row.focus(); });
      row.addEventListener("keydown", (e) => {
        const i = rows.indexOf(row);
        if (e.key === "ArrowLeft" || e.key === "ArrowRight") stepSetting(def, e.key === "ArrowLeft" ? -1 : 1);
        else if (e.key === "ArrowUp") (i > 0 ? rows[i - 1] : $("view-list")).focus();
        else if (e.key === "ArrowDown") (i < rows.length - 1 ? rows[i + 1] : $("btn-open")).focus();
        else return;
        e.preventDefault();
      });
      row.appendChild(label);
      row.appendChild(prev);
      row.appendChild(value);
      row.appendChild(next);
      box.appendChild(row);
      rows.push(row);
      setRows[def.id] = { row, label, prev, value, next };
    }
    renderSettings();
  }

  // Every value cell is as wide as the widest option of any selector, measured in the current font and language,
  // so the arrows line up and a value change never moves them. Hidden settings cannot be measured: wait for them.
  let valueFit = "";
  function fitSettingValues() {
    const box = $("settings");
    if (!box.getClientRects().length) return;
    const texts = [];
    for (const def of SETTINGS) for (const option of def.options()) texts.push(str(option[1]));
    const key = fontPx(S.prefs.font_size) + "|" + texts.join("\u0000");
    if (key === valueFit) return;
    valueFit = key;
    const probe = el("span", "set-value set-probe");
    box.appendChild(probe);
    let widest = 0;
    for (const text of texts) {
      probe.textContent = text;
      widest = Math.max(widest, probe.getBoundingClientRect().width);
    }
    probe.remove();
    box.style.setProperty("--set-value-w", Math.ceil(widest) + "px");
  }

  function renderSettings() {
    fitSettingValues();
    for (const def of SETTINGS) {
      const r = setRows[def.id];
      if (!r) continue;
      const opts = def.options();
      const cur = prefValue(def);
      const found = opts.find((o) => o[0] === cur);
      setText(r.label, t(def.label));
      r.label.title = t(def.label);
      r.row.setAttribute("aria-label", t(def.label));
      r.prev.setAttribute("aria-label", t("desktop_prev"));
      r.next.setAttribute("aria-label", t("desktop_next"));
      setText(r.value, found ? found[1] : str(cur) || "-");
      r.value.title = found ? found[1] : str(cur) || "-";
    }
  }

  function stepSetting(def, delta) {
    const opts = def.options();
    if (!opts.length) return;
    const i = opts.findIndex((o) => o[0] === prefValue(def));
    const next = opts[(Math.max(0, i) + (i < 0 ? 0 : delta) + opts.length) % opts.length][0];
    savePrefs({ [def.id]: next });
  }

  let saveChain = Promise.resolve();
  let savesPending = 0;

  function savePrefs(patch) {
    const before = Object.assign({}, S.prefs);
    savesPending += 1;
    Object.assign(S.prefs, patch);
    if (has(patch, "theme")) applyTheme(patch.theme);
    if (has(patch, "font_size")) applyFont(patch.font_size);
    if (has(patch, "list_height")) applyListHeight(patch.list_height);
    renderSettings();
    if (S.screen === "home") renderHome();
    saveChain = saveChain.then(async () => {
      let res = null;
      let err = "";
      try {
        res = await call("save_preferences", patch);
      } catch (e) {
        err = str(e && e.message ? e.message : e);
      }
      savesPending -= 1;
      if (res && typeof res === "object" && res.strings && res.preferences) {
        applyBoot(res, false, savesPending > 0);
        if (has(patch, "view") || has(patch, "hours")) {
          S.revision = undefined;
          pollSnapshot();
        }
      } else {
        S.prefs = before;
        applyTheme(before.theme);
        applyFont(before.font_size);
        applyListHeight(before.list_height);
        renderSettings();
        if (S.screen === "home") renderHome();
        toast(t("desktop_saving_failed", { e: err || (res && (str(res.message) || str(res.error))) || "?" }), "error");
      }
    });
    return saveChain;
  }

  function viewDesc(v) {
    const key = "view_" + v.id + "_d";
    return has(S.strings, key) ? t(key) : t("view_custom_d");
  }

  function renderHome() {
    $("home-first").hidden = !S.firstRunSession;
    renderHealth();
    const list = $("view-list");
    const vc = (S.counts && S.counts.views && typeof S.counts.views === "object") ? S.counts.views : {};
    const loading = !S.loaded || S.reader.state === "loading";
    const sig = JSON.stringify([S.views, vc, S.prefs.view, S.homeHi, S.lang, loading]);
    if (list._sig !== sig) {
      list._sig = sig;
      clear(list);
      S.views.forEach((v, i) => {
        const row = el("div", "view-row" + (i === S.homeHi ? " hi" : "") + (v.id === S.prefs.view ? " current" : ""));
        row.id = "view-" + i;
        row.setAttribute("role", "option");
        row.setAttribute("aria-selected", String(i === S.homeHi));
        const c = has(vc, v.id) && vc[v.id] && typeof vc[v.id] === "object" ? vc[v.id] : null;
        const total = c ? num(c.total) || 0 : 0;
        row.appendChild(span("vmark", v.id === S.prefs.view ? "▸" : ""));
        row.appendChild(span("vname", viewName(v.id)));
        row.appendChild(span("vdesc", viewDesc(v)));
        const countLabel = loading ? t("picker_loading") : c ? t("picker_counts", { n: total, live: num(c.live) || 0 }) : "";
        const count = span("vcount" + (total ? "" : " zero"));
        count.title = countLabel;
        count.append(span("vcount-full", countLabel), span("vcount-short", loading ? "…" : `${total} · ●${num(c && c.live) || 0}`));
        row.appendChild(count);
        row.addEventListener("click", () => {
          S.homeHi = i;
          list.focus({ preventScroll: true });
          if (v.id !== S.prefs.view) savePrefs({ view: v.id });
          else renderHome();
        });
        row.addEventListener("dblclick", () => leaveHome(v.id));
        list.appendChild(row);
      });
      list.setAttribute("aria-activedescendant", S.views.length ? "view-" + S.homeHi : "");
    }
    const none = S.loaded && S.reader.state === "ready" && S.views.length &&
      S.views.every((v) => !(has(vc, v.id) && vc[v.id] && num(vc[v.id].total)));
    const note = $("home-note");
    note.hidden = !none;
    setText(note, none ? t("picker_nothing") : "");
  }

  function onViewListKey(e) {
    const n = S.views.length;
    if (!n) return;
    if (e.key === "ArrowUp") S.homeHi = Math.max(0, S.homeHi - 1);
    else if (e.key === "ArrowDown") {
      if (S.homeHi >= n - 1) {
        const first = $("settings").querySelector(".set-row");
        if (first) first.focus();
        e.preventDefault();
        return;
      }
      S.homeHi += 1;
    } else if (e.key === "Home") S.homeHi = 0;
    else if (e.key === "End") S.homeHi = n - 1;
    else if (e.key === "Enter") {
      e.preventDefault();
      leaveHome(viewAt(S.homeHi));
      return;
    } else return;
    e.preventDefault();
    renderHome();
    const row = $("view-" + S.homeHi);
    if (row) row.scrollIntoView({ block: "nearest" });
  }

  // ------------------------------------------------------------------------------------------------ scene
  // The lighthouse, sky and falling code come from Python's scene (get_scene). Half-block cells carry two square
  // pixels: the top one in fg and the bottom one in bg.
  const scene = { busy: false, timer: 0, rows: 16, failed: 0, metrics: null };

  function sceneMetrics() {
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(200, $("scene-wrap").clientWidth || window.innerWidth);
    const bodyHeight = $("home").querySelector(".home-body").getBoundingClientRect().height;
    const target = Math.max(0, Math.min(window.innerHeight <= 700 ? 96 : 300,
      window.innerHeight * 0.34, window.innerHeight - bodyHeight));
    if (scene.metrics && scene.metrics.width === width && scene.metrics.height === target && scene.metrics.dpr === dpr) return scene.metrics;
    let px = Math.max(2, Math.floor((target / (scene.rows || 16) / 2) * dpr));
    let cols = Math.floor((width * dpr) / px);
    while (cols > 240) cols = Math.floor((width * dpr) / ++px);
    scene.metrics = { px, cols: Math.max(40, cols), dpr, width, height: target };
    return scene.metrics;
  }

  function sceneStart() {
    if (scene.timer) return;
    // A fixed rate: the next frame does not wait for the previous round trip plus a full period.
    scene.timer = setInterval(sceneTick, REDUCED_MOTION ? SCENE_STILL_MS : SCENE_MS);
    sceneTick();
  }

  function sceneStop() {
    clearInterval(scene.timer);
    scene.timer = 0;
  }

  async function sceneTick() {
    if (scene.busy) return; // never overlap calls; a slow frame just skips ticks
    if (S.screen !== "home" || document.hidden || scene.failed > 5) {
      sceneStop();
      return;
    }
    const m = sceneMetrics();
    scene.busy = true;
    let data = null;
    try {
      data = await Promise.race([call("get_scene", m.cols),
                                 new Promise((done) => setTimeout(() => done(null), SCENE_TIMEOUT_MS))]);
    } catch (e) {
      data = null;
    }
    scene.busy = false;
    if (data && typeof data === "object" && Array.isArray(data.cells)) {
      scene.failed = 0;
      if (S.screen === "home" && !document.hidden) drawScene(data, m);
    } else if (++scene.failed > 5) {
      sceneStop();
      $("scene").hidden = true;
      $("scene-fallback").hidden = false;
    }
  }

  function sceneColor(c, fallback) {
    if (hexOk(c)) return c;
    if (Array.isArray(c) && c.length >= 3) return toHex(c.slice(0, 3).map(Number));
    return fallback;
  }

  function drawScene(data, m) {
    const grid = data.cells.length && Array.isArray(data.cells[0]) ? data.cells : null;
    const cols = num(data.columns) || m.cols;
    const rows = num(data.rows) || (grid ? grid.length : Math.ceil(data.cells.length / cols));
    if (rows !== scene.rows) {
      scene.rows = rows;
      scene.metrics = null; // the pixel size depends on the row count
    }
    const px = m.px;
    const cv = $("scene");
    cv.hidden = false;
    $("scene-fallback").hidden = true;
    const W = cols * px;
    const H = rows * 2 * px;
    if (cv.width !== W || cv.height !== H) {
      cv.width = W;
      cv.height = H;
    }
    cv.style.width = "100%";
    cv.style.height = Math.min(H / m.dpr, m.height) + "px";
    const ctx = cv.getContext("2d", { alpha: false });
    const base = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() || "#000000";
    const font = `${Math.round(px * 1.75)}px "Cascadia Mono", Consolas, "MS Gothic", "Yu Gothic", monospace`;
    const bold = "bold " + font;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    let curFont = "";
    let x = 0;
    let y = 0;
    let first = null;

    const draw = (ch, fg, bg, isBold) => {
      const X = x * px;
      const Y = y * 2 * px;
      if (ch === "▀") {
        ctx.fillStyle = fg;
        ctx.fillRect(X, Y, px, px);
        ctx.fillStyle = bg;
        ctx.fillRect(X, Y + px, px, px);
      } else if (ch === "▄") {
        ctx.fillStyle = bg;
        ctx.fillRect(X, Y, px, px);
        ctx.fillStyle = fg;
        ctx.fillRect(X, Y + px, px, px);
      } else if (ch === "█") {
        ctx.fillStyle = fg;
        ctx.fillRect(X, Y, px, 2 * px);
      } else {
        ctx.fillStyle = bg;
        ctx.fillRect(X, Y, px, 2 * px);
        if (ch !== " ") {
          const f = isBold ? bold : font;
          if (f !== curFont) {
            ctx.font = f;
            curFont = f;
          }
          ctx.fillStyle = fg;
          ctx.fillText(ch, X + px / 2, Y + px + 0.5, px * 1.2);
        }
      }
    };

    const palette = Array.isArray(data.palette) ? data.palette : [];
    const seg = (cell) => {
      if (!Array.isArray(cell)) return; // [text, fg, bg, bold], colors indexed into the palette
      const bg = sceneColor(palette[cell[2]], base);
      const fg = sceneColor(palette[cell[1]], bg);
      if (first === null) first = bg;
      const text = str(cell[0]);
      if (grid && /^ +$/.test(text)) { // the sky is mostly blank runs: one rectangle each
        const n = Math.min(text.length, cols - x);
        if (y < rows && n > 0) {
          ctx.fillStyle = bg;
          ctx.fillRect(x * px, y * 2 * px, n * px, 2 * px);
        }
        x += text.length;
        return;
      }
      for (const ch of text) {
        if (!grid && x >= cols) {
          x = 0;
          y++;
        }
        if (y >= rows || x >= cols) return;
        draw(ch, fg, bg, !!cell[3]);
        x++;
      }
    };

    ctx.fillStyle = base;
    ctx.fillRect(0, 0, W, H);
    if (grid) {
      for (y = 0; y < Math.min(rows, grid.length); y++) {
        x = 0;
        for (const cell of grid[y]) seg(cell);
      }
    } else {
      for (const cell of data.cells) seg(cell);
    }
    if (first) $("scene-wrap").style.backgroundColor = first;
  }
})();
