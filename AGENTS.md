# Working on Bitácora

- Python 3.11+, Textual for the UI, psutil for processes. Install with `pip install -e ".[dev]"`.
- Checks before any commit: `ruff check .` and `pytest`. Both must pass on Windows, Linux and macOS (CI runs all three).
- Layout: `bitacora/providers/` (one module per agent), `model.py` (events, sessions, runs), `collector.py`
  (joins processes and session files), `ui/` (app, setup screen, masthead), `i18n.py` (every UI string in
  English and Spanish).
- Every user-facing string goes through `i18n.t()` with both languages; a test checks that the pair exists and
  that placeholders match.
- Tests run on synthetic sessions from `bitacora/demo.py`. Never commit real session data, paths from a real
  machine or screenshots of real sessions; regenerate `docs/*.svg` with `python scripts/screenshot.py en|es`.
- Agent session formats are undocumented. When a provider changes, update the parser, the demo generator and
  the tests together.
- Files are LF. Commits follow plain imperative messages.
