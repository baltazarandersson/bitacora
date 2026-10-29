"""Browser regression tests for the desktop front end: message cursor, paging and language.

The UI is served from the real desktop assets; window.pywebview.api is a deterministic in-page stub. No session
data is read: the run, messages and detail responses are synthetic.
"""
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bitacora import i18n

playwright_sync = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync.sync_playwright
PlaywrightError = playwright_sync.Error

ASSETS = Path(__file__).resolve().parents[1] / "bitacora" / "desktop_assets"
STRINGS = {lang: {key: pair[index] for key, pair in i18n.S.items()} for index, lang in enumerate(i18n.LANGS)}
PREFS = {"view": "everything", "lang": "auto", "hours": 24, "notify": True,
         "theme": "monokai", "font_size": 14, "list_height": None}
RUN = {
    "key": "synthetic-run", "label": "Synthetic session", "status": "running", "agent": "Codex",
    "agent_full": "Codex", "provider": "codex", "live": True, "last_ts": 1700000000, "duration": 12,
    "model": "gpt-5", "sandbox": "read-only", "n_cmds": 1, "n_files": 1, "pid": 0,
    "actions": {"open_result": False, "open_log": False, "copy_resume": False, "stop": False},
}
SNAPSHOT = {
    "revision": 1,
    "reader": {"state": "ready", "age": 1},
    "runs": [RUN],
    "counts": {"running": 1, "asking": 0, "starting": 0, "your_turn": 0, "done": 0, "failed": 0,
               "limited": 0, "stopped": 0, "quiet": 0, "unknown": 0, "total": 1, "live": 1,
               "views": {"everything": {"total": 1, "live": 1}}},
    "quotas": {},
    "notices": [],
}
INFO = {"en": [{"label": "Agent status", "value": "ready"}],
        "es": [{"label": "Estado del agente", "value": "listo"}]}


_STUB = """(() => {
  const CFG = __CONFIG__;
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const state = window.__state = {lang: "en", snapshotRevision: 1, detailCalls: 0, pageRequests: [],
                                  snapshotMuted: false};
  const pages = copy(CFG.pages);
  window.__calls = [];
  const boot = () => ({
    version: "test", lang: state.lang, strings: CFG.strings[state.lang],
    preferences: Object.assign({}, CFG.prefs), first_run: false, live_only: false,
    views: [{id: "everything", name: "Everything"}],
    themes: [{id: "monokai", name: "Monokai", colors: {}}],
  });
  const snapshot = () => {
    const snap = copy(CFG.snapshot);
    snap.revision = state.snapshotRevision;
    return snap;
  };
  const detail = () => {
    state.detailCalls += 1;
    const win = CFG.details[Math.min(state.detailCalls - 1, CFG.details.length - 1)];
    const out = {
      run: copy(CFG.run), resume: "-", info: copy(CFG.info[state.lang]),
      revisions: {run: state.detailCalls, messages: state.detailCalls, events: 1, files: 1,
                  log: 1, result: 1, info: state.detailCalls},
      events: [], events_total: 0, files: [],
      log: {path: "synthetic.log", available: true, text: "log line"},
      result: {available: false, path: "", text: ""},
    };
    if (win.messages) {
      out.messages = copy(win.messages);
      out.messages_offset = win.offset;
    }
    return out;
  };
  window.pywebview = {api: {
    bootstrap: async () => { window.__calls.push(["bootstrap"]); return boot(); },
    get_snapshot: async () => {
      window.__calls.push(["get_snapshot"]);
      return state.snapshotMuted ? {error: true} : snapshot();
    },
    get_detail: async (key, known) => {
      window.__calls.push(["get_detail", key, known]);
      return detail();
    },
    get_detail_page: async (key, before, limit) => {
      window.__calls.push(["get_detail_page", key, before, limit]);
      state.pageRequests.push({before, limit});
      return {token: state.pageRequests.length};
    },
    get_page: async (token) => {
      window.__calls.push(["get_page", token]);
      return pages[token - 1] ? copy(pages[token - 1]) : {pending: true};
    },
    get_scene: async () => ({cells: [], columns: 1, rows: 1, palette: []}),
    save_preferences: async (patch) => {
      window.__calls.push(["save_preferences", copy(patch)]);
      Object.assign(CFG.prefs, patch);
      if (patch.lang) state.lang = patch.lang;
      return boot();
    },
    toggle_fullscreen: async () => ({ok: true}),
    quit: async () => ({ok: true}),
  }};
})();"""


def _script(details, pages=(), info=None):
    """An init script that installs the bridge, the synthetic data and a call log in window.__state."""
    payload = {"strings": STRINGS, "prefs": PREFS, "run": RUN, "snapshot": SNAPSHOT,
               "details": details, "pages": list(pages), "info": info or INFO}
    return _STUB.replace("__CONFIG__", json.dumps(payload))


@pytest.fixture(scope="module")
def assets_url():
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(ASSETS)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/index.html"
    server.shutdown()
    server.server_close()


def _cached_chromium():
    """A browser build cached by another Playwright revision, when this package's own build is missing."""
    home = Path.home()
    roots = (home / "AppData" / "Local" / "ms-playwright", home / ".cache" / "ms-playwright",
             home / "Library" / "Caches" / "ms-playwright")
    patterns = ("chromium-*/chrome-win64/chrome.exe", "chromium-*/chrome-linux/chrome",
                "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium")
    for root in roots:
        for pattern in patterns:
            found = sorted(root.glob(pattern))
            if found:
                return found[-1]
    return None


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as manager:
        try:
            instance = manager.chromium.launch(headless=True)
        except Exception as first:  # missing browser build, sandbox or display
            executable = _cached_chromium()
            if executable is None:
                pytest.skip(f"Chromium is not available: {first}")
            try:
                instance = manager.chromium.launch(headless=True, executable_path=str(executable))
            except Exception as exc:
                pytest.skip(f"Chromium is not available: {exc}")
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    page.set_default_timeout(15000)
    yield page
    context.close()


