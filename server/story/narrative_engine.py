"""
Story Lorekeeper & Narrative Continuity Engine.
Connects to llama.cpp (OpenAI-compatible /v1 endpoint) on comack-server.
Maintains story arcs with dynamic lifespans (4 hours to 7 days),
incorporates scrambled overheard dialogue into surreal comic scripts,
and chooses adaptive 1-, 2-, or 3-panel layouts.
"""
import json
import time
import random
import logging
import httpx
from typing import Dict, Any, List, Optional
from server.config import settings
from server.storage import db

logger = logging.getLogger("comack.story")

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
    "Time-Traveling Jazz Tragedy"
]

PREMISE_SEEDS = [
    "A retired space admiral and a sentient percolator run an interstellar laundromat that folds space-time.",
    "A world-weary trench-coat detective investigates why all the furniture in Apartment 4B is plotting a mutiny.",
    "A team of clandestine underground botanists must deliver the world's last sarcastic fern to Neo-Chicago.",
    "Two Victorian inventors keep accidentally inventing kitchen appliances that open portals to the year 3042.",
    "A crew of deep-sea submariners discovers that the ocean floor is made entirely of unreturned library books.",
    "An unhinged weather forecaster realizes that the clouds are spelling out petty office gossip in Morse code."
]


class NarrativeEngine:
    def __init__(self):
        pass

    async def get_or_create_active_arc(self) -> Dict[str, Any]:
        """Fetch the current active story arc or seed a new one if expired/absent."""
        arc = db.get_active_arc()
        now = time.time()
        
        if arc:
            started_at = arc["started_at"]
            target_hours = arc["target_duration_hours"]
            elapsed_hours = (now - started_at) / 3600.0
            
            # Check if current arc has naturally expired
            if elapsed_hours >= target_hours:
                logger.info(f"Arc '{arc['title']}' completed after {elapsed_hours:.1f} hours. Seeding new arc.")
                db.conclude_arc(arc["id"])
                arc = None
            else:
                return arc
                
        # Generate a new story arc seed (between 4 hours and 7 days)
        duration_hours = random.uniform(settings.ARC_MIN_HOURS, settings.ARC_MAX_HOURS)
        genre = random.choice(ARC_GENRES)
        premise = random.choice(PREMISE_SEEDS)
        title = f"The {genre} Chronicles"
        
        arc_id = db.create_arc(
            title=title,
            genre=genre,
            premise=premise,
            target_duration_hours=round(duration_hours, 1),
            metadata={"created_seed": premise}
        )
        logger.info(f"New story arc #{arc_id} seeded: '{title}' ({genre}), Target duration: {duration_hours:.1f}h")
        return db.get_active_arc()

    async def generate_comic_script(self, transcript_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generate the next comic strip script using llama.cpp.
        Takes dialogue collected during speech periods and maintains continuity.
        """
        arc = await self.get_or_create_active_arc()
        issue_number = db.get_next_issue_number()
        
        # Calculate how far through the arc we are
        now = time.time()
        elapsed_hours = (now - arc["started_at"]) / 3600.0
        arc_progress = min(1.0, elapsed_hours / max(1.0, arc["target_duration_hours"]))
        
        mined_elements = [item["scrambled_text"] for item in transcript_items if item.get("scrambled_text")]
        
        system_prompt = (
            "You are the master creator of 'COMACK', an ongoing, surreal comic strip that transforms "
            "ambient room vibes and mined thematic elements into a continuous, absurdist graphic narrative. "
            "IMPORTANT PRIVACY RULE: Never quote or reproduce any spoken words directly. "
            "Instead, translate the mined emotional vibes, abstract conflicts, and subtext into 100% ORIGINAL, "
            "SURREAL character dialogue and comic situations fitting the active story arc. "
            "Characters treat ridiculous events with total, deadpan melodrama and serious urgency. "
            "You must output ONLY valid, parseable JSON conforming strictly to the requested schema."
        )
        
        prompt_content = f"""
Current Narrative Arc #{arc['id']}: '{arc['title']}'
Genre: {arc['genre']}
Arc Premise: {arc['premise']}
Arc Progress: {arc_progress * 100:.1f}% (Hour {elapsed_hours:.1f} of {arc['target_duration_hours']}h)
Issue Number: #{issue_number}

Mined Thematic Elements & Emotional Vibes from the past 30 minutes:
{json.dumps(mined_elements, indent=2) if mined_elements else '["(Eerie room silence... only the ambient hum of quantum machinery)"]'}

TASK:
1. Choose an ADAPTIVE layout:
   - "1-panel" if the energy represents a single dramatic climax or contemplative splash scene.
   - "2-panel" if there is a back-and-forth tension or cause-and-effect beat.
   - "3-panel" for classic setup, escalation, and absurd punchline.
3. Provide descriptive visual prompts for black-and-white ink comic illustrations, specifically optimized for a 1-bit monochrome e-ink display:
   - Emphasize bold silhouettes, strong foreground/background contrast, and distinct outlines.
   - Describe scenes with stark black shadows, bright white highlights, crosshatching, halftone dots, or woodcut engraving textures.
   - Avoid subtle color nuances, smoky haze, or smooth gray gradients that turn into muddy noise when dithered to 1-bit.
4. Output STRICT JSON in this exact structure:
{{
  "title": "A witty, deadpan issue title",
  "layout_type": "1-panel" | "2-panel" | "3-panel",
  "panels": [
    {{
      "panel_number": 1,
      "scene_description": "High-contrast black and white ink scene description: bold character silhouettes, heavy ink outlines, stark shadows, crosshatching patterns, setting, perspective",
      "narration": "Optional caption box text (e.g. 'Meanwhile, in Sector 9...')",
      "speech_balloons": [
        {{"character": "Character Name", "text": "Completely original spoken dialogue inspired by the theme"}}
      ],
      "sound_effect": "Optional comic SFX e.g. 'BZZZZT' or 'CLICK'"
    }}
  ],
  "arc_status": "CONTINUE"
}}
"""

        script = await self._call_llama_cpp(system_prompt, prompt_content)
        
        # Fallback if LLM call fails or returns unparseable JSON
        if not script or "panels" not in script:
            script = self._create_fallback_script(issue_number, arc, mined_elements)
            
        return {
            "issue_number": issue_number,
            "arc_id": arc["id"],
            "title": script.get("title", f"Episode #{issue_number}"),
            "layout_type": script.get("layout_type", "2-panel"),
            "panel_count": len(script.get("panels", [])),
            "panels": script.get("panels", []),
            "dialogue_used": mined_elements
        }

    async def _call_llama_cpp(self, system_prompt: str, user_prompt: str) -> Optional[Dict[str, Any]]:
        """Call llama.cpp OpenAI-compatible chat completion endpoint."""
        base = settings.LLAMA_CPP_URL.rstrip('/')
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
        
        try:
            # Reasoning model with thinking on: generation can take minutes.
            async with httpx.AsyncClient(timeout=600.0) as client:
                logger.info(f"Sending prompt to llama.cpp at {url}")
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    # Extract JSON block if wrapped in markdown
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0].strip()
                    elif "```" in content:
                        content = content.split("```")[1].split("```")[0].strip()
                    return json.loads(content)
                else:
                    logger.warning(f"llama.cpp returned status {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
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
            "layout_type": "2-panel",
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
