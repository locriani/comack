"""
Story Lorekeeper & Narrative Continuity Engine.
Connects to llama.cpp (OpenAI-compatible /v1 endpoint) on comack-server.
Maintains story arcs with dynamic lifespans (4 hours to 7 days),
incorporates scrambled overheard dialogue into comic scripts,
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
PROMPT_LOG_TTL_S = 15 * 60


def expire_prompt_log():
    """Forget calls made more than PROMPT_LOG_TTL_S ago (entries are in call order)."""
    cutoff = time.time() - PROMPT_LOG_TTL_S
    while prompt_log and prompt_log[0]["at"] < cutoff:
        prompt_log.popleft()

LAYOUT_MENU = "\n".join(f'   - "{name}" ({len(p)} panel{"s" * (len(p) > 1)}): {use}'
                        for name, (use, p) in LAYOUTS.items())

BIBLE_KEYS = ("title", "genre", "premise", "tone", "art_style")


def arc_bible_prompt(past_arcs: List[Dict[str, Any]]) -> str:
    """Asks for a brand-new series, unlike every arc listed."""
    past = []
    for a in past_arcs:
        meta = json.loads(a.get("metadata_json") or "{}")
        style = "; ".join(s for s in (meta.get("tone"), meta.get("art_style")) if s)
        past.append(f"- {a['title']} ({a['genre']}{'; ' + style if style else ''}): {a['premise']}")
    return f"""Previous series (do NOT reuse their genres, tones, art styles, settings, or characters):
{chr(10).join(past) or "- (none yet)"}

Invent a completely new comic series. Draw from the whole range of comics and storytelling:
any genre, era, audience, and kind of humor or drama.
Output: {{"title": "the series title", "genre": "a few words", "premise": "one or two sentences",
"tone": "how the story is told: voice, kind of humor or drama, how the characters behave",
"art_style": "the visual style, as a phrase an image model understands",
"cast": ["Name: one-line description"]}}"""


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

        # The arc's length is fixed here; the script model never decides when it ends.
        target_panels = random.randint(settings.ARC_MIN_PANELS, settings.ARC_MAX_PANELS)
        bible = await self._call_llama_cpp("You invent comic series. Output ONLY valid JSON.",
                                           arc_bible_prompt(db.get_arcs()[-12:]))
        if not bible or not all(str(bible.get(k) or "").strip() for k in BIBLE_KEYS):
            raise RuntimeError("the LLM did not invent a new arc; the next cycle retries")
        bible = {k: str(bible[k]).strip() for k in BIBLE_KEYS} | {"cast": [str(c) for c in bible.get("cast") or []][:8]}

        arc_id = db.create_arc(
            title=bible["title"],
            genre=bible["genre"],
            premise=bible["premise"],
            target_panels=target_panels,
            metadata={k: bible[k] for k in ("tone", "art_style", "cast")}
        )
        logger.info(f"New story arc #{arc_id}: '{bible['title']}' ({bible['genre']}), target {target_panels} panels")
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

        meta = json.loads(arc.get("metadata_json") or "{}")
        tone = meta.get("tone") or "whatever best fits the genre"
        art_style = meta.get("art_style") or "whatever best fits the genre"
        cast = "\nCast:" + "".join(f"\n- {c}" for c in meta["cast"]) if meta.get("cast") else ""

        system_prompt = (
            f"You write {arc['title']!r}, a {arc['genre']} comic series. Tone: {tone}. "
            f"Art style: {art_style}. Stay true to both in every issue. "
            "You are given room notes: a vibe, an energy level, and a line riffing on something "
            "overheard in the room (some words swapped for random substitutes). Put those lines, swaps and all, "
            "into characters' mouths, captions, and situations, twisted to fit the story. "
            "You must output ONLY valid, parseable JSON conforming strictly to the requested schema."
        )

        prompt_content = f"""
Series: {arc['title']}
Genre: {arc['genre']}
Premise: {arc['premise']}{cast}
Arc Progress: {arc_progress * 100:.1f}% ({panels_used} of {arc['target_panels']} panels drawn).
  The arc ends when {arc['target_panels']} panels are drawn: pace the story to land there.
Issue Number: #{issue_number}

{history_section}Room notes since the last issue ([VIBE • ENERGY] line):
{json.dumps(mined_elements, indent=2, ensure_ascii=False) if mined_elements else '["(Eerie room silence... only the ambient hum of quantum machinery)"]'}

TASK:
1. Plan the story beats this issue needs, then choose the page layout whose shape serves them
   (pacing, number of beats, energy). Write exactly as many panels as the layout has:
{LAYOUT_MENU}
2. Write each scene_description as an image prompt in the series' art style.
3. Decide how many room notes to collect before the next issue ("lines_wanted_next", 1-30):
   fewer for a fast, punchy beat; more to let the next issue build from a richer stretch of the room.
4. Output STRICT JSON in this exact structure:
{{
  "title": "This issue's title, in the series' voice",
  "layout_type": "one of the layout names above",
  "panels": [
    {{
      "panel_number": 1,
      "scene_description": "Most important subject first: characters, action, setting, in the series' art style",
      "narration": "Optional caption box text (e.g. 'Meanwhile, in Sector 9...')",
      "speech_balloons": [
        {{"character": "Character Name", "text": "Dialogue built from the room notes"}}
      ],
      "sound_effect": "Optional comic SFX e.g. 'BZZZZT' or 'CLICK'"
    }}
  ],
  "lines_wanted_next": 8
}}
"""

        script = await self._call_llama_cpp(system_prompt, prompt_content)
        
        # Fallback if LLM call fails or returns unparseable JSON
        if not script or "panels" not in script:
            script = self._create_fallback_script(issue_number, arc, mined_elements)

        layout = resolve_layout(script.get("layout_type", ""), len(script["panels"]))
        panels = script["panels"][:len(LAYOUTS[layout][1])]
        return {
            "issue_number": issue_number,
            "arc_id": arc["id"],
            "arc_title": arc["title"],
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
