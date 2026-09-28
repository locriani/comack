"""
Stochastic Part-of-Speech (POS) Scrambler for "Differential Privacy".
Swaps a fraction of words for absurd words of the exact same part of speech
(Penn Treebank tag from nltk's perceptron tagger), so the sentence stays grammatical:
nouns for nouns (plural for plural), proper nouns for proper nouns, verbs in the same
tense, adjectives, -ly adverbs, and prepositions for prepositions.
"""
import random
import re
from typing import List, Optional, Tuple

import nltk

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

ABSURD_PROPER_NOUNS = [
    "Gertrude", "Bartholomew", "Zanzibar", "Reginald", "Philippa", "Toledo",
    "Mortimer", "Kalamazoo", "Ignatius", "Winnifred", "Timbuktu", "Cornelius",
    "Hortense", "Poughkeepsie", "Bingley", "Ophelia", "Walla Walla", "Thaddeus"
]

# All regular verbs, so the suffix rules in _conjugate_verb stay correct.
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

ABSURD_ADVERBS = [
    "suspiciously", "majestically", "reluctantly", "sarcastically", "ominously",
    "flamboyantly", "bureaucratically", "clandestinely", "melodramatically",
    "sheepishly", "furiously", "tenderly", "vigorously", "solemnly", "gleefully"
]

# Only prepositions that rarely act as conjunctions ("for", "after", "since" often do), so a
# swap never turns "after he left" into "beneath he left". Used both to pick and to replace.
PREPOSITIONS = {
    "above", "across", "against", "along", "amid", "among", "around", "at", "behind",
    "below", "beneath", "beside", "beyond", "by", "from", "in", "inside", "into", "near",
    "of", "off", "on", "onto", "outside", "over", "through", "throughout", "toward",
    "towards", "under", "underneath", "upon", "with", "within", "without"
}

# be / have / do / get forms and modals carry grammar, not content. Never swapped, whatever
# the tagger says (it tags a sentence-initial "Can" as a proper noun).
AUXILIARIES = {
    "be", "am", "is", "are", "was", "were", "been", "being", "have", "has", "had",
    "having", "do", "does", "did", "doing", "done", "get", "gets", "got", "gotten", "getting",
    "can", "could", "will", "would", "shall", "should", "may", "might", "must"
}

VERB_FORMS = {"VB": "base", "VBP": "base", "VBD": "ed", "VBN": "ed", "VBG": "ing", "VBZ": "s"}


def _conjugate_verb(base: str, form: str) -> str:
    """Conjugate a regular base verb."""
    if form == "ing":
        return (base[:-1] if base.endswith("e") and not base.endswith("ee") else base) + "ing"
    if form == "ed":
        return base + ("d" if base.endswith("e") else "ed")
    if form == "s":
        return base + ("es" if base.endswith(("s", "sh", "ch", "x", "z")) else "s")
    return base


def _pluralize(noun: str) -> str:
    return noun + ("es" if noun.endswith(("s", "sh", "ch", "x", "z")) else "s")


def _tag(words: List[str]) -> List[Tuple[str, str]]:
    try:
        return nltk.pos_tag(words)
    except LookupError:  # tagger data not downloaded yet (~6 MB, one time)
        nltk.download("averaged_perceptron_tagger_eng", quiet=True)
        return nltk.pos_tag(words)


def _replacement(word: str, tag: str) -> Optional[str]:
    """A random word with the same part of speech, or None if this word must not be swapped."""
    w = word.lower()
    if "'" in w or w in AUXILIARIES:
        return None
    if tag == "NN":
        return random.choice(ABSURD_NOUNS)
    if tag == "NNS":
        return _pluralize(random.choice(ABSURD_NOUNS))
    if tag in ("NNP", "NNPS"):
        return random.choice(ABSURD_PROPER_NOUNS)
    if tag in VERB_FORMS:
        return _conjugate_verb(random.choice(ABSURD_VERBS_BASE), VERB_FORMS[tag])
    if tag == "JJ":
        return random.choice(ABSURD_ADJECTIVES)
    if tag == "RB" and w.endswith("ly"):
        return random.choice(ABSURD_ADVERBS)
    if tag == "IN" and w in PREPOSITIONS:
        return random.choice(sorted(PREPOSITIONS - {w}))
    return None


def scramble_text(text: str, scramble_rate: float = 0.10) -> str:
    """
    Replaces about scramble_rate of all words with random words of the same part of speech.
    Preserves capitalization, punctuation, and spacing.
    """
    if not text or not text.strip():
        return text

    tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)*|[^A-Za-z\s]+|\s+", text)
    word_idx = [i for i, t in enumerate(tokens) if t[0].isalpha()]
    tags = _tag([tokens[i] for i in word_idx])

    swaps = {}
    for i, (word, tag) in zip(word_idx, tags):
        sub = _replacement(word, tag)
        if sub and sub.lower() != word.lower():
            swaps[i] = sub

    # Stochastic rounding: exactly scramble_rate of all words on average, even for short sentences.
    k = min(len(swaps), int(scramble_rate * len(word_idx) + random.random()))
    for i in random.sample(sorted(swaps), k):
        sub, orig = swaps[i], tokens[i]
        if orig.isupper() and len(orig) > 1:
            sub = sub.upper()
        elif orig[0].isupper():
            sub = sub[0].upper() + sub[1:]
        tokens[i] = sub
    return "".join(tokens)
