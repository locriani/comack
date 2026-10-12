"""
Story Lorekeeper & Narrative Continuity Engine.
Writes with the LLM (server.llm, at LLM_URL).
Maintains story arcs with dynamic lifespans (4 hours to 7 days),
incorporates scrambled overheard dialogue into comic scripts,
and picks a page layout (comic_compositor.LAYOUTS) to suit each scene.
"""
import json
import random
import logging
from typing import Dict, Any, List
from server import llm
from server.config import settings
from server.storage import db
from server.layouts import LAYOUTS, LAYOUT_STEERING, layout_menu, resolve_layout
from server.story.transcript_batch import clamp_lines_wanted

logger = logging.getLogger("comack.story")

BIBLE_KEYS = ("title", "genre", "premise", "tone", "art_style")


def format_cast(cast: List[str] | None) -> str:
    if not cast:
        return ""
    return "\nCast:" + "".join(f"\n- {c}" for c in cast)


def comic_script_system(title: str, genre: str, tone: str, art_style: str) -> str:
    """System prompt for one issue: this arc is a closed series; room notes are spice."""
    return (
        f"You write {title!r}, a {genre} comic series. Tone: {tone}. "
        f"Art style: {art_style}. Stay true to both in every issue. "
        "This story arc is an entirely independent series: its own universe, cast, continuity, "
        "and voice. Never reference, continue, crossover with, or allude to any other series. "
        "Within this arc, continuity is primary: continue the plot, characters, running jokes, "
        "and unresolved threads from Story So Far (when provided). "
        "Room notes are optional seasoning, not the plot. When notes are present, pick at most "
        "one line that best fits this issue's beat and weave it in (word-swaps and all) as "
        "dialogue, a caption, or a situation. Do not catalogue the room or force every note "
        "onto the page. All other dialogue and narration must advance this arc. "
        f"Every scene_description must use this exact series art style ({art_style!r}) — "
        "do not invent a different look. "
        "scene_description is visuals only — never mention speech bubbles, balloons, captions, "
        "lettering, readable text, or SFX glyphs (those are drawn later in code). "
        "Never ask for a tilted, dutch-angle, slanted, or rotated camera/frame in "
        "scene_description — each panel bitmap must stay level and axis-aligned; "
        "diagonal page layouts are drawn by the compositor as panel shapes, not by tilting the art. "
        "Choose layout_type from the beat's mood and energy first (splash for critical strikes "
        "and sweeping stillness; diagonals/action/strips for kinetic density; columns/tiers for "
        "talk and timing) — vary layouts across issues; do not default to the same shape. "
        "Lettering is drawn later in code: for every panel set text_anchor "
        "(top|top-left|top-right|bottom|bottom-left|bottom-right) and write scene_description so "
        "faces and key subjects sit opposite that zone, leaving clear empty sky/negative space "
        "where captions and balloons will go. "
        "layout_type and lines_wanted_next are enforced by the server: pick a real layout name "
        "and a real next-batch size (1-30). Output ONLY valid, parseable JSON conforming "
        "strictly to the schema."
    )


