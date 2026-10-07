"""Language identification for short learner requests (milliseconds, no model swap).

1. Unicode script decides most non-Latin languages outright (Kannada, Tamil, Telugu, ...).
2. Devanagari is ambiguous (Hindi / Marathi) and Latin is very ambiguous on short text, so
   py3langid restricted to the configured languages breaks the tie.
3. Short Latin-script requests with low confidence default to English (the common case);
   the UI also lets the learner pick the language explicitly.
"""
from __future__ import annotations

import functools
import re
import unicodedata

# Unicode script prefix (from unicodedata.name) -> language, when the script is unambiguous.
_SCRIPT_LANG = {
    "KANNADA": "kn", "TAMIL": "ta", "TELUGU": "te", "BENGALI": "bn", "MALAYALAM": "ml",
    "GUJARATI": "gu", "GURMUKHI": "pa", "HANGUL": "ko", "HIRAGANA": "ja", "KATAKANA": "ja",
    "THAI": "th", "GREEK": "el", "HEBREW": "he", "ARABIC": "ar",
}
_AMBIGUOUS = {"DEVANAGARI": ["hi", "mr"], "CYRILLIC": ["ru", "uk", "bg"], "CJK": ["zh", "ja"]}
# Words that are English and NOT also common short words in Portuguese/Spanish/Italian/Catalan
# ("me", "i", "a" were removed: "Me explique a lei de Ohm" is Portuguese).
_EN_HINT = re.compile(r"\b(the|what|how|why|explain|learn|teach|about|is|are|of|and|with|want|make|show|please|"
                      r"tell|works?|does|do)\b", re.I)


def _script_of(ch: str) -> str | None:
    if not ch.isalpha():
        return None
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return None
    if name.startswith("CJK"):
        return "CJK"
    return name.split(" ", 1)[0]


def dominant_script(text: str) -> str | None:
    counts: dict[str, int] = {}
    for ch in text:
        s = _script_of(ch)
        if s:
            counts[s] = counts.get(s, 0) + 1
    return max(counts, key=counts.get) if counts else None


@functools.lru_cache(maxsize=1)
def _identifier(langs: tuple[str, ...]):
    from py3langid.langid import MODEL_FILE, LanguageIdentifier

    ident = LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)
    known = set(ident.nb_classes)
    ident.set_languages([l for l in langs if l in known])
    return ident


def detect_language(text: str, supported: list[str] | None = None) -> dict:
    """Return {'lang', 'confidence', 'method'} for a learner request."""
    if supported is None:
        from .config import load_config
        supported = list(load_config().languages)
    text = (text or "").strip()
    if not text:
        return {"lang": "en", "confidence": 0.0, "method": "empty"}
    script = dominant_script(text)
    if script in _SCRIPT_LANG and _SCRIPT_LANG[script] in supported:
        return {"lang": _SCRIPT_LANG[script], "confidence": 1.0, "method": f"script:{script.lower()}"}
    if script == "CJK" and any(_script_of(c) in ("HIRAGANA", "KATAKANA") for c in text):
        return {"lang": "ja", "confidence": 1.0, "method": "script:kana"}
    candidates = _AMBIGUOUS.get(script or "", supported)
    candidates = [c for c in candidates if c in supported] or supported
    if len(candidates) == 1:
        return {"lang": candidates[0], "confidence": 1.0, "method": f"script:{(script or '').lower()}"}
    try:
        lang, prob = _identifier(tuple(sorted(candidates))).classify(text)
    except Exception:
        lang, prob = candidates[0], 0.0
    prob = float(prob)
    if script == "LATIN" and "en" in supported and lang != "en":
        words = len(text.split())
        hints = {m.lower() for m in _EN_HINT.findall(text)}
        if (prob < 0.9 and len(hints) >= 2) or (words <= 3 and prob < 0.6):
            return {"lang": "en", "confidence": round(prob, 3), "method": "latin-short-default"}
    return {"lang": lang, "confidence": round(prob, 3), "method": "py3langid"}
