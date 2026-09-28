"""
Stochastic Part-of-Speech (POS) Scrambler for "Differential Privacy".
Stochastically substitutes nouns, verbs, and adjectives with random words
of the same grammatical category to obscure private details while injecting
surreal humor into the comic generation prompt.
"""
import random
import re
from typing import Tuple, List, Set

# Curated absurd, surreal, and deadpan word banks by grammatical category
ABSURD_NOUNS = [
    "casserole", "toaster", "jellyfish", "trombone", "kazoo", "kumquat",
    "badger", "monocle", "submariner", "spatula", "accordion", "rutabaga",
    "manatee", "armadillo", "turnip", "goblet", "periscope", "bureaucrat",
    "anvil", "cantaloupe", "artichoke", "waffle", "banjo", "zeppelin",
    "astrolabe", "tapioca", "flamingo", "poodle", "parsnip", "tuba",
    "hovercraft", "walrus", "caterpillar", "plunger", "saxophone",
    "dinghy", "platypus", "chihuahua", "marshmallow", "tricycle",
    "bagpipe", "mayonnaise", "macaroni", "chupacabra", "iguana",
    "meteorite", "hedgehog", "porcupine", "tambourine", "kaleidoscope"
]

ABSURD_VERBS_BASE = [
    "hypnotize", "ferment", "levitate", "interrogate", "marinate",
    "disintegrate", "recalibrate", "tickle", "serenade", "galvanize",
    "infiltrate", "teleport", "combust", "exfoliate", "confiscate",
    "conspire", "catapult", "orchestrate", "vaporize", "defenestrate",
    "crystallize", "bamboozle", "transmute", "investigate", "juggle"
]

ABSURD_ADJECTIVES = [
    "celestial", "radioactive", "melodramatic", "suspicious", "inflatable",
    "existential", "quantum", "biodegradable", "flamboyant", "hyperbolic",
    "unhinged", "clandestine", "ornamental", "subterranean", "gothic",
    "crustacean", "holographic", "sarcastic", "velvet", "fluorescent",
    "clueless", "spectral", "nebulous", "eccentric", "microscopic",
    "bohemian", "apocalyptic", "hypnotic", "vintage", "audacious"
]

# Common pronouns and grammatical stopwords that should NEVER be swapped
STOPWORDS: Set[str] = {
    "i", "me", "my", "myself", "we", "our", "ours", "you", "your", "yours",
    "he", "him", "his", "she", "her", "hers", "it", "its", "they", "them",
    "their", "what", "which", "who", "whom", "this", "that", "these", "those",
    "am", "is", "are", "was", "were", "be", "been", "being", "have", "has",
    "had", "do", "does", "did", "would", "should", "could", "ought", "shall",
    "will", "can", "may", "might", "must", "the", "a", "an", "and", "but",
    "if", "or", "because", "as", "until", "while", "of", "at", "by", "for",
    "with", "about", "against", "between", "into", "through", "during", "before",
    "after", "above", "below", "to", "from", "up", "down", "in", "out", "on",
    "off", "over", "under", "again", "further", "then", "once", "here", "there",
    "when", "where", "why", "how", "all", "any", "both", "each", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same",
    "so", "than", "too", "very", "s", "t", "can", "will", "just", "don", "should"
}


def _conjugate_verb(base: str, tense_hint: str) -> str:
    """Conjugate a base verb to match a given tense ending."""
    if tense_hint == "ing":
        if base.endswith("e") and not base.endswith("ee"):
            return base[:-1] + "ing"
        return base + "ing"
    elif tense_hint == "ed":
        if base.endswith("e"):
            return base + "d"
        return base + "ed"
    elif tense_hint == "s":
        if base.endswith(("s", "sh", "ch", "x", "z")):
            return base + "es"
        elif base.endswith("y") and base[-2] not in "aeiou":
            return base[:-1] + "ies"
        return base + "s"
    return base


def _classify_word(word: str) -> Tuple[str, str]:
    """
    Heuristic rule-based POS classifier.
    Returns (pos_category, tense_or_variant).
    Categories: 'NOUN', 'VERB', 'ADJ', 'OTHER'.
    """
    clean = word.lower()
    if clean in STOPWORDS or len(clean) <= 2:
        return "OTHER", ""
    
    # Check verb endings
    if clean.endswith("ing") and len(clean) > 5:
        return "VERB", "ing"
    if clean.endswith("ed") and len(clean) > 4:
        return "VERB", "ed"
    if clean.endswith("ize") or clean.endswith("ise") or clean.endswith("ate"):
        return "VERB", "base"
    
    # Check adjective endings
    if clean.endswith(("ous", "ive", "ful", "less", "able", "ible", "ic", "al", "ish")):
        return "ADJ", ""
    
    # Check noun endings
    if clean.endswith(("tion", "sion", "ment", "ness", "ity", "ance", "ence", "er", "or", "ist")):
        return "NOUN", "singular"
    if clean.endswith("s") and not clean.endswith("ss") and len(clean) > 3:
        return "NOUN", "plural"
        
    # Default fallback heuristics based on length
    if len(clean) > 3:
        # 60% chance treat as noun, 40% as adj
        return "NOUN", "singular"
    
    return "OTHER", ""


def scramble_text(text: str, scramble_rate: float = 0.25) -> str:
    """
    Stochastically scrambles a percentage of words with random words of the same POS.
    Preserves case (capitalization) and punctuation.
    """
    if not text or not text.strip():
        return text
    
    tokens = re.findall(r"[\w]+|[^\w\s]|\s+", text)
    result = []
    
    for token in tokens:
        # Check if token is a word
        if not re.match(r"^[A-Za-z]+$", token):
            result.append(token)
            continue
            
        category, hint = _classify_word(token)
        
        # Decide if we scramble this word
        if category != "OTHER" and random.random() < scramble_rate:
            if category == "NOUN":
                sub = random.choice(ABSURD_NOUNS)
                if hint == "plural":
                    sub = sub + "s" if not sub.endswith("s") else sub + "es"
            elif category == "VERB":
                base = random.choice(ABSURD_VERBS_BASE)
                sub = _conjugate_verb(base, hint)
            elif category == "ADJ":
                sub = random.choice(ABSURD_ADJECTIVES)
            else:
                sub = token
                
            # Match original capitalization
            if token.isupper():
                sub = sub.upper()
            elif token[0].isupper():
                sub = sub.capitalize()
                
            result.append(sub)
        else:
            result.append(token)
            
    return "".join(result)
