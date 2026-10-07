"""Claude Code: transcripts in $CLAUDE_CONFIG_DIR/projects/<cwd-slug>/<session>.jsonl."""
from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from pathlib import Path

from bitacora.model import Event, ProcInfo, Run, Session
from bitacora.procs import Proc
from bitacora.providers.base import Provider
from bitacora.util import UUID_RE, count_lines, first_line, parse_iso, rel_to

VALUE_FLAGS = {"--model", "--effort", "--permission-mode", "--output-format", "--input-format",
               "--allowedTools", "--allowed-tools", "--disallowedTools", "--disallowed-tools",
               "--append-system-prompt", "--system-prompt", "--mcp-config", "--add-dir", "--agents",
               "--settings", "--permission-prompt-tool", "--max-turns", "--fallback-model",
               "--setting-sources", "--plugin-dir", "--agent", "--betas", "--json-schema", "--name", "-n"}
SHELL_TOOLS = {"Bash", "PowerShell"}
READ_TOOLS = {"Read": "read", "Grep": "search", "Glob": "list_files", "LS": "list_files"}
EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
ASK_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
TITLE_KEYS = (("custom-title", "customTitle"), ("agent-name", "agentName"),
              ("ai-title", "aiTitle"), ("summary", "summary"))
ENTRYPOINTS = {"cli": ("cli", False), "sdk-cli": ("print", True), "sdk-ts": ("sdk", True),
               "sdk-py": ("sdk", True), "claude-desktop": ("desktop", False),
               "claude-vscode": ("ide", False), "claude-jetbrains": ("ide", False)}
INTERRUPTED = "[Request interrupted by user"


def parse_claude_args(argv: list) -> dict:
    info: dict = {"headless": False}
    args = argv[1:]
    fork = False
    i = 0
    while i < len(args):
        a = args[i]
        key, eq, inline = a.partition("=") if a.startswith("--") else (a, "", "")
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if key in ("-p", "--print"):
            info["headless"] = True
        elif key in ("-r", "--resume", "--session-id"):
            v = inline if eq else (nxt if UUID_RE.fullmatch(nxt or "") else "")
            if v and not eq:
                i += 1
            if v:
                info["session_id"] = v
        elif key == "--fork-session":
            fork = True
        elif key == "--dangerously-skip-permissions":
            info["sandbox"] = "bypassPermissions"
        elif key in VALUE_FLAGS:
            v = inline if eq else nxt
            i += 0 if eq else 1
            if key == "--model":
                info["model"] = v
            elif key == "--effort":
                info["effort"] = v
            elif key == "--permission-mode":
                info["sandbox"] = v
        i += 1
    if fork:  # a forked session writes to a new file
        info.pop("session_id", None)
    return info


def is_claude_code(p: Proc) -> bool:
    if not p.cmdline or any(a.startswith("--type=") for a in p.cmdline[1:]):
        return False
    exe = p.exe.replace("\\", "/")
    if p.name in ("claude.exe", "claude"):
        # The desktop app's own shell is also called Claude; Claude Code lives elsewhere.
        return not ("/windowsapps/" in exe or "/anthropicclaude/" in exe or ".app/contents/macos/" in exe
                    or exe.endswith("/app/claude.exe"))
    if p.name.startswith(("node", "bun")):
        return any("@anthropic-ai/claude-code" in a.replace("\\", "/") for a in p.cmdline[1:3])
    return False


