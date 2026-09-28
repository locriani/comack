"""
Stochastic Part-of-Speech (POS) Scrambler for "Differential Privacy".
Swaps a fraction of words for random words of the exact same part of speech
(Penn Treebank tag from nltk's perceptron tagger), so the sentence stays grammatical:
nouns for nouns (plural for plural), proper nouns for proper nouns, verbs in the same
tense, adjectives, -ly adverbs, and prepositions for prepositions.
Replacements come from the Brown corpus (1M tagged words of real English), so there are
thousands of candidates per part of speech, already correctly inflected.
"""
import functools
import random
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

import nltk

# Brown corpus tag -> Penn tag (the tagger's tagset). NPS (plural proper nouns) is left out:
# it is tiny and mostly nationalities, including a slur.
BROWN_TO_PENN = {
    "NN": "NN", "NNS": "NNS", "NP": "NNP", "VB": "VB", "VBD": "VBD", "VBG": "VBG",
    "VBN": "VBN", "VBZ": "VBZ", "JJ": "JJ", "RB": "RB"
}

# Slurs and insults that occur in the (1961) Brown corpus; never used as replacements.
BLOCKLIST = {
    "negro", "negroes", "nigger", "niggers", "savage", "savages", "bastard", "bastards",
    "retarded", "retard", "queer", "bitch", "gypsy", "gypsies", "crackers", "japs", "injun",
    "injuns", "coons", "whore", "whores", "hun", "dyke", "kraut", "krauts", "paddies", "mick",
    "cripple", "spic", "squaw", "wop", "wops"
}

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


def _ensure(resource: str, package: str):
    try:
        nltk.data.find(resource)
    except LookupError:  # one-time download into ~/nltk_data
        nltk.download(package, quiet=True)


@functools.cache
def pools() -> Dict[str, List[str]]:
    """Every Brown corpus word used 3+ times, grouped by Penn tag (~19k words, ~2s to build)."""
    _ensure("corpora/brown", "brown")
    from nltk.corpus import brown
    counts = Counter()
    for word, tag in brown.tagged_words():
        penn = BROWN_TO_PENN.get(tag.split("-")[0])
        if penn and word.isalpha() and len(word) > 2:
            counts[(penn, word)] += 1
    out: Dict[str, List[str]] = {}
    for (penn, word), n in counts.items():
        w = word.lower()
        if n < 3 or w in BLOCKLIST or w in AUXILIARIES:
            continue
        if not (word.istitle() if penn == "NNP" else word.islower()):
            continue
        if penn == "RB" and not w.endswith("ly"):
            continue
        out.setdefault(penn, []).append(word)
    return out


def _tag(words: List[str]) -> List[Tuple[str, str]]:
    _ensure("taggers/averaged_perceptron_tagger_eng", "averaged_perceptron_tagger_eng")
    return nltk.pos_tag(words)


def _replacement(word: str, tag: str) -> Optional[str]:
    """A random word with the same part of speech, or None if this word must not be swapped."""
    w = word.lower()
    if "'" in w or w in AUXILIARIES:
        return None
    if tag == "IN":
        return random.choice(sorted(PREPOSITIONS - {w})) if w in PREPOSITIONS else None
    if tag == "RB" and not w.endswith("ly"):
        return None
    pool = pools().get("VB" if tag == "VBP" else tag)
    return random.choice(pool) if pool else None


def _swap_digits(match: re.Match) -> str:
    num = match.group()
    if sum(c.isdigit() for c in num) < 3:
        return num
    return "".join(random.choice("0123456789".replace(c, "")) if c.isdigit() else c for c in num)


def scramble_digits(text: str) -> str:
    """Every digit of every 3+ digit number (phone, house, card numbers) becomes a different
    random digit. Separators join groups, so 555-1234 and 1,234 count as one number."""
    return re.sub(r"\d(?:[\d,.\-]*\d)?", _swap_digits, text)


def scramble_text(text: str, scramble_rate: float = 0.10) -> str:
    """
    Replaces every digit of 3+ digit numbers, then about scramble_rate of all words with
    random words of the same part of speech. Preserves capitalization, punctuation, and spacing.
    """
    if not text or not text.strip():
        return text
    text = scramble_digits(text)

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
