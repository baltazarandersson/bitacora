import errno
import os
import pickle
import time
from pathlib import Path
from types import SimpleNamespace

from bitacora.collector import Collector
from bitacora.model import Event, ProcInfo, Run, Session
from bitacora.providers import all_providers
from bitacora.store import Memory
from bitacora.worker import Mirror, Packer, Source, _history_page, _touched_since


def transfer(payload):
    """Exercise the same object boundary as multiprocessing.Pipe, not shared references."""
    return pickle.loads(pickle.dumps(payload))


def test_mirror_preserves_normalized_fields_for_every_demo_session(demo_env):
    memory = Memory()
    for key, entry in demo_env["state"].items():
        memory.remember(key, entry)
    collector = Collector(all_providers(), memory, hours=24, scan_procs=False)
    original = collector.collect()
    mirrored = Mirror().apply(transfer(Packer().pack(original, collector.quotas)))
    expected_by_key = {run.key: run for run in original}
    fields = ("origin_kind", "origin_raw", "effort_source", "access_kind", "usage_basis",
              "context_window_assumed")

    assert len(original) == len(mirrored) == 19
    for run in mirrored:
        source = expected_by_key[run.key]
        assert run.origin_kind == source.origin_kind
        assert (run.session is None) == (source.session is None)
        if run.session and source.session:
            assert {field: getattr(run.session, field) for field in fields} == {
                field: getattr(source.session, field) for field in fields
            }


def test_packer_resends_mutated_events_and_mirror_upserts_by_sequence(tmp_path):
    session = Session(tmp_path / "synthetic.jsonl")
    session.provider = "synthetic"
    run = Run("synthetic:1", "synthetic", session, None, "sample")
    packer, mirror = Packer(), Mirror()

    session.add_think(10.0, "first")
    mirror.apply(transfer(packer.pack([run], {})))
    session.add_think(20.0, "second")
    payload = transfer(packer.pack([run], {}))
    assert payload["runs"][0]["session"]["events"][0].count == 2
    mirror.apply(payload)
    mirrored = mirror.sessions[("synthetic", "synthetic:1")]
    event = mirrored.last_event
    assert event.count == 2 and event.dur == 10.0 and event.detail == "first\nsecond"
    assert len(mirrored.events) == 1


def test_reset_generation_replaces_the_mirrored_event_stream(tmp_path):
    session = Session(tmp_path / "synthetic.jsonl")
    session.provider = "synthetic"
    run = Run("synthetic:1", "synthetic", session, None, "sample")
    packer, mirror = Packer(), Mirror()

    session.add(Event(1.0, "msg", "old"))
    mirror.apply(transfer(packer.pack([run], {})))
    old_generation = session.gen
    session.reset()
    assert session.gen != old_generation
    for i in range(3):
        session.add(Event(float(i), "msg", f"new-{i}"))

    payload = transfer(packer.pack([run], {}))
    assert payload["runs"][0]["session"]["reset"] is True
    mirror.apply(payload)
    assert [(e.seq, e.text) for e in mirror.sessions[("synthetic", "synthetic:1")].events] == [
        (1, "new-0"), (2, "new-1"), (3, "new-2")]


def test_mirror_keys_two_pathless_sessions_by_run_identity():
    first, second = Session(None), Session(None)
    first.provider = second.provider = "synthetic"
    first.add(Event(1.0, "msg", "first"))
    second.add(Event(2.0, "msg", "second"))
    runs = [Run("synthetic:a", "synthetic", first, None, "a"),
            Run("synthetic:b", "synthetic", second, None, "b")]

    payload = transfer(Packer().pack(runs, {}))
    assert [item["session"]["path"] for item in payload["runs"]] == [None, None]
    mirror = Mirror()
    mirror.apply(payload)

    assert set(mirror.sessions) == {("synthetic", "synthetic:a"), ("synthetic", "synthetic:b")}
    assert [event.text for event in mirror.sessions[("synthetic", "synthetic:a")].events] == ["first"]
    assert [event.text for event in mirror.sessions[("synthetic", "synthetic:b")].events] == ["second"]


def test_pathless_session_update_is_a_noop():
    session = Session(None)
    before = (session.offset, session.records_total, session.events_total, session.discarded)

    assert session.update() is False
    assert (session.offset, session.records_total, session.events_total, session.discarded) == before
    assert session.read_error is None


