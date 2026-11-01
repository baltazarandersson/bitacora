"""Block glyphs in the documentation SVGs become exact rectangles, so the pixel art has no seams."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("screenshot", ROOT / "scripts" / "screenshot.py")
screenshot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(screenshot)

SVG = (
    '<svg><style>\n.t-r1 { fill: #112233 }\n.t-r2 { fill: #445566 }\n.t-matrix {\n    line-height: 24.4px;\n}\n</style>'
    '<text class="t-r1" x="36.6" y="20" textLength="24.4" clip-path="url(#u)">▀▀</text>'
    '<text class="t-r2" x="60" y="20" textLength="12.2" clip-path="url(#u)">█</text>'
    '<text class="t-r2" x="80" y="44.4" textLength="12.2" clip-path="url(#u)">▄</text>'
    '<text class="t-r2" x="100" y="20" textLength="12.2" clip-path="url(#u)">A</text>'
    "</svg>"
)


def test_block_runs_become_one_rectangle_per_cell_in_the_glyph_colour():
    out = screenshot.pixel_rects(SVG)
    assert 'fill="#112233" x="36.6" y="1.5" width="12.2" height="12.2"' in out
    assert 'fill="#112233" x="48.8" y="1.5" width="12.2" height="12.2"' in out
    assert 'fill="#445566" x="60" y="1.5" width="12.2" height="24.4"' in out
    assert 'fill="#445566" x="80" y="38.1" width="12.2" height="12.2"' in out  # bottom half of row 1


def test_letters_and_unknown_classes_stay_text():
    out = screenshot.pixel_rects(SVG)
    assert '>A</text>' in out
    assert "█" not in out and "▀" not in out and "▄" not in out
    untouched = '<text class="t-missing" x="0" y="20" textLength="12.2">▀</text>'
    assert screenshot.pixel_rects(untouched) == untouched
