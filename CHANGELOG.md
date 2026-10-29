# Changelog

## 1.2.0 (2026-10-29)

### Reliability

- Keep each provider's last good data when a scan fails, name the failed providers in a partial-reader notice and keep working with what remains. Preserve provider histories across failures and restarts, and stop rescanning an empty Codex tree on every pass.
- Validate the Codex checkpoint's path and file pattern, and rewrite it only when its contents change.
- Freeze message values, bound log and result reads, and serve older messages from the reader through expiring page tokens instead of unbounded transfers.
- Validate the history window (`--hours`) as a positive number capped at 72 hours, and finish process termination without holding the collection lock.
- Show Result from the end of the file in both interfaces, with the same bounded-read note. Refresh Info when questions, process identity, folder or result/log paths change.

### Desktop

- Request detail aspects by revision: unchanged aspects are omitted, and older messages load page by page with a bounded DOM.
- Keep Load older usable across consecutive pages, merge message offsets correctly after deltas and resets, and refresh server-provided text after a language change.
- Use declared provider capabilities to decide which prefixes to scan and when a resume command exists.
- Show the partial-reader banner without hiding the last good data or the actions that do not depend on the failed provider.

### Performance

- Reuse scene colors and styles, locate the beam with a bisect over precomputed angles, skip empty rows and cache the beacon gradient by level. Rendered frames stay byte-identical while frame time drops 16–21% (about 22% at 200×50).
- Add `scripts/bench_home.py` for repeatable frame, segment and desktop-payload measurements, and golden frame hashes that guard the visual output.

### Diagnostics

- Add `bitacora --diagnostics`: local JSON counters, latencies, memory and bounded quarantine metadata (record kind and exception class only). It includes no message text, commands, file paths or network access.

### Documentation

- Document the revisioned detail contract, message paging, partial reads and the desktop partial-reader banner; update both READMEs and the privacy note.

## 1.1.1 (2026-10-10)

### Documentation

- Rewrite the README's Why section around the questions background runs raise: audit of what an agent did, context window, waiting for input, stuck runs, quota limits and safe Stop.
- Add a looping demo GIF to the README header in English and Spanish, and show the desktop and terminal screenshots in the Use section.
- Move Install above the feature list and remove duplicated sorting and privacy details.

## 1.1.0 (2026-10-10)

### Desktop app

- Add a Windows desktop app with local assets, no console and one public `bitacora.exe`. Keep the terminal interface available from Python on Windows, Linux and macOS. Require WebView2 Runtime for the desktop app.
- Preserve the lighthouse, time-aware sky, falling code and themes. Remember window size and text size, and support Home, Help, F11 and all six detail tabs.

### Sessions and values

- Share normalized statuses across providers: question, starting, running, quiet, your turn, done, failed, limit reached, stopped and unknown. Show the evidence for running status in Details; questions without a process stop counting as pending after six hours without transcript activity.
- Normalize origins, access modes, context, cumulative token totals and quota windows. Keep unknown values unknown, show used quota percentages and reset states, and preserve model-specific Claude limits.
- Read sessions in a separate process, synchronize changed events and rewritten files, retry transient errors and show reader health. Avoid ambiguous process associations and cached PID reuse.
- Preserve session memory during migration and failed writes, merge concurrent preference changes, and validate process identity again when confirming Stop. Keep notifications within the current view.

### Interface parity

- Keep fourteen columns and horizontal scrolling in both tables; pin desktop Status and Name. Use short localized Agent labels in the table and full names in the selected session and Details.
- Order questions first, then live sessions and newest starts. Cycle heading sorting through ascending, descending and default order; support `<`, `>` and Ctrl+R in both interfaces and keyboard-focusable desktop headings.
- Adapt headers, selected-session panels, footers and Home to available space. Set the desktop minimum to 800×560; keep Stop visible and move secondary actions into an overflow menu.
- Match question, maximize-list and plain-text shortcuts across interfaces. Toggle Plain text in Messages and Result with `m`; offer selectable text when terminal clipboard copying fails.
- Cap history at 72 hours, default to Monokai, keep readable theme contrast and update language and shared settings in place. Use singular and plural labels for session counts.
- Add a divider between the header and list that sweeps while sessions run and turns amber while an answer is pending. Keep the desktop animation within its container and use the same Windows taskbar identity and icon for source and packaged launches.

### Release checks

- Expand the synthetic demo to sixteen runs across six projects, covering questions, live sessions, recovered failures and usage limits for both agents.
- Validate release tags against the package version. Wait for the cross-platform test matrix, package validation and a hidden frozen-app smoke test before publishing. Require Textual 8.2.8 or newer.

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
