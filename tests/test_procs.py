import contextlib

import bitacora.procs as procs


class FakeProcess:
    def __init__(self, pid: int, name: str):
        self.pid = pid
        self.info = {"name": name}

    def oneshot(self):
        return contextlib.nullcontext()

    def cmdline(self):
        return [self.info["name"]]

    def create_time(self):
        return 1.0

    def ppid(self):
        return 0


def test_snapshot_filters_by_declared_prefixes(monkeypatch):
    fake = [FakeProcess(1, "toy.exe"), FakeProcess(2, "python.exe"),
            FakeProcess(3, "codex.exe"), FakeProcess(4, "node")]
    monkeypatch.setattr(procs.psutil, "process_iter", lambda attrs: iter(fake))

    assert [p.pid for p in procs.snapshot(("toy",))] == [1]
    assert [p.pid for p in procs.snapshot(("codex", "node"))] == [3, 4]
    assert [p.pid for p in procs.snapshot(())] == [3, 4]  # empty falls back to the shared default
