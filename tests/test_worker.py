import errno
import os
import pickle
import time

from bitacora.collector import Collector
from bitacora.model import Event, Run, Session
from bitacora.providers import all_providers
from bitacora.store import Memory
from bitacora.worker import Mirror, Packer, Source


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

    assert len(original) == len(mirrored) == 16
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
    event = mirror.sessions[str(session.path)].last_event
    assert event.count == 2 and event.dur == 10.0 and event.detail == "first\nsecond"
    assert len(mirror.sessions[str(session.path)].events) == 1


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
    assert [(e.seq, e.text) for e in mirror.sessions[str(session.path)].events] == [
        (1, "new-0"), (2, "new-1"), (3, "new-2")]


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
    monkeypatch.setattr(worker, "all_providers", lambda: [])
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
