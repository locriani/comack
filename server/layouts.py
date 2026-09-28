"""The comic page layouts, shared by the script writer (which picks one) and the compositor
(which draws it)."""

T = 1 / 3


def R(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# Common comic page layouts: name -> (what scene it suits, panels in reading order). Each panel is a
# quad (top-left, top-right, bottom-right, bottom-left) in fractions of the page area; later panels
# draw over earlier ones (the inset). The first layout for each panel count is the plain fallback.
LAYOUTS = {
    "splash":         ("one full-page splash: a single dramatic climax, reveal, or quiet contemplative moment",
                       [R(0, 0, 1, 1)]),
    "two-columns":    ("two side-by-side panels: back-and-forth dialogue, before/after",
                       [R(0, 0, .5, 1), R(.5, 0, 1, 1)]),
    "splash-inset":   ("full-page splash with a small close-up inset: a big moment plus someone's reaction to it",
                       [R(0, 0, 1, 1), R(.64, .06, .97, .46)]),
    "two-tiers":      ("two wide stacked panels: cause, then effect",
                       [R(0, 0, 1, .5), R(0, .5, 1, 1)]),
    "diagonal-split": ("two panels cut by a slash: a clash, confrontation, or sudden reversal",
                       [[(0, 0), (.55, 0), (.45, 1), (0, 1)], [(.55, 0), (1, 0), (1, 1), (.45, 1)]]),
    "slanted-tiers":  ("two tiers cut by a slope: a fall, an impact, things going sideways",
                       [[(0, 0), (1, 0), (1, .42), (0, .58)], [(0, .58), (1, .42), (1, 1), (0, 1)]]),
    "three-columns":  ("classic strip: setup, escalation, punchline",
                       [R(0, 0, T, 1), R(T, 0, 2 * T, 1), R(2 * T, 0, 1, 1)]),
    "widescreen":     ("three cinematic stacked strips: a slow, sweeping sequence or passage of time",
                       [R(0, 0, 1, T), R(0, T, 1, 2 * T), R(0, 2 * T, 1, 1)]),
    "big-left":       ("a big opening shot, then two small beats stacked on the right",
                       [R(0, 0, .6, 1), R(.6, 0, 1, .5), R(.6, .5, 1, 1)]),
    "big-right":      ("two small beats stacked on the left, then a big payoff",
                       [R(0, 0, .4, .5), R(0, .5, .4, 1), R(.4, 0, 1, 1)]),
    "wide-top":       ("a wide establishing shot, then two beats below",
                       [R(0, 0, 1, .5), R(0, .5, .5, 1), R(.5, .5, 1, 1)]),
    "wide-bottom":    ("two quick beats, then a wide punchline",
                       [R(0, 0, .5, .5), R(.5, 0, 1, .5), R(0, .5, 1, 1)]),
    "diagonal-three": ("three slanted columns: fast action, a chase, momentum",
                       [[(0, 0), (.36, 0), (.30, 1), (0, 1)], [(.36, 0), (.70, 0), (.64, 1), (.30, 1)],
                        [(.70, 0), (1, 0), (1, 1), (.64, 1)]]),
    "grid":           ("2x2 grid: four even beats, a conversation or a countdown",
                       [R(0, 0, .5, .5), R(.5, 0, 1, .5), R(0, .5, .5, 1), R(.5, .5, 1, 1)]),
    "wide-top-three": ("a wide establishing shot, then three quick beats",
                       [R(0, 0, 1, .45), R(0, .45, T, 1), R(T, .45, 2 * T, 1), R(2 * T, .45, 1, 1)]),
    "action":         ("a wide slanted opener, then three tilted beats: chaos breaking loose",
                       [[(0, 0), (1, 0), (1, .38), (0, .52)], [(0, .52), (.36, .47), (.30, 1), (0, 1)],
                        [(.36, .47), (.70, .42), (.64, 1), (.30, 1)], [(.70, .42), (1, .38), (1, 1), (.64, 1)]]),
    "five":           ("two panels over three: a busy, escalating scene",
                       [R(0, 0, .5, .5), R(.5, 0, 1, .5), R(0, .5, T, 1), R(T, .5, 2 * T, 1), R(2 * T, .5, 1, 1)]),
}
MAX_PANELS = max(len(p) for _, p in LAYOUTS.values())


def resolve_layout(name: str, panel_count: int) -> str:
    """The requested layout if it fits the panel count, else the plain layout for that count."""
    if name in LAYOUTS and len(LAYOUTS[name][1]) == panel_count:
        return name
    n = min(max(panel_count, 1), MAX_PANELS)
    return next(k for k, (_, p) in LAYOUTS.items() if len(p) == n)
