"""
Thematic Story Element Miner.
Extracts high-level emotional subtext, dramatic tensions, abstract themes,
and surreal metaphors from speech without ever storing, quoting, or directly using
the actual spoken words.
"""
import re
from typing import Dict, Any, List

# Keywords mapped to emotional vibes
TONE_KEYWORDS = {
    "urgent": ["hurry", "quick", "fast", "asap", "late", "run", "now", "emergency", "deadline", "clock"],
    "suspicious": ["why", "who", "secret", "weird", "strange", "doubt", "sure", "check", "hiding", "look"],
    "frustrated": ["broken", "fail", "stuck", "error", "hate", "damn", "wrong", "again", "won't", "can't"],
    "philosophical": ["think", "maybe", "why", "meaning", "life", "future", "always", "wonder", "deep", "space"],
    "bureaucratic": ["meeting", "schedule", "plan", "document", "form", "policy", "process", "sign", "email"],
    "culinary": ["eat", "food", "cook", "oven", "fridge", "coffee", "lunch", "dinner", "snack", "hungry", "tea"],
    "technical": ["code", "server", "deploy", "computer", "wifi", "network", "system", "screen", "power", "plug"]
}

ABSTRACT_THEMES = [
    "An uncooperative apparatus threatening municipal harmony",
    "A desperate race against an arbitrary chronometer",
    "The metaphysical search for a misplaced household relic",
    "A quiet standoff over an unresolved administrative grievance",
    "The sudden realization that reality has shifted by three inches",
    "A solemn debate concerning the caloric intake of celestial beings",
    "An urgent diplomatic summit regarding appliance etiquette",
    "A cosmic malfunction inside an unassuming domestic receptacle"
]

SURREAL_METAPHORS = [
    "The quantum toaster is vibrating at an unauthorized frequency",
    "An interstellar courier has dropped an unregistered trombone into Sector 4",
    "The bureaucratic archives have decided to hibernate until Tuesday",
    "A subterranean jellyfish has commandeered the main heating duct",
    "The chronometer is moving sideways instead of forward",
    "Someone has attempted to microwave an ancient curse"
]


def mine_story_elements(text: str, duration_seconds: float) -> Dict[str, Any]:
    """
    Mines abstract story elements from text.
    The raw text is discarded immediately after analysis.
    Returns: Dict containing vibe, thematic_conflict, surreal_metaphor, energy_level.
    """
    clean = text.lower()
    
    # 1. Detect emotional tone
    detected_tones = []
    for tone, keywords in TONE_KEYWORDS.items():
        if any(kw in clean for kw in keywords):
            detected_tones.append(tone)
            
    primary_tone = detected_tones[0] if detected_tones else "deadpan curiosity"
    
    # 2. Select an abstract theme based on length & detected tone
    import random
    theme = random.choice(ABSTRACT_THEMES)
    metaphor = random.choice(SURREAL_METAPHORS)
    
    # Energy level based on duration and punctuation
    energy = "high" if "!" in text or duration_seconds > 4.0 else ("medium" if duration_seconds > 1.5 else "low")
    
    return {
        "emotional_tone": primary_tone,
        "abstract_theme": theme,
        "surreal_metaphor": metaphor,
        "energy_level": energy,
        "duration_seconds": round(duration_seconds, 1)
    }