def test_touched_since_uses_last_ts_when_there_is_no_path():
    now = time.time()

    recent = Session(None)
    recent.last_ts = now
    assert _touched_since(recent, now - 3600) is True

    old = Session(None)
    old.last_ts = now - 2 * 3600
    assert _touched_since(old, now - 3600) is False

    undated = Session(None)
    assert undated.last_ts is None
    assert _touched_since(undated, now - 3600) is True


def test_a_pathless_history_run_stays_in_the_window_by_last_ts():
    recent, old = Session(None), Session(None)
    for session in (recent, old):
        session.provider = "synthetic"
        session.state = "complete"
    recent.last_ts = time.time()
    old.last_ts = time.time() - 2 * 3600
    runs = [Run("synthetic:recent", "synthetic", recent, None, "recent"),
            Run("synthetic:old", "synthetic", old, None, "old")]
    assert not any(run.live for run in runs)

    source = Source(hours=1, scan_procs=False, process=False)
    source._q.put(transfer(Packer().pack(runs, {})))
    shown = source.poll()

    assert [run.key for run in shown] == ["synthetic:recent"]


def test_pack_and_mirror_transport_provider_errors(tmp_path):
    session = Session(tmp_path / "synthetic.jsonl")
    session.provider = "synthetic"
    run = Run("synthetic:1", "synthetic", session, None, "sample")
    packer, mirror = Packer(), Mirror()

    assert packer.pack([run], {})["errors"] == {}
    mirror.apply(transfer(packer.pack([run], {}, {"codex": "RuntimeError"})))
    assert mirror.errors == {"codex": "RuntimeError"}

    mirror.apply(transfer(packer.pack([run], {}, {"claude": "TimeoutError"})))
    assert mirror.errors == {"claude": "TimeoutError"}

    mirror.apply(transfer(packer.pack([run], {})))
    assert mirror.errors == {}


def test_source_applies_provider_errors_and_reports_partial_health():
    source = Source(hours=1, scan_procs=False, process=False)
    source._q.put(transfer(Packer().pack([], {}, {"codex": "RuntimeError"})))

    assert source.poll() == []
    assert source.provider_errors == {"codex": "RuntimeError"}
    assert source.health == "partial"

    source._q.put(transfer(Packer().pack([], {}, {})))
    source.poll()
    assert source.provider_errors == {}
    assert source.health == "ready"


def test_provider_health_travels_with_the_payload_and_only_degraded_is_a_problem():
    from bitacora.sources import ProviderHealth

    source = Source(hours=1, scan_procs=False, process=False)
    health = {
        "alpha": ProviderHealth("degraded", readers=1, records=5, parsed=2, discarded=3, details=("discarded",)),
        "beta": ProviderHealth("unavailable"),
        "gamma": ProviderHealth("ok", readers=1, records=4, parsed=4),
    }
    source._q.put(transfer(Packer().pack([], {}, {}, health)))
    assert source.poll() == []
    assert source.provider_health["alpha"] == {
        "state": "degraded", "readers": 1, "records": 5, "parsed": 2, "discarded": 3, "unknown": 0,
        "details": ["discarded"], "last_good": None}
    assert source.provider_health["beta"]["state"] == "unavailable"
    assert source.health == "degraded"

    source._q.put(transfer(Packer().pack([], {}, {"zeta": "RuntimeError"}, health)))
    source.poll()
    assert source.health == "partial"  # a failed provider outranks a degraded one

    source._q.put(transfer(Packer().pack([], {}, {}, {"beta": ProviderHealth("unavailable")})))
    source.poll()
    assert source.health == "ready"  # unavailable and healthy providers stay silent

    payload = Packer().pack([], {}, {})
    del payload["health"]  # a payload without the key is still accepted
    source._q.put(transfer(payload))
    source.poll()
    assert source.provider_health == {} and source.health == "ready"


