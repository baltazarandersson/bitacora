"""Golden frames for the masthead scene: text and colors must stay byte-for-byte identical across changes.

The hashes were generated with the scene before the 1.1.x performance pass, using fixed sizes, seed, clock,
tagline and time sequence. If a change to Scene alters any Segment text or Style, one of these breaks: do not
update a hash to make it pass, the visual output is the contract.
"""
import hashlib
from datetime import datetime

import pytest

from bitacora.themes import mix
from bitacora.ui.masthead import MAX_LEVEL, BeaconRule, beam_strip
from bitacora.ui.scene import Scene

CASES = [
    ("night-80x16", 80, 16, datetime(2026, 3, 14, 21, 30), "2 agents running · 1 waiting", [0.0, 0.75, 1.6, 3.2, 6.0]),
    ("day-120x16", 120, 16, datetime(2026, 3, 14, 11, 5), "1 agent waiting for you", [0.0, 1.25, 2.5, 4.75]),
    ("night-200x50", 200, 50, datetime(2026, 3, 14, 21, 30), "3 agents running · 1 waiting", [0.0, 2.0, 5.5]),
]
GOLDEN = {
    "night-80x16": [
        "59694aa7a5adc0c944e19f2d1668652de8441c0f03692b7cbd8c92a66daef306",
        "989f907b7b212a2e79408a81249d73a58edda23140e0022eb33b5dc218424392",
        "9ea50daae7e10a0b3c390e9ee598a68533b89f13e1f314c1af7560dcb041ca17",
        "7f8596f3aaec0f492ffa2d608edca1132e06001e67c984fc4e74c31a2676908b",
        "b3097634bd2a110b075033bafa51b3692a48a5daa1e2eaf6e111ffea93b230c0",
    ],
    "day-120x16": [
        "8897f2dfbfbd3c99558e9f066f4f607ef6dfb94d27780f8a294d4011a7637de5",
        "9fc9acbb6b5c00aaeecf164fffa018218b1e5b05afb873c4f6c7c21a48b6dbb7",
        "0631b8c66d62d5a75f1bc6c0b0a840d461bd958cd33a4a523cf6731c4fcee24c",
        "aed7b511a9e3a7ce84d758ebae8c6ad9f3a838f5fbed245ae64b95a6c250803d",
    ],
    "night-200x50": [
        "695d9e64954d83415f90e6a3296ac9a6b0e3c694fb915693d45bffc70a92c51d",
        "0baf6520dec39c47fec77c4f20f40c1603c7ba86fb51880317d1cce127926d20",
        "74ed937ec289bc53210ee4389c38ccf1b61cbaf482dd1a8c26dd62136f94a597",
    ],
}


def frame_hash(rows) -> str:
    digest = hashlib.sha256()
    for row in rows:
        for segment in row:
            style = segment.style
            fg = style.color.get_truecolor().hex if style and style.color else "-"
            bg = style.bgcolor.get_truecolor().hex if style and style.bgcolor else "-"
            bold = int(bool(style and style.bold))
            digest.update(f"{segment.text}\x1f{fg}\x1f{bg}\x1f{bold}\n".encode())
    return digest.hexdigest()


@pytest.mark.parametrize(("name", "width", "height", "now", "tagline", "times"), CASES, ids=[case[0] for case in CASES])
def test_frames_match_the_golden_hashes(name, width, height, now, tagline, times):
    scene = Scene(width, height, seed=7)
    for index, t in enumerate(times):
        rows = scene.frame(t, now, tagline)
        assert len(rows) == height
        assert all(sum(len(segment.text) for segment in row) == width for row in rows)
        assert frame_hash(rows) == GOLDEN[name][index], f"{name}: frame {index} (t={t}) changed"


def test_beam_strip_is_the_same_gradient_as_the_rule():
    muted, beam = "#123456", "#abcdef"
    runs = beam_strip(40, 20.0)
    levels = [level for text, level in runs for _ in text]
    assert len(levels) == 40
    assert all(left != right for (_, left), (_, right) in zip(runs, runs[1:], strict=False))
    colors = [mix(muted, beam, level / MAX_LEVEL) for level in levels]
    exact = [mix(muted, beam, max(0.0, 1 - abs(x - 20.0) / BeaconRule.GLOW)) for x in range(40)]
    for got, want in zip(colors, exact, strict=True):
        assert max(abs(int(got[i:i + 2], 16) - int(want[i:i + 2], 16)) for i in (1, 3, 5)) <= 1
    assert colors[20] == beam  # the centre of the beam is the full accent


def test_beam_strip_outside_the_rule_is_flat():
    assert beam_strip(12, -BeaconRule.GLOW) == [("─" * 12, 0)]
    assert beam_strip(12, 12 + BeaconRule.GLOW) == [("─" * 12, 0)]


def test_beam_styles_are_cached_per_level():
    rule = BeaconRule.__new__(BeaconRule)
    rule._beam_styles, rule._beam_key = {}, None
    first = BeaconRule._beam_style(rule, 128, "#123456", "#abcdef")
    assert BeaconRule._beam_style(rule, 128, "#123456", "#abcdef") is first
    assert BeaconRule._beam_style(rule, 129, "#123456", "#abcdef") is not first
    assert len(rule._beam_styles) == 2
    BeaconRule._beam_style(rule, 128, "#123456", "#fedcba")  # theme change drops the cache
    assert len(rule._beam_styles) == 1
