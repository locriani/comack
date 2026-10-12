"""The comic page layouts, shared by the script writer (which picks one) and the compositor
(which draws it)."""

T = 1 / 3
Q = 0.25
H = 0.5
TQ = 0.75  # three-quarters


def R(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# name -> (when to pick it — mood/energy/beat, panels in reading order).
# Each panel is a quad (TL, TR, BR, BL) in fractions of the page area; later panels draw over
# earlier ones (insets). Plain fallbacks per panel count are listed in FALLBACK_BY_COUNT.
LAYOUTS = {
    # ── 1 panel ──────────────────────────────────────────────────────────
    "splash": (
        "1 panel — sweeping expanse, iconic pose, quiet contemplation, or a CRITICAL STRIKE / "
        "make-or-break reveal that needs the whole page",
        [R(0, 0, 1, 1)],
    ),

    # ── 2 panels ─────────────────────────────────────────────────────────
    "two-columns": (
        "2 panels side-by-side — calm back-and-forth dialogue, before/after, question and answer",
        [R(0, 0, H, 1), R(H, 0, 1, 1)],
    ),
    "splash-inset": (
        "2 panels — one huge moment plus a small reaction inset (shock, smirk, tear)",
        [R(0, 0, 1, 1), R(0.64, 0.06, 0.97, 0.46)],
    ),
    "two-tiers": (
        "2 panels stacked — cause then effect, setup then payoff",
        [R(0, 0, 1, H), R(0, H, 1, 1)],
    ),
    "diagonal-split": (
        "2 panels cut by a slash — clash, confrontation, sudden reversal",
        [[(0, 0), (0.55, 0), (0.45, 1), (0, 1)], [(0.55, 0), (1, 0), (1, 1), (0.45, 1)]],
    ),
    "slanted-tiers": (
        "2 slanted tiers — a fall, an impact, things going sideways",
        [[(0, 0), (1, 0), (1, 0.42), (0, 0.58)], [(0, 0.58), (1, 0.42), (1, 1), (0, 1)]],
    ),
    "big-top": (
        "2 panels — large establishing shot on top, thin reaction strip below",
        [R(0, 0, 1, 0.68), R(0, 0.68, 1, 1)],
    ),
    "big-bottom": (
        "2 panels — thin setup strip on top, large payoff below",
        [R(0, 0, 1, 0.32), R(0, 0.32, 1, 1)],
    ),
    "tall-left": (
        "2 panels — tall portrait on the left, wide scene on the right",
        [R(0, 0, 0.38, 1), R(0.38, 0, 1, 1)],
    ),
    "tall-right": (
        "2 panels — wide scene on the left, tall portrait / reveal on the right",
        [R(0, 0, 0.62, 1), R(0.62, 0, 1, 1)],
    ),
    "corner-inset": (
        "2 panels — full page with a small bottom-left inset (memory, clue, whisper)",
        [R(0, 0, 1, 1), R(0.04, 0.58, 0.38, 0.94)],
    ),
    "peek": (
        "2 panels — full page with a top-right peek inset (spy, glance, incoming threat)",
        [R(0, 0, 1, 1), R(0.62, 0.06, 0.96, 0.38)],
    ),

    # ── 3 panels ─────────────────────────────────────────────────────────
    "three-columns": (
        "3 even columns — classic strip: setup → escalation → punchline",
        [R(0, 0, T, 1), R(T, 0, 2 * T, 1), R(2 * T, 0, 1, 1)],
    ),
    "widescreen": (
        "3 stacked cinematic strips — slow passage of time, travel, lingering mood",
        [R(0, 0, 1, T), R(0, T, 1, 2 * T), R(0, 2 * T, 1, 1)],
    ),
    "big-left": (
        "3 panels — large opener on the left, two small beats stacked on the right",
        [R(0, 0, 0.6, 1), R(0.6, 0, 1, H), R(0.6, H, 1, 1)],
    ),
    "big-right": (
        "3 panels — two small setups on the left, large payoff on the right",
        [R(0, 0, 0.4, H), R(0, H, 0.4, 1), R(0.4, 0, 1, 1)],
    ),
    "wide-top": (
        "3 panels — wide establishing shot on top, two beats below",
        [R(0, 0, 1, H), R(0, H, H, 1), R(H, H, 1, 1)],
    ),
    "wide-bottom": (
        "3 panels — two quick beats on top, wide punchline below",
        [R(0, 0, H, H), R(H, 0, 1, H), R(0, H, 1, 1)],
    ),
    "diagonal-three": (
        "3 slanted columns — chase, rush, momentum, dense action",
        [[(0, 0), (0.36, 0), (0.30, 1), (0, 1)], [(0.36, 0), (0.70, 0), (0.64, 1), (0.30, 1)],
         [(0.70, 0), (1, 0), (1, 1), (0.64, 1)]],
    ),
    "staircase": (
        "3 panels stepping down — rising tension, a descent, or a plan unfolding step by step",
        [R(0, 0, 0.55, 0.38), R(0.22, 0.32, 0.78, 0.68), R(0.45, 0.62, 1, 1)],
    ),
    "bookends": (
        "3 panels — two tall side beats framing a wide middle moment",
        [R(0, 0, 0.28, 1), R(0.28, 0, 0.72, 1), R(0.72, 0, 1, 1)],
    ),
    "focus-center": (
        "3 panels — thin strips top and bottom with a large emotional center",
        [R(0, 0, 1, 0.22), R(0, 0.22, 1, 0.78), R(0, 0.78, 1, 1)],
    ),
    "l-shape": (
        "3 panels — wide top plus a tall left leg (L): establish, then dive into detail",
        [R(0, 0, 1, 0.42), R(0, 0.42, 0.45, 1), R(0.45, 0.42, 1, 1)],
    ),
    "reverse-l": (
        "3 panels — wide top plus a tall right leg: establish, then a right-side reveal",
        [R(0, 0, 1, 0.42), R(0, 0.42, 0.55, 1), R(0.55, 0.42, 1, 1)],
    ),

    # ── 4 panels ─────────────────────────────────────────────────────────
    "grid": (
        "4 even beats — conversation rounds, checklist, countdown, parallel reactions",
        [R(0, 0, H, H), R(H, 0, 1, H), R(0, H, H, 1), R(H, H, 1, 1)],
    ),
    "wide-top-three": (
        "4 panels — wide open on top, then three quick staccato beats",
        [R(0, 0, 1, 0.45), R(0, 0.45, T, 1), R(T, 0.45, 2 * T, 1), R(2 * T, 0.45, 1, 1)],
    ),
    "action": (
        "4 panels — chaos breaking loose: slanted wide opener plus three tilted beats",
        [[(0, 0), (1, 0), (1, 0.38), (0, 0.52)], [(0, 0.52), (0.36, 0.47), (0.30, 1), (0, 1)],
         [(0.36, 0.47), (0.70, 0.42), (0.64, 1), (0.30, 1)], [(0.70, 0.42), (1, 0.38), (1, 1), (0.64, 1)]],
    ),
    "wide-bottom-three": (
        "4 panels — three quick beats on top, wide aftermath / punchline below",
        [R(0, 0, T, 0.55), R(T, 0, 2 * T, 0.55), R(2 * T, 0, 1, 0.55), R(0, 0.55, 1, 1)],
    ),
    "tall-feature": (
        "4 panels — tall feature on the left, three stacked beats on the right",
        [R(0, 0, 0.48, 1), R(0.48, 0, 1, T), R(0.48, T, 1, 2 * T), R(0.48, 2 * T, 1, 1)],
    ),
    "feature-right": (
        "4 panels — three stacked beats on the left, tall feature / payoff on the right",
        [R(0, 0, 0.52, T), R(0, T, 0.52, 2 * T), R(0, 2 * T, 0.52, 1), R(0.52, 0, 1, 1)],
    ),
    "z-path": (
        "4 panels in a Z reading path — zig-zag discovery, mismatched timescales, montage",
        [R(0, 0, 0.62, 0.42), R(0.62, 0, 1, 0.42), R(0, 0.42, 0.38, 1), R(0.38, 0.42, 1, 1)],
    ),
    "quarter-burst": (
        "4 panels — one large three-quarter moment with three small corner beats around it",
        [R(0, 0, TQ, TQ), R(TQ, 0, 1, T), R(TQ, T, 1, 2 * T), R(TQ, 2 * T, 1, 1)],
    ),
    "four-strip": (
        "4 even vertical strips — rapid-fire beats, parallel action, a race against time",
        [R(0, 0, Q, 1), R(Q, 0, H, 1), R(H, 0, TQ, 1), R(TQ, 0, 1, 1)],
    ),
    "four-tiers": (
        "4 thin horizontal tiers — slow burn, diary entries, weather/time passing",
        [R(0, 0, 1, Q), R(0, Q, 1, H), R(0, H, 1, TQ), R(0, TQ, 1, 1)],
    ),

    # ── 5 panels ─────────────────────────────────────────────────────────
    "five": (
        "5 panels — busy escalating scene; packed jokes or mounting disaster",
        [R(0, 0, H, H), R(H, 0, 1, H), R(0, H, T, 1), R(T, H, 2 * T, 1), R(2 * T, H, 1, 1)],
    ),
    "five-strip": (
        "5 vertical strips — machine-gun pacing, countdown, overlapping chatter",
        [R(i / 5, 0, (i + 1) / 5, 1) for i in range(5)],
    ),
    "hero-four": (
        "5 panels — large hero shot plus four small surrounding beats",
        [R(0, 0, 0.62, 0.62), R(0.62, 0, 1, 0.31), R(0.62, 0.31, 1, 0.62),
         R(0, 0.62, H, 1), R(H, 0.62, 1, 1)],
    ),
    "five-cascade": (
        "5 panels cascading down — spiral of bad decisions or escalating chase beats",
        [R(0, 0, 0.48, 0.28), R(0.52, 0.08, 1, 0.36), R(0, 0.32, 0.48, 0.60),
         R(0.52, 0.40, 1, 0.68), R(0, 0.64, 1, 1)],
    ),
    "banner-four": (
        "5 panels — thin banner on top, then a 2×2 grid of beats",
        [R(0, 0, 1, 0.22), R(0, 0.22, H, 0.61), R(H, 0.22, 1, 0.61),
         R(0, 0.61, H, 1), R(H, 0.61, 1, 1)],
    ),

    # ── 6 panels ─────────────────────────────────────────────────────────
    "six-grid": (
        "6 panels in a 2×3 grid — ensemble cast, parallel plots, dense but orderly storytelling",
        [R(0, 0, H, T), R(H, 0, 1, T), R(0, T, H, 2 * T), R(H, T, 1, 2 * T),
         R(0, 2 * T, H, 1), R(H, 2 * T, 1, 1)],
    ),
    "six-strip": (
        "6 vertical strips — frantic, comic-book fight choreography or rapid banter",
        [R(i / 6, 0, (i + 1) / 6, 1) for i in range(6)],
    ),
    "six-tiers": (
        "6 thin horizontal tiers — timeline, dossier, relentless slow dread",
        [R(0, i / 6, 1, (i + 1) / 6) for i in range(6)],
    ),
    "feature-five": (
        "6 panels — large feature plus five satellite beats (investigation board energy)",
        [R(0, 0, 0.55, 0.55), R(0.55, 0, 1, 0.275), R(0.55, 0.275, 1, 0.55),
         R(0, 0.55, T, 1), R(T, 0.55, 2 * T, 1), R(2 * T, 0.55, 1, 1)],
    ),
}

# Plain fallback when the model asks for a layout that doesn't match its panel count.
FALLBACK_BY_COUNT = {
    1: "splash",
    2: "two-columns",
    3: "three-columns",
    4: "grid",
    5: "five",
    6: "six-grid",
}
MAX_PANELS = max(FALLBACK_BY_COUNT)

# Compact steering for the script model: mood/energy → layout family.
LAYOUT_STEERING = """Match layout_type to this issue's mood and energy (do not default to the same layout every time):
- CRITICAL STRIKE / iconic reveal / sweeping vista / hush → "splash" (or "splash-inset" / "peek" / "corner-inset" if you need one reaction).
- Calm dialogue, before/after → "two-columns", "two-tiers", "tall-left", "tall-right"; big establish then thin reply → "big-top" / "big-bottom".
- Clash / collision → "diagonal-split"; fall / impact → "slanted-tiers".
- Timed joke → "three-columns"; slow cinematic mood → "widescreen" or "focus-center"; step-by-step tension → "staircase".
- One big shot plus two small beats → "big-left" / "big-right" / "l-shape" / "reverse-l"; establish then split → "wide-top" / "wide-bottom" / "bookends".
- Chase / rush → "diagonal-three"; full chaos → "action"; rapid-fire → "four-strip" / "five-strip" / "six-strip".
- Even multipanel talk → "grid" / "six-grid"; open wide then snaps → "wide-top-three" / "wide-bottom-three"; packed escalation → "five" / "five-cascade" / "hero-four".
- Timeline / dossier dread → "four-tiers" / "six-tiers"; investigation board → "feature-five" / "quarter-burst" / "banner-four".
Prefer diagonal / action / strip layouts when kinetic; prefer splash / widescreen / focus-center when vast or still.
Important: diagonal/slanted layout names reshape the panel *boxes on the page*. Never describe a tilted camera, dutch angle, or slanted frame inside scene_description — panel art must stay level."""


def layout_menu() -> str:
    """Human-readable layout catalog for the script model (name, panel count, when to use)."""
    return "\n".join(
        f'   - "{name}" ({len(panels)} panel{"s" * (len(panels) > 1)}): {use}'
        for name, (use, panels) in LAYOUTS.items()
    )


def resolve_layout(name: str, panel_count: int) -> str:
    """The requested layout if it fits the panel count, else the plain layout for that count."""
    if name in LAYOUTS and len(LAYOUTS[name][1]) == panel_count:
        return name
    n = min(max(panel_count, 1), MAX_PANELS)
    return FALLBACK_BY_COUNT[n]