def comic_script_user(
    *,
    title: str,
    genre: str,
    premise: str,
    art_style: str,
    cast: str,
    arc_progress: float,
    panels_used: int,
    target_panels: int,
    series_issue: int,
    history_section: str,
    mined_elements: List[str],
) -> str:
    """User prompt for one issue script."""
    notes = (
        json.dumps(mined_elements, indent=2, ensure_ascii=False)
        if mined_elements
        else '["(Eerie room silence... only the ambient hum of quantum machinery)"]'
    )
    note_count = len(mined_elements)
    return f"""
Series: {title}
Genre: {genre}
Art style (required in every scene_description): {art_style}
Premise: {premise}{cast}
Arc Progress: {arc_progress * 100:.1f}% ({panels_used} of {target_panels} panels drawn).
  The arc ends when {target_panels} panels are drawn: pace the story to land there.
Issue Number: #{series_issue}

{history_section}Room notes for this issue ({note_count} line{"s" * (note_count != 1)}; the server
already capped this batch to the previous issue's lines_wanted_next).
Pick the single most compelling note for this beat of the arc; leave the rest unused
(or echo them only if they truly sharpen the same beat):
{notes}

TASK:
1. Decide the next story beats from the premise and prior issues of this arc only
   (continuity first). This arc is a closed series — do not continue, reference, or resume
   any other series. Name the beat's mood and energy to yourself (still / talky / tense /
   kinetic / packed), then pick layout_type that matches — the page shape is storytelling,
   not decoration. Write exactly as many panels as that layout has
   (layout_type must be one of the names below; the page is drawn from that choice).

{LAYOUT_STEERING}

Layouts (name → when to use):
{layout_menu()}
2. For each panel pick text_anchor (where captions/balloons will sit): top, top-left,
   top-right, bottom, bottom-left, or bottom-right. Write scene_description as a
   visual-only image prompt that begins with {art_style!r}, then characters, action,
   setting, lighting — and explicitly leave clear empty negative space in that
   text_anchor zone (faces and key subjects go in the opposite area).
   Keep every scene level (upright horizon, no dutch angle, no "tilted panel" /
   "slanted frame" / rotated border). Kinetic energy comes from pose and motion
   lines, not from tilting the camera — diagonal layouts are page shapes only.
   No speech bubbles, balloons, captions, text, or lettering in the art.
3. Write narration and speech_balloons for this arc's cast and continuity. Keep them
   short enough to fit the text_anchor band. At most one balloon or caption may
   incorporate the chosen room note; everything else advances this arc's story.
4. Set lines_wanted_next (1-30): the exact number of room notes the server will feed
   the next issue (oldest first; extras stay queued). Fewer for a fast beat; more to
   build from a richer stretch of the room.
5. Output STRICT JSON in this exact structure:
{{
  "title": "This issue's title, in the series' voice",
  "layout_type": "one of the layout names above",
  "panels": [
    {{
      "panel_number": 1,
      "text_anchor": "top-left",
      "scene_description": "{art_style}. Visual only — clear empty space at text_anchor; faces opposite. No bubbles or text.",
      "narration": "Optional caption box text (e.g. 'Meanwhile, in Sector 9...')",
      "speech_balloons": [
        {{"character": "Character Name", "text": "Dialogue that continues the story"}}
      ],
      "sound_effect": "Optional comic SFX e.g. 'BZZZZT' or 'CLICK'"
    }}
  ],
  "lines_wanted_next": 8
}}
"""

def arc_bible_prompt(past_arcs: List[Dict[str, Any]]) -> str:
    """Asks for a brand-new, self-contained series unlike every arc listed."""
    past = []
    for a in past_arcs:
        meta = json.loads(a.get("metadata_json") or "{}")
        style = "; ".join(s for s in (meta.get("tone"), meta.get("art_style")) if s)
        past.append(f"- {a['title']} ({a['genre']}{'; ' + style if style else ''}): {a['premise']}")
    return f"""Each story arc is an entirely independent series (own universe, cast, continuity).
Previous series on this device — do NOT reuse their genres, tones, art styles, settings,
characters, or plot threads:
{chr(10).join(past) or "- (none yet)"}

Invent a completely new comic series, self-contained from issue one. Draw from the whole
range of comics and storytelling: any genre, era, audience, and kind of humor or drama.
Output: {{"title": "the series title", "genre": "a few words", "premise": "one or two sentences",
"tone": "how the story is told: voice, kind of humor or drama, how the characters behave",
"art_style": "the visual style, as a phrase an image model understands",
"cast": ["Name: one-line description"]}}"""


def lines_wanted(script: Dict[str, Any]) -> int:
    """The LLM's requested room-note count for the next issue, clamped to 1-30."""
    try:
        return clamp_lines_wanted(int(script.get("lines_wanted_next")))
    except (TypeError, ValueError):
        return settings.DEFAULT_LINES_WANTED


def story_so_far(comics: List[Dict[str, Any]], *, arc_id: int | None = None) -> str:
    """On-panel text of previous issues in this arc only (oldest first).

    Callers must pass comics already scoped to one arc (`db.get_arc_comics`).
    When `arc_id` is set, any row with a different `arc_id` is dropped so a
    mistaken all-comics list cannot leak earlier series into the prompt.
    """
    lines = []
    n = 0
    for c in comics:
        if arc_id is not None and c.get("arc_id") is not None and c["arc_id"] != arc_id:
            continue
        n += 1
        lines.append(f"Issue #{n}: {c['title']}")
        for p in json.loads(c["script_json"]).get("panels", []):
            if p.get("narration"):
                lines.append(f"  [Caption] {p['narration']}")
            for b in p.get("speech_balloons") or []:
                lines.append(f"  {b.get('character', '?')}: {b.get('text', '')}")
            if p.get("sound_effect"):
                lines.append(f"  [SFX] {p['sound_effect']}")
    return "\n".join(lines)


