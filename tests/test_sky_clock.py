from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from bitacora.ui import masthead
from bitacora.ui.masthead import SkyClock, sky_speed


def fake_monotonic(monkeypatch, seconds):
    monkeypatch.setattr(masthead, "time", SimpleNamespace(monotonic=lambda: seconds[0]))


def test_time_lapse_runs_the_sky_fast_from_its_start(monkeypatch):
    seconds = [1000.0]
    fake_monotonic(monkeypatch, seconds)
    monkeypatch.setenv("BITACORA_SKY_SPEED", "1440")  # one day a minute
    sky = SkyClock("06:00")
    seconds[0] += 30
    assert sky.at() - sky.start == timedelta(hours=12)
    assert sky.start.strftime("%H:%M") == "06:00"


def test_huge_speed_stops_at_the_end_of_the_calendar(monkeypatch):
    seconds = [0.0]
    fake_monotonic(monkeypatch, seconds)
    monkeypatch.setenv("BITACORA_SKY_SPEED", "1e12")
    sky = SkyClock(None)
    seconds[0] = 1e6
    assert sky.at() > datetime(9999, 1, 1)


@pytest.mark.parametrize("value", ["", "abc", "0", "-5", "nan", "inf"])
def test_invalid_speed_is_real_time(monkeypatch, value):
    monkeypatch.setenv("BITACORA_SKY_SPEED", value)
    assert sky_speed() == 1
    assert abs(SkyClock(None).at() - datetime.now()) < timedelta(seconds=1)
