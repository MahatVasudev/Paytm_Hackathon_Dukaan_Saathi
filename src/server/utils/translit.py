"""Devanagari -> Latin transliteration for party names, so Hindi input ("रमेश") matches names
stored in Latin script ("Ramesh"). Pure Python, no dependencies. Output is a search key, not
dictionary-perfect romanization: it targets the spellings shopkeepers actually type."""
import re
import unicodedata

_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h",
}
_NUKTA = {"क": "q", "ख": "kh", "ग": "g", "ज": "z", "ड": "r", "ढ": "rh", "फ": "f"}
_INDEP = {"अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ऋ": "ri",
          "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o"}
_MATRA = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri",
          "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o"}
_NASAL = {"ं": "n", "ँ": "n", "ः": "h"}
VIRAMA, NUKTA = "्", "़"

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

HONORIFICS = {"ji", "bhai", "bhaiya", "bhaiyya", "bhaisahab", "sahab", "saab", "sahib", "didi", "behen", "ben",
              "aunty", "auntie", "uncle", "mr", "mrs", "ms", "shri", "shree", "sri", "seth", "sheth", "lala",
              "kaka", "mama", "chacha", "mausi", "bua", "dada", "dadi", "madam", "sir", "bhabhi"}


def has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI.search(text or ""))


def _word(word: str) -> str:
    word = unicodedata.normalize("NFD", word)       # split precomposed nukta letters (ड़ -> ड + ़)
    out, i, n = [], 0, len(word)
    while i < n:
        ch = word[i]
        if ch in _CONS:
            nxt = word[i + 1] if i + 1 < n else ""
            base = _NUKTA[ch] if nxt == NUKTA and ch in _NUKTA else _CONS[ch]
            if nxt == NUKTA:
                i += 1
                nxt = word[i + 1] if i + 1 < n else ""
            out.append(base)
            if nxt in _MATRA:
                out.append(_MATRA[nxt]); i += 1
            elif nxt == VIRAMA:
                i += 1
            else:
                out.append("a")                     # inherent vowel (schwa) - trimmed below
        elif ch in _INDEP:
            out.append(_INDEP[ch])
        elif ch in _NASAL:
            out.append(_NASAL[ch])
        elif ch in (VIRAMA, NUKTA):
            pass
        else:
            out.append(ch)
        i += 1
    latin = "".join(out)
    # Hindi drops the word-final schwa: रमेश -> ramesh (not ramesha); but keep it for 1-syllable words
    if has_devanagari(word) and len(latin) > 2 and word[-1] in _CONS and latin.endswith("a"):
        latin = latin[:-1]
    return latin


def transliterate(text: str) -> str:
    return re.sub(r"[ऀ-ॿ]+", lambda m: _word(m.group(0)), text or "")


def search_key(name: str) -> str:
    """Lowercase Latin search key: transliterated, honorifics dropped, whitespace squeezed."""
    key = transliterate(name).lower().strip()
    key = re.sub(r"[^\w\s]", " ", key)
    tokens = [t for t in key.split() if t]
    kept = [t for t in tokens if t not in HONORIFICS]
    return " ".join(kept or tokens)