def story_so_far_section(arc_title: str, history: str) -> str:
    """Prompt banner + on-panel history for this arc only, or empty when there is no history."""
    if not history:
        return ""
    return (
        f"Story So Far for this arc only ({arc_title!r}) — earlier series are closed and "
        "must not appear here or be continued. Text of prior issues in this arc, oldest first. "
        "Continue this plot directly; keep the same characters, running jokes, and "
        f"unresolved threads:\n{history}\n\n"
    )


def build_comic_script_prompts(
    arc: Dict[str, Any],
    *,
    series_issue: int,
    arc_comics: List[Dict[str, Any]],
    mined_elements: List[str],
) -> tuple[str, str]:
    """System and user prompts for the next issue script (pure assembly from arc state)."""
    panels_used = arc["panels_used"]
    arc_progress = min(1.0, panels_used / arc["target_panels"])
    meta = json.loads(arc.get("metadata_json") or "{}")
    tone = meta.get("tone") or "whatever best fits the genre"
    art_style = meta.get("art_style") or "whatever best fits the genre"
    history_section = story_so_far_section(
        arc["title"],
        story_so_far(arc_comics, arc_id=arc["id"]),
    )
    system = comic_script_system(arc["title"], arc["genre"], tone, art_style)
    user = comic_script_user(
        title=arc["title"],
        genre=arc["genre"],
        premise=arc["premise"],
        art_style=art_style,
        cast=format_cast(meta.get("cast")),
        arc_progress=arc_progress,
        panels_used=panels_used,
        target_panels=arc["target_panels"],
        series_issue=series_issue,
        history_section=history_section,
        mined_elements=mined_elements,
    )
    return system, user