def _open(page, url, script):
    page.add_init_script(script=script)
    page.goto(url)
    page.wait_for_function("() => window.__state && window.__state.detailCalls >= 1")


def _poll(page):
    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")


def test_unpaged_detail_moves_the_older_cursor(page, assets_url):
    window_a = [{"ts": 1, "text": "initial message"}]
    window_b = [{"ts": 1, "text": "initial message"}, {"ts": 2, "text": "new message"}]
    _open(page, assets_url, _script([{"offset": 5, "messages": window_a},
                                     {"offset": 6, "messages": window_b}],
                                    pages=[{"messages": [{"ts": 0, "text": "older"}], "next": 0}]))
    page.locator("#tab-msg").click()
    page.locator(".load-older").wait_for()
    page.evaluate("() => { window.__state.snapshotRevision = 2; }")
    _poll(page)
    page.wait_for_function("() => window.__state.detailCalls >= 2")
    page.evaluate("() => { window.__state.snapshotMuted = true; }")
    page.wait_for_function("() => { const b = document.querySelector('.load-older'); return b && !b.disabled; }")
    page.locator(".load-older").click()
    page.wait_for_function("() => window.__state.pageRequests.length === 1")
    assert page.evaluate("() => window.__state.pageRequests[0].before") == 6


def test_paged_cache_keeps_its_cursor_when_a_new_message_arrives(page, assets_url):
    initial = [{"ts": 5, "text": "m5"}, {"ts": 6, "text": "m6"}, {"ts": 7, "text": "m7"}]
    newer = initial + [{"ts": 8, "text": "m8"}]
    pages = [{"messages": [{"ts": 3, "text": "m3"}, {"ts": 4, "text": "m4"}], "next": 3},
             {"messages": [{"ts": 2, "text": "m2"}], "next": 0}]
    _open(page, assets_url, _script([{"offset": 5, "messages": initial},
                                     {"offset": 5, "messages": newer}], pages=pages))
    page.locator("#tab-msg").click()
    page.locator(".load-older").wait_for()
    page.locator(".load-older").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#pane-msg .msg').length === 5 &&"
        " document.querySelector('.load-older') && !document.querySelector('.load-older').disabled")
    page.evaluate("() => { window.__state.snapshotRevision = 2; }")
    _poll(page)
    page.wait_for_function("() => window.__state.detailCalls >= 2")
    page.evaluate("() => { window.__state.snapshotMuted = true; }")
    page.wait_for_function("() => document.querySelectorAll('#pane-msg .msg').length === 6")
    page.locator(".load-older").click()
    page.wait_for_function("() => window.__state.pageRequests.length === 2")
    assert page.evaluate("() => window.__state.pageRequests.map((r) => r.before)") == [5, 3]


def test_two_older_pages_load_and_prepend_once(page, assets_url):
    first = [{"ts": 1, "text": "page one a"}, {"ts": 2, "text": "page one b"}]
    second = [{"ts": 3, "text": "page two a"}]
    initial = [{"ts": 4, "text": "window a"}, {"ts": 5, "text": "window b"}, {"ts": 6, "text": "window c"}]
    pages = [{"messages": first, "next": 100}, {"messages": second, "next": 50}]
    _open(page, assets_url, _script([{"offset": 105, "messages": initial}], pages=pages))
    page.evaluate("() => { window.__state.snapshotMuted = true; }")  # nothing new arrives while paging
    page.locator("#tab-msg").click()
    page.locator(".load-older").wait_for()
    page.locator(".load-older").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#pane-msg .msg').length === 5 &&"
        " document.querySelector('.load-older') && !document.querySelector('.load-older').disabled")
    page.locator(".load-older").click()
    page.wait_for_function("() => document.querySelectorAll('#pane-msg .msg').length === 6")
    assert page.evaluate("() => window.__state.pageRequests.map((r) => r.before)") == [105, 100]
    body = page.locator("#pane-msg").inner_text()
    for text in ("page one a", "page one b", "page two a", "window a", "window b", "window c"):
        assert body.count(text) == 1


def test_language_change_refetches_server_rendered_detail(page, assets_url):
    _open(page, assets_url, _script([{"offset": 0, "messages": []}]))
    # No periodic detail refresh: only the language change may trigger the second get_detail.
    page.evaluate("() => { window.__state.snapshotMuted = true; }")
    page.locator("#tab-info").click()
    page.wait_for_function("() => document.querySelector('#pane-info dt')?.textContent === 'Agent status'")
    previous = page.evaluate("() => window.__state.detailCalls")
    page.locator("#btn-home").click()
    page.locator("#settings .set-row").first.wait_for()
    lang_row = page.locator("#settings .set-row").first
    lang_row.locator("button.step").last.click()  # auto -> en
    page.wait_for_function("() => window.__state.lang === 'en'")
    lang_row.locator("button.step").last.click()  # en -> es
    page.wait_for_function("() => window.__state.lang === 'es'")
    page.wait_for_function(f"() => window.__state.detailCalls > {previous}", timeout=5000)
    page.wait_for_function("() => document.querySelector('#settings .set-row .set-label')?.textContent === 'Idioma'")
    page.locator("#btn-open").click()
    page.locator("#tab-info").click()
    page.wait_for_function("() => document.querySelector('#pane-info dt')?.textContent === 'Estado del agente'")