def test_a_skipped_record_in_a_real_transcript_reaches_the_source_as_degraded(demo_env):
    claude_home = demo_env["homes"]["claude"]
    transcript = max(claude_home.rglob("*.jsonl"), key=lambda path: path.stat().st_mtime)
    transcript.write_text(transcript.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
    collector = Collector(all_providers(), Memory(), 24, scan_procs=False)
    runs = collector.collect()
    source = Source(hours=24, scan_procs=False, process=False)
    source._q.put(transfer(Packer().pack(runs, collector.quotas, collector.errors, collector.health)))
    source.poll()

    assert source.provider_health["claude"]["state"] == "degraded"
    assert source.provider_health["claude"]["discarded"] >= 1
    assert source.provider_health["codex"]["state"] == "ok"
    assert source.health == "degraded"


def test_source_health_order_is_error_loading_stale_partial_ready():
    source = Source(hours=1, scan_procs=False, process=False)
    source.mirror.errors = {"codex": "RuntimeError"}

    assert source.health == "loading"
    source.last_update = time.monotonic()
    assert source.health == "partial"
    source.mirror.errors = {}
    assert source.health == "ready"
    source.mirror.errors = {"codex": "RuntimeError"}
    source.last_update = time.monotonic() - 11
    assert source.health == "stale"
    source.error = "synthetic"
    assert source.health == "error"


def test_source_recovers_from_transient_read_error_and_reports_health(monkeypatch, tmp_path):
    import bitacora.worker as worker

    class FlakyCollector:
        def __init__(self, *_args):
            self.quotas = {}
            self.calls = 0

        def collect(self):
            self.calls += 1
            if self.calls == 1:
                raise PermissionError(errno.EACCES, "synthetic read failure", str(tmp_path / "private.jsonl"))
            return []

    monkeypatch.setattr(worker, "Collector", FlakyCollector)
    monkeypatch.setattr(worker, "all_providers", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(worker, "Memory", lambda: object())
    source = Source(hours=1, scan_procs=False, interval=0.01, process=False)
    assert source.health == "loading"
    source.start()

    deadline = time.monotonic() + 4
    saw_error = False
    while time.monotonic() < deadline and source.last_update is None:
        source.poll()
        saw_error |= source.error is not None
        time.sleep(0.01)
    assert saw_error
    assert source.last_update is not None and source.health == "ready"
    assert "private.jsonl" not in (source.error or "")

    source.stop()
    assert source._thread is not None and not source._thread.is_alive()


def test_source_reports_dead_thread_and_stale_data(monkeypatch):
    class DeadThread:
        ident = 1

        def is_alive(self):
            return False

        def join(self, timeout):
            assert timeout == 2

    source = Source(hours=1, scan_procs=False, process=False)
    source._started = True
    source._thread = DeadThread()
    source.poll()
    assert source.health == "error"
    assert source.error == "Reader thread stopped unexpectedly."
    source.stop()

    source.last_update = time.monotonic() - 11
    source.error = None
    assert source.health == "stale"


def test_source_process_stop_joins_closes_and_terminates_if_needed():
    class FakeConn:
        closed = False

        def send(self, _cmd):
            pass

        def close(self):
            self.closed = True

    class FakeProcess:
        pid = 321
        exitcode = 0
        alive = True

        def __init__(self):
            self.joins = 0
            self.terminated = False

        def join(self, timeout):
            assert timeout in (1, 2)
            self.joins += 1

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

    source = Source(hours=1, scan_procs=False, process=True)
    child, conn = FakeProcess(), FakeConn()
    source._proc, source._conn = child, conn
    source.stop()

    assert child.terminated and child.joins == 2 and conn.closed
    assert source._conn is None


def test_a_shorter_window_drops_older_history_from_a_payload_built_before_the_change(demo_env):
    memory = Memory()
    for key, entry in demo_env["state"].items():
        memory.remember(key, entry)
    wide = Collector(all_providers(), memory, hours=168, scan_procs=False)
    late = transfer(Packer().pack(wide.collect(), wide.quotas))  # the reader has not answered the new window yet
    source = Source(hours=168, scan_procs=False, process=False)
    source.set_hours(6)
    source._q.put(late)  # thread mode delivers payloads through this queue
    shown = {run.key for run in source.poll()}
    narrow = Collector(all_providers(), Memory(), hours=6, scan_procs=False)
    assert shown == {run.key for run in narrow.collect()}
    assert len(shown) < len(late["runs"])


def test_an_old_session_written_again_stays_in_a_shorter_window(demo_env):
    memory = Memory()
    for key, entry in demo_env["state"].items():
        memory.remember(key, entry)
    wide = Collector(all_providers(), memory, hours=168, scan_procs=False)
    old = next(run for run in wide.collect() if run.label == "Fix login redirect loop")  # last event 7 h ago
    os.utime(old.session.path)  # its file was written again just now
    late = transfer(Packer().pack(wide.collect(), wide.quotas))
    source = Source(hours=168, scan_procs=False, process=False)
    source.set_hours(6)
    source._q.put(late)
    assert old.key in {run.key for run in source.poll()}


def test_a_shorter_window_narrows_the_runs_already_shown_before_the_reader_answers(demo_env):
    memory = Memory()
    for key, entry in demo_env["state"].items():
        memory.remember(key, entry)
    wide = Collector(all_providers(), memory, hours=168, scan_procs=False)
    source = Source(hours=168, scan_procs=False, process=False)
    payload = transfer(Packer().pack(wide.collect(), wide.quotas))
    source._q.put(payload)
    assert len(source.poll()) == len(payload["runs"])
    source.set_hours(6)  # no payload is queued: the runs already shown are narrowed at once
    narrow = Collector(all_providers(), Memory(), hours=6, scan_procs=False)
    shown = source.poll()
    assert {run.key for run in shown} == {run.key for run in narrow.collect()}
    assert source.poll() is None  # nothing changed since


def test_history_page_slices_and_validates_the_cursor():
    run = SimpleNamespace(key="k", session=SimpleNamespace(messages=[(float(i), f"m{i}") for i in range(450)]))
    page = _history_page([run], 7, "k", 450, 200)
    assert page == {"token": 7, "key": "k", "total": 450, "next": 250,
                    "messages": [{"ts": float(i), "text": f"m{i}"} for i in range(250, 450)]}
    middle = _history_page([run], 8, "k", 250, 200)
    assert middle["next"] == 50 and [m["text"] for m in middle["messages"]] == [f"m{i}" for i in range(50, 250)]
    last = _history_page([run], 9, "k", 50, 200)
    assert last["next"] is None and [m["text"] for m in last["messages"]] == [f"m{i}" for i in range(0, 50)]
    for args in ((True, "k", 0, 10), (1, "k", -1, 10), (1, "k", 0, 0), (1, "missing", 0, 10)):
        assert "error" in _history_page([run], *args)


def test_page_tokens_stay_pending_until_answered_then_expire():
    source = Source(hours=1, scan_procs=False, process=False)
    token = source.request_page("k", 100, 50)
    assert source.take_page(token) is None  # emitted, but the reader has not answered yet
    page = {"token": token, "key": "k", "total": 200, "next": 0, "messages": []}
    source._q.put(transfer({"page": page}))  # the same envelope the reader sends
    source.poll()
    assert source.take_page(token) == page
    assert source.take_page(token) == {"error": "expired"}
    assert source.take_page(token + 1) == {"error": "invalid"}
    assert source.take_page("1") == {"error": "invalid"}


def test_page_token_tracking_is_bounded_and_evicted_pages_expire():
    source = Source(hours=1, scan_procs=False, process=False)
    issued = [source.request_page("k", 10, 1) for _ in range(20)]
    assert len(source._page_pending) == 16
    for token in issued:
        source._store_page({"token": token, "key": "k", "total": 1, "next": None, "messages": []})
    assert len(source._pages) == 16
    assert source.take_page(issued[0]) == {"error": "expired"}
    assert source.take_page(issued[-1])["token"] == issued[-1]
    assert source.take_page(issued[-1] + 1) == {"error": "invalid"}


def test_reader_serves_older_message_pages(monkeypatch):
    class PageCollector:
        def __init__(self, *args, **kwargs):
            self.hours = 24
            self.quotas = {}
            self.errors = {}
            self.session = Session(Path("page-source.jsonl"))
            self.session.messages.extend((float(i), f"m{i}") for i in range(450))

        def collect(self):
            return [Run("k", "codex", self.session, None, "page")]

    monkeypatch.setattr("bitacora.worker.all_providers", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("bitacora.worker.Memory", lambda: object())
    monkeypatch.setattr("bitacora.worker.Collector", PageCollector)
    source = Source(hours=24, scan_procs=False, interval=0.02, process=False)
    source.start()
    try:
        token = source.request_page("k", 450, 200)
        deadline = time.monotonic() + 5
        page = None
        while page is None and time.monotonic() < deadline:
            source.poll()
            page = source.take_page(token)
            if page is None:
                time.sleep(0.02)
        assert page is not None
        assert page["next"] == 250 and page["total"] == 450
        assert [m["text"] for m in page["messages"][:2]] == ["m250", "m251"]
    finally:
        source.stop()


def test_packed_runs_keep_the_provider_supplied_origin_kind(tmp_path):
    session = Session(tmp_path / "synthetic.jsonl")
    session.origin_kind = "other"
    closed = Run("synthetic:1", "synthetic", session, None, "sample", meta={"origin_kind": "cli"})
    assert closed.origin_kind == "cli"

    mirrored = Mirror().apply(transfer(Packer().pack([closed], {})))[0]
    assert mirrored.meta == {"origin_kind": "cli"}
    assert mirrored.origin_kind == "cli"

    plain = Run("synthetic:2", "synthetic", None,
                ProcInfo("synthetic", 7, 1.0, ["synthetic"], headless=True), "plain")
    assert plain.origin_kind == "background"
    plain_mirrored = Mirror().apply(transfer(Packer().pack([plain], {})))[0]
    assert plain_mirrored.meta == {}
    assert plain_mirrored.origin_kind == "background"

    other_session = Session(tmp_path / "other.jsonl")
    other_session.origin_kind = "other"
    other = Run("synthetic:3", "synthetic", other_session,
                ProcInfo("synthetic", 8, 1.0, ["synthetic"], headless=True), "other",
                meta={"origin_kind": "other"})
    assert other.origin_kind == "other"  # a provider-supplied kind is authoritative, "other" included

    # Without provider metadata, Run keeps the historical session/headless fallback.
    fallback = Run("synthetic:4", "synthetic", other_session,
                   ProcInfo("synthetic", 9, 1.0, ["synthetic"], headless=True), "fallback")
    assert fallback.origin_kind == "background"  # meta-less fallback: session kind "other" + headless


def test_provider_origin_kind_keeps_head_raw_first_branch(tmp_path):
    from bitacora.providers.claude import ClaudeProvider
    from bitacora.providers.codex import CodexProvider

    session = Session(tmp_path / "synthetic.jsonl")
    session.origin_kind = "other"
    session.headless = True
    for provider, raw in ((ClaudeProvider(), "claude-zed"), (ClaudeProvider(), "weird"),
                          (CodexProvider(), "weird")):
        session.origin_raw = raw
        assert provider.origin_kind(session) == "other"  # unknown raw stays "other" even when headless

    session.origin_raw = "sdk-cli"
    assert ClaudeProvider().origin_kind(session) == "background"

    session.origin_kind = "desktop"
    assert ClaudeProvider().origin_kind(session) == "desktop"  # mapped kind wins when the raw value is valid

    session.origin_kind = "other"
    session.origin_raw = "?"
    session.headless = False
    assert ClaudeProvider().origin_kind(session) == "other"
    assert CodexProvider().origin_kind(session) == "other"

    session.origin_raw = ""
    session.headless = True
    # A missing raw value ignores a stale session kind, as HEAD did.
    assert ClaudeProvider().origin_kind(session) == "background"
    assert CodexProvider().origin_kind(session) == "background"


def test_a_reader_error_without_a_failed_scan_still_reads_partial():
    from bitacora.sources import ProviderHealth

    source = Source(hours=1, scan_procs=False, process=False)
    health = {"alpha": ProviderHealth("error", readers=1, details=("read_error",)),
              "beta": ProviderHealth("degraded", readers=1, records=2, parsed=1, discarded=1)}
    source._q.put(transfer(Packer().pack([], {}, {}, health)))
    source.poll()
    assert source.health == "partial"
    assert source.provider_errors == {"alpha": "read_error"}


def test_reader_leaves_out_providers_switched_off_in_config(tmp_path, monkeypatch):
    import bitacora.worker as worker

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("[providers]\nopencode = false\n", encoding="utf-8")
    monkeypatch.setenv("BITACORA_HOME", str(home))

    assert [provider.name for provider in worker.configured_providers()] == ["codex", "claude"]
    monkeypatch.setenv("BITACORA_HOME", str(tmp_path / "unset"))
    assert [provider.name for provider in worker.configured_providers()] == ["codex", "claude", "opencode"]
