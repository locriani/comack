"""
Story Lorekeeper & Narrative Continuity Engine.
Connects to llama.cpp (OpenAI-compatible /v1 endpoint) on comack-server.
Maintains story arcs with dynamic lifespans (4 hours to 7 days),
incorporates scrambled overheard dialogue into surreal comic scripts,
and picks a page layout (comic_compositor.LAYOUTS) to suit each scene.
"""
import json
import time
import random
import logging
import httpx
from collections import deque
from typing import Dict, Any, List, Optional
from server.config import settings
from server.storage import db
from server.render.comic_compositor import LAYOUTS, resolve_layout

logger = logging.getLogger("comack.story")

# Recent model prompts and outputs for the dashboard's debug view. The miner's prompts hold
# the first-pass scrambled transcript, which may not be stored: this lives in memory only.
prompt_log: deque = deque(maxlen=60)

LAYOUT_MENU = "\n".join(f'   - "{name}" ({len(p)} panel{"s" * (len(p) > 1)}): {use}'
                        for name, (use, p) in LAYOUTS.items())

# Diverse genres for shifting narrative universe seeds
ARC_GENRES = [
    "Existential Noir",
    "Bureaucratic Space Opera",
    "Victorian Gothic Slapstick",
    "Subterranean Cyberpunk",
    "Melodramatic Soap Opera of Inanimate Objects",
    "Cryptid Small-Town Mystery",
    "Culinary Occult Sci-Fi",
    "1970s Disco Espionage",
    "Post-Apocalyptic Etiquette Guide",
    "Time-Traveling Jazz Tragedy",
    "Suburban Kaiju Sitcom",
    "Medieval Tech Support Epic",
    "Deep-Sea Corporate Thriller",
    "Haunted Strip-Mall Western",
    "Interdimensional Cooking Competition",
    "Gothic Romance Among Office Supplies",
    "Antarctic Heist Caper",
    "Prehistoric Courtroom Drama",
    "Lunar Colony Farce",
    "Pirate Radio Revolution",
    "Clockwork Detective Procedural",
    "Nature Documentary Gone Rogue",
    "Retirement-Home Superhero Saga",
    "Noir Musical of Sentient Weather",
    "Espionage at the County Fair",
    "Cosmic Horror HOA Meeting",
    "Wizard Academy Budget Crisis",
    "Soviet-Era Space Sitcom",
    "Mythological Road Trip",
    "Dinner-Party Murder Mystery Aboard a Zeppelin"
]

PREMISE_SEEDS = [
    "A retired space admiral and a sentient percolator run an interstellar laundromat that folds space-time.",
    "A world-weary trench-coat detective investigates why all the furniture in Apartment 4B is plotting a mutiny.",
    "A team of clandestine underground botanists must deliver the world's last sarcastic fern to Neo-Chicago.",
    "Two Victorian inventors keep accidentally inventing kitchen appliances that open portals to the year 3042.",
    "A crew of deep-sea submariners discovers that the ocean floor is made entirely of unreturned library books.",
    "An unhinged weather forecaster realizes that the clouds are spelling out petty office gossip in Morse code.",
    "A lighthouse keeper discovers the lighthouse has been quietly unionizing the seagulls.",
    "The last working vending machine on Earth demands tribute before dispensing snacks.",
    "A retired dragon opens a strictly unlicensed dental practice in a suburban garage.",
    "A committee of ghosts cannot agree on how to haunt a house with an open-plan layout.",
    "An elevator in a 90-story tower has declared independence between floors 42 and 43.",
    "Two rival mimes are forced to share a single invisible box for the rest of their lives.",
    "A diner's jukebox only plays songs from next week, and they are getting worse.",
    "A houseplant is elected mayor on a technicality and takes the job very seriously.",
    "A time-traveling accountant keeps getting audited by his own future self.",
    "A haunted Roomba maps the house and finds a room nobody has ever seen.",
    "A small-town bakery accidentally bakes a loaf of sourdough that achieves sentience.",
    "The moon files a formal noise complaint against the tides.",
    "An elite squad of retired librarians is reactivated to shush an approaching comet.",
    "A pigeon courier network is the only thing standing between the city and total chaos.",
    "A cursed karaoke machine forces anyone who sings to tell the truth.",
    "The world's most boring superhero must stop a villain powered by excitement.",
    "An IKEA showroom turns out to be infinitely large, and a family is lost inside it.",
    "A washed-up magician's rabbit is finally ready to go solo.",
    "A submarine crew realizes their periscope only shows reruns of 1970s game shows.",
    "Every clock in town starts running on a different, strongly held opinion of time."
]


def lines_wanted(script: Dict[str, Any]) -> int:
    """The LLM's requested room-note count for the next issue, clamped to 1-30."""
    try:
        return max(1, min(30, int(script.get("lines_wanted_next"))))
    except (TypeError, ValueError):
        return settings.DEFAULT_LINES_WANTED


