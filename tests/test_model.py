import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bitacora.model import ASK_STALE_SECS, Run, Session


class RecordingSession(Session):
    def reset(self):
        super().reset()
        self.records = []

    def handle(self, obj):
        self.records.append(obj)


def test_missing_file_retry_partial_lines_and_malformed_records(tmp_path):
    path = tmp_path / "synthetic.jsonl"
    session = RecordingSession(path)
    generation = session.gen
    assert not session.update()  # a file can disappear between collection and open
    assert session.gen == generation

    path.write_bytes(b'{"value":1')
    assert session.update()
    assert session.records == [] and session.pending == b'{"value":1'

    with open(path, "ab") as fh:
        fh.write(b'}\nnot-json\n{"value":2}\n')
    assert session.update()
    assert session.records == [{"value": 1}, {"value": 2}]


def test_explicit_reset_changes_generation(tmp_path):
    session = RecordingSession(tmp_path / "synthetic.jsonl")
    generation = session.gen
    session.reset()
    assert session.gen != generation


def test_truncate_regrow_with_middle_only_change_is_detected(tmp_path):
    path = tmp_path / "synthetic.jsonl"
    padding = "a" * 600
    old_line = json.dumps({"value": padding})
    path.write_text(old_line + "\n", encoding="utf-8")
    session = RecordingSession(path)
    session.update()
    generation, old_size = session.gen, session.offset

    changed = padding[:300] + "b" + padding[301:]
    new_line = json.dumps({"value": changed})
    assert len(old_line) == len(new_line)
    with open(path, "wb") as fh:
        fh.write((new_line + "\n" + json.dumps({"value": "appended"}) + "\n").encode("utf-8"))
    assert path.stat().st_size > old_size

    assert session.update()
    assert session.gen != generation
    assert session.records == [{"value": changed}, {"value": "appended"}]


@pytest.mark.parametrize(
    ("turn", "process", "headless", "question", "age", "expected", "source"),
    [
        (None, False, False, False, None, "unknown", None),
        (None, True, False, False, None, "starting", None),
        ("unknown", True, False, False, None, "starting", None),
        ("running", False, False, True, 300, "asking", None),
        ("unknown", False, False, True, 300, "asking", None),
        ("running", False, False, True, ASK_STALE_SECS - 1, "asking", None),
        ("running", False, False, True, ASK_STALE_SECS + 1, "quiet", None),
        ("unknown", False, False, True, ASK_STALE_SECS + 1, "quiet", None),
        ("running", True, False, True, 300, "asking", None),
        ("running", True, False, False, 300, "running", "process"),
        ("running", False, False, False, 30, "running", "transcript"),
        ("running", False, False, False, 300, "quiet", None),
        ("complete", False, True, False, 300, "done", None),
        ("complete", False, False, False, 300, "your_turn", None),
        ("complete", True, False, False, 300, "your_turn", None),
        ("failed", False, True, False, 300, "failed", None),
        ("limited", False, True, False, 300, "limited", None),
        ("stopped", False, True, False, 300, "stopped", None),
        ("stopped", False, False, False, 300, "stopped", None),
        ("complete", False, False, True, 300, "your_turn", None),
        ("failed", False, False, True, 300, "failed", None),
    ],
)
def test_run_status_matrix(turn, process, headless, question, age, expected, source):
    now = time.time()
    session = None
    if turn is not None:
        session = Session(Path("synthetic.jsonl"))
        session.state = turn
        session.headless = headless
        session.last_ts = now - age if age is not None else None
        session.open_questions = {"q1": "question"} if question else {}
    proc = SimpleNamespace(headless=headless) if process else None
    run = Run("synthetic", "codex", session, proc, "Synthetic")

    assert run.status == expected
    assert run.status_source == source
    if expected == "asking":
        assert run.live
    if question and not process and age is not None and age > ASK_STALE_SECS:
        assert not run.live
