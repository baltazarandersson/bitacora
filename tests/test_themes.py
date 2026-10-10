"""The rules every theme must meet, shared by the terminal and the desktop app."""
from bitacora.themes import DEFAULT_THEME, THEMES, contrast, descriptors, ink_on, mix, rule_color


def test_default_theme_is_monokai_and_every_theme_is_described():
    assert DEFAULT_THEME == "monokai" and DEFAULT_THEME in THEMES
    assert [d["id"] for d in descriptors()] == THEMES
    for theme in descriptors():
        for key, value in theme["colors"].items():
            assert len(value) == 7 and value.startswith("#"), (theme["id"], key, value)


def test_text_on_every_highlight_reaches_4_5_on_every_theme():
    """The highlights the apps draw in a theme's colours: the selection, the primary and error buttons, the waiting
    chips and the amber beam (warning). Each takes ink from the theme's own text colours or black and white."""
    checked = 0
    for theme in descriptors():
        colors = theme["colors"]
        text = [colors["background"], colors["foreground"]]
        for highlight in (colors["primary"], colors["error"], colors["warning"]):
            ink = ink_on(highlight, text)
            assert contrast(ink, highlight) >= 4.5, (theme["id"], highlight, ink)
            checked += 1
    assert checked == 3 * len(THEMES)


def test_ink_is_the_theme_text_when_it_reads_and_black_or_white_when_it_does_not():
    assert ink_on("#ffffff", ["#101010", "#202020"]) == "#101010"  # the darkest text colour reads on white
    assert ink_on("#101010", ["#f0f0f0", "#ffffff"]) == "#ffffff"  # the lightest reads on near-black
    assert ink_on("#ae81ff", ["#272822", "#d6d6d6"]) == "#272822"  # monokai's background reads on its primary
    assert ink_on("#777777", ["#7a7a7a", "#808080"]) == "#000000"  # neither theme colour reads: black does


def test_the_still_line_reads_at_1_6_on_every_theme():
    for theme in descriptors():
        colors = theme["colors"]
        line = rule_color(colors["background"], colors["foreground"])
        assert contrast(line, colors["background"]) >= 1.6, (theme["id"], line)


def test_mix_rounds_half_up_as_the_desktop_does():
    assert mix("#000000", "#ffffff", 0.5) == "#808080"  # 127.5 rounds up, as Math.round does
    assert mix("#102030", "#102030", 0.7) == "#102030"