def slug(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", cwd)


def block_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", ""))
        elif isinstance(b, str):
            parts.append(b)
    return "\n".join(parts)


class ClaudeSession(Session):
    provider = "claude"
    skip_markers = (b'"type":"attachment"', b'"type":"file-history', b'"type":"queue-operation"',
                    b'"type":"last-prompt"', b'"type":"atis-latch"')

    def reset(self) -> None:
        super().reset()
        self._titles: dict[str, str] = {}
        self._usage_ids: set[str] = set()
        self._tools: dict[str, tuple[str, dict, float | None]] = {}

    def handle(self, o: dict) -> None:
        typ = o.get("type")
        for kind, key in TITLE_KEYS:
            if typ == kind and o.get(key):
                self._titles[kind] = o[key]
                self.title = next(self._titles[k] for k, _ in TITLE_KEYS if k in self._titles)
                return
        if typ not in ("user", "assistant") or o.get("isSidechain"):
            return
        ts = parse_iso(o.get("timestamp"))
        if ts:
            self.last_ts = ts
            if self.start is None:
                self.start = ts
        self.session_id = self.session_id or o.get("sessionId")
        self.cwd = self.cwd or o.get("cwd")
        if o.get("entrypoint") and self.origin == "?":
            self.origin, self.headless = ENTRYPOINTS.get(o["entrypoint"], (o["entrypoint"], False))
        if o.get("permissionMode"):
            self.sandbox = o["permissionMode"]
        msg = o.get("message") or {}
        if typ == "user":
            self._user(o, msg, ts)
        else:
            self._assistant(o, msg, ts)

    # -- user side: prompts and tool results
    def _user(self, o: dict, msg: dict, ts: float | None) -> None:
        if o.get("isMeta") or o.get("isCompactSummary"):
            return
        content = msg.get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    self._tool_result(b, o, ts)
        text = block_text(content).strip()
        if not text:
            return
        if text.startswith(INTERRUPTED):
            self.state = "aborted"
            self.add(Event(ts, "err", "user", sub="aborted"))
            return
        if text.startswith("<"):  # harness notices: task notifications, slash command output
            tag = re.match(r"<([\w-]+)", text)
            self.add(Event(ts, "note", tag.group(1) if tag else first_line(text, 60), text))
            return
        if not self.first_prompt:
            self.first_prompt = text
        self.state = "running"
        self.add(Event(ts, "turn", "", sub="started"))
        self.add(Event(ts, "user", first_line(text, 300), text))

    # -- assistant side: thinking, text, tool calls, usage
    def _assistant(self, o: dict, msg: dict, ts: float | None) -> None:
        model = msg.get("model")
        if model and not model.startswith("<"):
            self.model = model
        usage = msg.get("usage") or {}
        mid = msg.get("id")
        if usage and mid not in self._usage_ids:
            if mid:
                self._usage_ids.add(mid)
            ctx = sum(usage.get(k) or 0 for k in
                      ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            self.tokens += ctx + (usage.get("output_tokens") or 0)
            self.ctx_used = ctx or self.ctx_used
        if o.get("isApiErrorMessage"):
            self.add(Event(ts, "err", first_line(block_text(msg.get("content")), 300)))
            return
        text_parts = []
        for b in msg.get("content") or []:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt in ("thinking", "redacted_thinking"):
                self.add_think(ts, (b.get("thinking") or "").strip())
            elif bt == "text" and b.get("text", "").strip():
                txt = b["text"]
                text_parts.append(txt)
                self.messages.append((ts, txt))
                self.add(Event(ts, "msg", first_line(txt, 400), txt))
            elif bt == "tool_use":
                self._tool_use(b, ts)
        if msg.get("stop_reason") == "end_turn":
            self.state = "complete"
            if text_parts:
                self.last_agent_message = "\n".join(text_parts)
            elif self.messages:
                self.last_agent_message = self.messages[-1][1]
            self.add(Event(ts, "turn", "", sub="complete"))
        elif msg.get("stop_reason") == "tool_use":
            self.state = "running"

    def _tool_use(self, b: dict, ts: float | None) -> None:
        name, inp, tid = b.get("name", ""), b.get("input") or {}, b.get("id", "")
        self._tools[tid] = (name, inp, ts)
        if name in ASK_TOOLS:
            if name == "AskUserQuestion":
                qs = inp.get("questions") or []
                q = " | ".join(x.get("question", "") for x in qs if isinstance(x, dict)) or name
            else:
                q = "plan"
            self.open_questions[tid] = q
            self.add(Event(ts, "ask", first_line(q, 300), q, sub=name))
            return
        self.in_flight[tid] = Event(ts, "cmd" if name in SHELL_TOOLS or name in READ_TOOLS else "tool",
                                    self._describe(name, inp), sub=name)

    def _describe(self, name: str, inp: dict) -> str:
        if name in SHELL_TOOLS:
            return str(inp.get("command", ""))
        if name in READ_TOOLS:
            target = inp.get("file_path") or inp.get("pattern") or inp.get("path") or ""
            return rel_to(str(target), self.cwd)
        if name in ("Agent", "Task"):
            return f"agent: {inp.get('description') or inp.get('subagent_type') or ''}"
        return f"{name}({first_line(json.dumps(inp, ensure_ascii=False), 100)})"

    def _tool_result(self, b: dict, o: dict, ts: float | None) -> None:
        tid = b.get("tool_use_id", "")
        self.in_flight.pop(tid, None)
        name, inp, start = self._tools.pop(tid, ("", {}, None))
        ok = not b.get("is_error")
        out = block_text(b.get("content"))
        dur = (ts - start) if ts and start else 0.0
        if tid in self.open_questions:
            self.open_questions.pop(tid)
            return
        if not name:
            return
        if name in EDIT_TOOLS:
            if ok:
                self._file_change(name, inp, o.get("toolUseResult"), start or ts)
            else:
                self.add_cmd(Event(start or ts, "cmd", f"{name} {rel_to(str(inp.get('file_path', '')), self.cwd)}",
                                   out, dur=dur, ok=False, sub=name))
            return
        if name in SHELL_TOOLS or name in READ_TOOLS:
            self.add_cmd(Event(start or ts, "cmd", self._describe(name, inp), out, dur=dur, ok=ok,
                               code=None if ok else "error", sub=READ_TOOLS.get(name, "")))
            return
        self.add(Event(start or ts, "tool" if ok else "err", self._describe(name, inp), out, sub=name, dur=dur))

    def _file_change(self, name: str, inp: dict, result, ts: float | None) -> None:
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        rel = rel_to(path, self.cwd)
        if name == "Write":
            created = isinstance(result, dict) and result.get("type") == "create"
            kind = "add" if created or rel not in self.files else "update"
            plus, minus, detail = count_lines(inp.get("content")), 0, inp.get("content") or ""
        elif name == "MultiEdit":
            edits = inp.get("edits") or []
            kind = "update"
            plus = sum(count_lines(e.get("new_string")) for e in edits)
            minus = sum(count_lines(e.get("old_string")) for e in edits)
            detail = "\n".join(_mini_diff(e.get("old_string"), e.get("new_string")) for e in edits)
        else:
            kind = "update"
            plus = count_lines(inp.get("new_string") or inp.get("new_source"))
            minus = count_lines(inp.get("old_string"))
            detail = _mini_diff(inp.get("old_string"), inp.get("new_string") or inp.get("new_source"))
        self.touch_file(rel, kind, ts, plus, minus)
        self.add(Event(ts, "file", rel, detail, sub=kind, plus=plus, minus=minus))


def _mini_diff(old: str | None, new: str | None) -> str:
    lines = ["-" + ln for ln in (old or "").splitlines()]
    return "\n".join(lines + ["+" + ln for ln in (new or "").splitlines()])


class ClaudeProvider(Provider):
    name = "claude"
    label = "Claude Code"
    marker = "claude"
    session_cls = ClaudeSession

    def home(self) -> Path:
        return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")

    def _projects(self) -> Path:
        return self.home() / "projects"

    def available(self) -> bool:
        return self._projects().exists()

    def procs(self, snap: list[Proc]) -> list[ProcInfo]:
        found = {p.pid: p for p in snap if is_claude_code(p)}
        wrappers = {p.ppid for p in found.values() if p.ppid in found}
        result = []
        for p in found.values():
            if p.pid in wrappers:  # a launcher whose child is the real binary
                continue
            info = parse_claude_args(p.cmdline)
            result.append(ProcInfo("claude", p.pid, p.create, p.cmdline, proc_cwd=p.cwd(), **info))
        return result

    @classmethod
    def born(cls, path: Path) -> float | None:
        """When the file was created. Forked sessions copy old records, so their content lies."""
        try:
            st = path.stat()
        except OSError:
            return None
        birth = getattr(st, "st_birthtime", None)
        if birth is None and os.name == "nt":
            birth = st.st_ctime
        return birth if birth is not None else cls.first_ts(path)

    @staticmethod
    def first_ts(path: Path) -> float | None:
        try:
            with open(path, "rb") as fh:
                for _ in range(40):
                    line = fh.readline()
                    if not line:
                        break
                    m = re.search(rb'"timestamp":\s*"([^"]+)"', line)
                    if m:
                        return parse_iso(m.group(1).decode())
        except OSError:
            pass
        return None

    def match(self, pi: ProcInfo, claimed: set[Path]) -> Path | None:
        projects = self._projects()
        if pi.session_id:
            hits = list(projects.glob(f"*/{pi.session_id}.jsonl"))
            if hits:
                return hits[0]
        if not pi.cwd:
            return None
        # A new or forked session: the file in the cwd's folder created right after the process.
        folder = projects / slug(pi.cwd)
        best = None
        for f in folder.glob("*.jsonl") if folder.exists() else []:
            if f in claimed:
                continue
            born = self.born(f)
            if born is None or born < pi.create - 10:
                continue
            if best is None or born < best[0]:
                best = (born, f)
        return best[1] if best else None

    def history(self, since: float) -> Iterable[Path]:
        projects = self._projects()
        if not projects.exists():
            return
        for f in projects.glob("*/*.jsonl"):
            try:
                if f.stat().st_mtime >= since:
                    yield f
            except OSError:
                continue

    def resume_cmd(self, run: Run) -> str | None:
        sid = run.session.session_id if run.session else None
        if not sid:
            return None
        cwd = run.cwd
        return f'cd "{cwd}"; claude --resume {sid}' if cwd else f"claude --resume {sid}"