class NarrativeEngine:
    """One engine per device: it fetches-or-seeds that device's active arc, and each
    issue continues that device's own story (story_so_far reads the arc's comics, so
    continuity never crosses into another device's series)."""

    def __init__(self, device_id: str):
        self.device_id = device_id

    async def get_or_create_active_arc(self) -> Dict[str, Any]:
        """Fetch the device's current active story arc or seed a new one if expired/absent."""
        arc = db.get_active_arc(self.device_id)
        if arc:
            if arc["panels_used"] < arc["target_panels"]:
                return arc
            logger.info(f"Arc '{arc['title']}' completed after {arc['panels_used']} panels. Seeding new arc.")
            db.conclude_arc(arc["id"])

        # The arc's length is fixed here; the script model never decides when it ends.
        target_panels = random.randint(settings.ARC_MIN_PANELS, settings.ARC_MAX_PANELS)
        # Variety vs prior series on this device only — arcs stay independent per room.
        past = [a for a in db.get_arcs() if a.get("device_id") == self.device_id][-12:]
        bible = await llm.complete_json(
            "You invent self-contained comic series. Each series is independent. Output ONLY valid JSON.",
            arc_bible_prompt(past),
        )
        if not bible or not all(str(bible.get(k) or "").strip() for k in BIBLE_KEYS):
            raise RuntimeError("the LLM did not invent a new arc; the next cycle retries")
        bible = {k: str(bible[k]).strip() for k in BIBLE_KEYS} | {"cast": [str(c) for c in bible.get("cast") or []][:8]}

        arc_id = db.create_arc(
            title=bible["title"],
            genre=bible["genre"],
            premise=bible["premise"],
            target_panels=target_panels,
            device_id=self.device_id,
            metadata={k: bible[k] for k in ("tone", "art_style", "cast")}
        )
        logger.info(f"New story arc #{arc_id} for device '{self.device_id}': "
                    f"'{bible['title']}' ({bible['genre']}), target {target_panels} panels")
        return db.get_active_arc(self.device_id)

    async def generate_comic_script(self, transcript_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generate the next comic strip script using the LLM.
        Takes dialogue collected during speech periods and maintains continuity.
        """
        arc = await self.get_or_create_active_arc()
        issue_number = db.get_next_issue_number()
        
        arc_comics = db.get_arc_comics(arc["id"])
        series_issue = len(arc_comics) + 1  # each arc is its own series

        mined_elements = [item["scrambled_text"] for item in transcript_items if item.get("scrambled_text")]
        meta = json.loads(arc.get("metadata_json") or "{}")
        art_style = meta.get("art_style") or "whatever best fits the genre"
        tone = meta.get("tone") or "whatever best fits the genre"

        system_prompt, prompt_content = build_comic_script_prompts(
            arc,
            series_issue=series_issue,
            arc_comics=arc_comics,
            mined_elements=mined_elements,
        )

        script = await llm.complete_json(system_prompt, prompt_content)
        
        # Fallback if LLM call fails or returns unparseable JSON
        if not script or "panels" not in script:
            script = self._create_fallback_script(issue_number, arc, mined_elements)

        layout = resolve_layout(script.get("layout_type", ""), len(script["panels"]))
        panels = script["panels"][:len(LAYOUTS[layout][1])]
        wanted = lines_wanted(script)
        return {
            "issue_number": issue_number,
            "series_issue": series_issue,
            "arc_id": arc["id"],
            "arc_title": arc["title"],
            "genre": arc["genre"],
            "art_style": art_style,
            "tone": tone,
            "title": script.get("title", f"Episode #{series_issue}"),
            "layout_type": layout,
            "panel_count": len(panels),
            "panels": panels,
            "lines_wanted_next": wanted,
            "dialogue_used": mined_elements,
        }

    def _create_fallback_script(self, issue_number: int, arc: Dict[str, Any], mined_elements: List[str]) -> Dict[str, Any]:
        """Robust procedural fallback script when LLM is unavailable."""
        line1 = "The anomalous readings are spiking across the perimeter!"
        line2 = "Deploy the secondary harmonic condenser at once!"
        meta = json.loads(arc.get("metadata_json") or "{}")
        style = meta.get("art_style") or arc.get("genre") or "graphic novel"

        if mined_elements:
            first_element = mined_elements[0]
            if "URGENT" in first_element:
                line1 = "We have less than three minutes before the chronometer folds!"
                line2 = "I'm already recalibrating the primary gyro-spatula!"
            elif "SUSPICIOUS" in first_element:
                line1 = "Did you hear that vibration beneath the deckplates?"
                line2 = "Act natural. The perimeter sensors are watching us."
            elif "FRUSTRATED" in first_element:
                line1 = "The bureaucratic apparatus refuses to acknowledge our clearance."
                line2 = "Then we shall dismantle the sub-relay manually."

        return {
            "title": f"Incident at Sector #{issue_number}",
            "layout_type": "two-columns",
            "lines_wanted_next": settings.DEFAULT_LINES_WANTED,
            "panels": [
                {
                    "panel_number": 1,
                    "text_anchor": "top",
                    "scene_description": (
                        f"{style}. Dramatic wide shot. Clear empty sky across the top. "
                        "A shadowed figure in the lower half stares at a glowing console."
                    ),
                    "narration": f"Meanwhile, during {arc['title']}...",
                    "speech_balloons": [
                        {"character": "Agent Banjo", "text": line1}
                    ],
                    "sound_effect": "HUMMMM"
                },
                {
                    "panel_number": 2,
                    "text_anchor": "top-right",
                    "scene_description": (
                        f"{style}. Close-up reaction shot. Clear empty space upper-right. "
                        "A bewildered cyborg on the left holds a mysterious mechanical device."
                    ),
                    "narration": None,
                    "speech_balloons": [
                        {"character": "Doctor Rutabaga", "text": line2}
                    ],
                    "sound_effect": "KLANG!"
                }
            ]
        }


# One engine per device (spec D3): story arcs and story-so-far continuity are
# device-scoped. An engine holds nothing but its binding, so instances are
# cached per device instead of rebuilt on every cycle.
_engines: Dict[str, NarrativeEngine] = {}


def engine_for(device_id: str) -> NarrativeEngine:
    """The narrative engine for one device — the replacement for the old
    module-level singleton, which could only ever tell one story."""
    engine = _engines.get(device_id)
    if engine is None:
        engine = _engines[device_id] = NarrativeEngine(device_id)
    return engine