def story_so_far(comics: List[Dict[str, Any]]) -> str:
    """On-panel text of previous issues (oldest first) so each new issue continues the plot."""
    lines = []
    for c in comics:
        lines.append(f"Issue #{c['issue_number']}: {c['title']}")
        for p in json.loads(c["script_json"]).get("panels", []):
            if p.get("narration"):
                lines.append(f"  [Caption] {p['narration']}")
            for b in p.get("speech_balloons") or []:
                lines.append(f"  {b.get('character', '?')}: {b.get('text', '')}")
            if p.get("sound_effect"):
                lines.append(f"  [SFX] {p['sound_effect']}")
    return "\n".join(lines)


class NarrativeEngine:
    def __init__(self):
        pass

    async def get_or_create_active_arc(self) -> Dict[str, Any]:
        """Fetch the current active story arc or seed a new one if expired/absent."""
        arc = db.get_active_arc()
        if arc:
            used = sum(c["panel_count"] for c in db.get_arc_comics(arc["id"]))
            if used < arc["target_panels"]:
                return arc
            logger.info(f"Arc '{arc['title']}' completed after {used} panels. Seeding new arc.")
            db.conclude_arc(arc["id"])

        target_panels = random.randint(settings.ARC_MIN_PANELS, settings.ARC_MAX_PANELS)
        genre = random.choice(ARC_GENRES)
        premise = random.choice(PREMISE_SEEDS)
        title = genre  # placeholder until the LLM names the arc in its first script

        arc_id = db.create_arc(
            title=title,
            genre=genre,
            premise=premise,
            target_panels=target_panels,
            metadata={"created_seed": premise}
        )
        logger.info(f"New story arc #{arc_id} seeded: '{title}' ({genre}), target {target_panels} panels")
        return db.get_active_arc()

    async def generate_comic_script(self, transcript_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generate the next comic strip script using llama.cpp.
        Takes dialogue collected during speech periods and maintains continuity.
        """
        arc = await self.get_or_create_active_arc()
        issue_number = db.get_next_issue_number()
        
        arc_comics = db.get_arc_comics(arc["id"])
        panels_used = sum(c["panel_count"] for c in arc_comics)
        arc_progress = min(1.0, panels_used / arc["target_panels"])

        mined_elements = [item["scrambled_text"] for item in transcript_items if item.get("scrambled_text")]
        # Arcs are capped at ARC_MAX_PANELS, which keeps this bounded.
        history = story_so_far(arc_comics)
        history_section = (
            "Story So Far (text of every previous issue, oldest first). Continue this plot directly: "
            f"keep the same characters, running jokes, and unresolved threads:\n{history}\n\n"
        ) if history else ""

        system_prompt = (
            "You are the master creator of 'COMACK', an ongoing, surreal comic strip that transforms "
            "ambient room vibes and mined thematic elements into a continuous, absurdist graphic narrative. "
            "You are given room notes: a vibe, an energy level, and a surreal line riffing on something "
            "overheard in the room (some words swapped for absurd substitutes). Put those lines, swaps and all, "
            "into characters' mouths, captions, and situations, twisted to fit the active story arc. "
            "Characters treat ridiculous events with total, deadpan melodrama and serious urgency. "
            "You must output ONLY valid, parseable JSON conforming strictly to the requested schema."
        )
        
        needs_name = not json.loads(arc.get("metadata_json") or "{}").get("named")
        name_task = ('\n5. This arc has no title yet. Name it in "arc_title": a punchy, deadpan series title '
                     'that fits the genre and premise.') if needs_name else ""

        prompt_content = f"""
Current Narrative Arc #{arc['id']}: {"(untitled)" if needs_name else repr(arc['title'])}
Genre: {arc['genre']}
Arc Premise: {arc['premise']}
Arc Progress: {arc_progress * 100:.1f}% ({panels_used} of {arc['target_panels']} panels drawn)
Issue Number: #{issue_number}

{history_section}Room notes since the last issue ([VIBE • ENERGY] line):
{json.dumps(mined_elements, indent=2) if mined_elements else '["(Eerie room silence... only the ambient hum of quantum machinery)"]'}

TASK:
1. Plan the story beats this issue needs, then choose the page layout whose shape serves them
   (pacing, number of beats, energy). Write exactly as many panels as the layout has:
{LAYOUT_MENU}
2. Provide descriptive visual prompts for black-and-white ink comic illustrations, specifically optimized for a 1-bit monochrome e-ink display:
   - Emphasize bold silhouettes, strong foreground/background contrast, and distinct outlines.
   - Describe scenes with stark black shadows, bright white highlights, crosshatching, halftone dots, or woodcut engraving textures.
   - Avoid subtle color nuances, smoky haze, or smooth gray gradients that turn into muddy noise when dithered to 1-bit.
3. Decide how many room notes to collect before the next issue ("lines_wanted_next", 1-30):
   fewer for a fast, punchy beat; more to let the next issue build from a richer stretch of the room.
4. Output STRICT JSON in this exact structure{' (plus "arc_title")' if needs_name else ''}:
{{
  "title": "A witty, deadpan issue title",
  "layout_type": "one of the layout names above",
  "panels": [
    {{
      "panel_number": 1,
      "scene_description": "Most important subject first: characters, action, setting, stark shadows, crosshatching",
      "narration": "Optional caption box text (e.g. 'Meanwhile, in Sector 9...')",
      "speech_balloons": [
        {{"character": "Character Name", "text": "Dialogue built from the room notes"}}
      ],
      "sound_effect": "Optional comic SFX e.g. 'BZZZZT' or 'CLICK'"
    }}
  ],
  "lines_wanted_next": 8,
  "arc_status": "CONTINUE"
}}{name_task}
"""

        script = await self._call_llama_cpp(system_prompt, prompt_content)
        
        # Fallback if LLM call fails or returns unparseable JSON
        if not script or "panels" not in script:
            script = self._create_fallback_script(issue_number, arc, mined_elements)

        arc_title = arc["title"]
        if needs_name and str(script.get("arc_title") or "").strip():
            arc_title = str(script["arc_title"]).strip()
            db.rename_arc(arc["id"], arc_title)

        layout = resolve_layout(script.get("layout_type", ""), len(script["panels"]))
        panels = script["panels"][:len(LAYOUTS[layout][1])]
        return {
            "issue_number": issue_number,
            "arc_id": arc["id"],
            "arc_title": arc_title,
            "title": script.get("title", f"Episode #{issue_number}"),
            "layout_type": layout,
            "panel_count": len(panels),
            "panels": panels,
            "lines_wanted_next": lines_wanted(script),
            "dialogue_used": mined_elements
        }

    async def _call_llama_cpp(self, system_prompt: str, user_prompt: str, base_url: str = "") -> Optional[Dict[str, Any]]:
        """Call llama.cpp OpenAI-compatible chat completion endpoint (default: the main model)."""
        base = (base_url or settings.LLAMA_CPP_URL).rstrip('/')
        if not base.endswith("/v1"):
            url = f"{base}/v1/chat/completions"
        else:
            url = f"{base}/chat/completions"
        payload = {
            "model": settings.LLAMA_CPP_MODEL or "default",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.85,
            "response_format": {"type": "json_object"}
        }
        entry = {"at": time.time(), "model": url, "system": system_prompt, "prompt": user_prompt, "output": None}
        prompt_log.append(entry)

        try:
            # Reasoning model with thinking on: generation can take minutes.
            async with httpx.AsyncClient(timeout=600.0) as client:
                logger.info(f"Sending prompt to llama.cpp at {url}")
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    entry["output"] = content
                    # Extract JSON block if wrapped in markdown
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0].strip()
                    elif "```" in content:
                        content = content.split("```")[1].split("```")[0].strip()
                    return json.loads(content)
                else:
                    entry["output"] = f"HTTP {resp.status_code}: {resp.text}"
                    logger.warning(f"llama.cpp returned status {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            entry["output"] = entry["output"] or f"error: {e!r}"
            logger.warning(f"Failed to query llama.cpp at {url}: {e}")
            
        # Try OpenRouter fallback if configured
        if settings.OPENROUTER_API_KEY:
            return await self._call_openrouter_fallback(system_prompt, user_prompt)
            
        return None

    async def _call_openrouter_fallback(self, system_prompt: str, user_prompt: str) -> Optional[Dict[str, Any]]:
        """Secondary fallback to OpenRouter API if local server is unreachable."""
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "HTTP-Referer": "https://github.com/comack",
            "X-Title": "Comack"
        }
        payload = {
            "model": "google/gemini-2.5-flash",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "response_format": {"type": "json_object"}
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                logger.info("Attempting OpenRouter fallback query...")
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0].strip()
                    return json.loads(content)
        except Exception as e:
            logger.warning(f"OpenRouter fallback failed: {e}")
        return None

    def _create_fallback_script(self, issue_number: int, arc: Dict[str, Any], mined_elements: List[str]) -> Dict[str, Any]:
        """Robust procedural fallback script when LLM is unavailable."""
        line1 = "The anomalous readings are spiking across the perimeter!"
        line2 = "Deploy the secondary harmonic condenser at once!"
        
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
            "panels": [
                {
                    "panel_number": 1,
                    "scene_description": f"Dramatic wide shot in {arc['genre']} style. A shadowed figure stares at a glowing console.",
                    "narration": f"Meanwhile, during {arc['title']}...",
                    "speech_balloons": [
                        {"character": "Agent Banjo", "text": line1}
                    ],
                    "sound_effect": "HUMMMM"
                },
                {
                    "panel_number": 2,
                    "scene_description": "Close up reaction shot of a bewildered cyborg holding a mysterious mechanical device.",
                    "narration": None,
                    "speech_balloons": [
                        {"character": "Doctor Rutabaga", "text": line2}
                    ],
                    "sound_effect": "KLANG!"
                }
            ]
        }


narrative_engine = NarrativeEngine()
