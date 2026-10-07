# Changelog

## 1.0.0 (2026-10-07)

First public release.

- Live dashboard for coding agents working in the background, with providers for Codex and Claude Code.
- Every session with its status, an activity feed (commands, edits, messages, thinking time) and tabs for messages, files, log, result and details.
- Matches live processes to their sessions by id, or by working directory and start time, including forked Claude Code sessions.
- Finds a run's log from the parent shell's redirect and remembers result and log paths after the run ends.
- Codex quota, tokens and context per session.
- Views (background runs, everything, interactive, live only) and custom views in `config.toml`.
- Home and setup screen with live counts per view, language, history window, notifications and theme.
- Notifications when a run finishes, stops or asks a question.
- Kill with confirmation, open result or log, copy the resume command.
- English and Spanish interface.
- `--demo` mode with generated sessions, also used by the tests and the README screenshots.
