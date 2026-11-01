# Provider support

Bitácora reads the session files each agent already writes. None of these formats are documented, so support is
graded by how much of it is proven.

| Provider | Tier | Verified with | Source | Live process |
| --- | --- | --- | --- | --- |
| Codex | Verified | Codex CLI 0.161 | JSONL rollouts in `~/.codex/sessions` | Yes, matched to its session |
| Claude Code | Verified | Claude Code 2.1 | JSONL transcripts in `~/.claude/projects` | Yes, matched to its session |
| OpenCode | Best-effort | OpenCode 2.2.4 | SQLite database `~/.local/share/opencode/opencode.db` | No: one shared service runs every session |
| GitHub Copilot CLI | Not supported | — | Planned; its session files are not a stable surface yet | — |
| Antigravity | Unsupported | — | No passive session source has been found | — |

- **Verified:** synthetic fixtures, conformance tests and the versions listed; every declared capability is expected
  to work.
- **Best-effort:** fixtures and graceful degradation. The format changes often; a newer version may lose fields,
  and Bitácora shows the loss instead of guessing.
- **Unsupported:** Bitácora shows nothing for it.

Versions are the ones tested for this release. Other versions are best-effort until they are tested.

## What each provider answers

| Capability | Codex | Claude Code | OpenCode |
| --- | --- | --- | --- |
| Messages | Full | Full | Full |
| Commands and exit codes | Full | Full | Full |
| Files with lines added and removed | Full | Full | Partial: writes have no diff |
| Questions | Full | Full | Partial: the question tool only |
| Token total | Full | Full | Full |
| Context | Full | Full (window assumed from the model) | Partial: size known, window unknown |
| Quota | Full | Partial | None |
| Titles | Full | From the transcript | Full |
| Subagents | None | None | Partial: shown inside the parent session |
| Resume command | Full | Full | Partial: needs `opencode` on `PATH` |
| Stop | Yes | Yes | No |

Unknown values stay unknown: a missing exit code is not a success, and a missing window is not 0%.

## OpenCode notes

- The desktop app and the CLI share one background service and one database, so Bitácora cannot tell which one
  started a session. Its origin reads as Other.
- There is no process per session. A session reads as running while its database row is busy and recently
  updated; it never has a PID or a Stop action.
- Subagent sessions are not listed on their own. Archived sessions are not listed.
- The database is opened read-only. When OpenCode is not running, it is opened immutable so that no `-wal` or `-shm`
  file is created next to it. Only the session tables are read; account and credential tables are never queried.
- `OPENCODE_DB` points Bitácora at one specific database. Without it, channel builds (`opencode-<channel>.db`) in
  the same folder are read too.
- Sessions that live inside WSL or on a remote OpenCode server are not visible from a Windows-side Bitácora.

## Turning a provider off

Add a `[providers]` table to `config.toml` (`bitacora --paths` shows where it lives):

```toml
[providers]
opencode = false
```
