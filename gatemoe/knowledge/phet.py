"""SIMULATION SPECIALIST: links a lesson to PhET interactive simulations stored offline.

Kiwix packages the PhET simulations (University of Colorado Boulder, CC BY 4.0) as one ZIM per
language (``phet_<lang>_all``, 60-150 MB). Each archive has a ``catalog.js`` listing every sim's
id, localized title and categories. Matching is deterministic (no model time):

    lesson key terms + search queries + request
      -> title-coverage score per sim (IDF-weighted, prefix match for inflected words)
      -> English titles matched with English queries are mapped to the learner's language
         version of the same sim (``<id>_<lang>.html``) when that archive is installed
      -> top-k sims, served by the web app at /zim/<zim>/<path> inside a sandbox
"""
from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter

from ..config import Config
from . import KnowledgeBase, tokenize

# Words that say little about a sim's topic ("Capacitor Lab: Basics" is about capacitors) ...
STOP = {"a", "an", "the", "of", "and", "in", "on", "to", "for", "with", "by", "at", "from", "is", "are",
        "basics", "intro", "introduction", "lab", "virtual", "essentials", "kit", "construction", "simulation"}
# ... and qualifiers that distinguish variants of one sim but rarely appear in a learner's request.
QUALIFIERS = {"ac": 0.3, "dc": 0.3}
SUBJECT_CATEGORIES = {
    "physics": {"physics"}, "chemistry": {"chemistry"}, "biology": {"biology"}, "mathematics": {"math"},
    "earth_science": {"earth-science"}, "electronics": {"physics"}, "electrical_engineering": {"physics"},
    "mechanical_engineering": {"physics"}, "civil_engineering": {"physics"},
}
_BIDI = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def _fold(tok: str) -> str:
    """English plural folding (laws -> law, circuits -> circuit); other scripts are left as they are."""
    if tok.isascii() and len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def title_tokens(title: str) -> list[str]:
    return [t for t in dict.fromkeys(_fold(t) for t in tokenize(title)) if t not in STOP]


def query_tokens(text: str) -> set[str]:
    return {_fold(t) for t in tokenize(text)}


def _matches(tok: str, query: set[str]) -> bool:
    if tok in query:
        return True
    if len(tok) < 3:                      # "ph", "dc": exact only
        return False
    # prefix match for inflected forms (gas/gases, Kannada case endings: ನಿಯಮ/ನಿಯಮವನ್ನು)
    return any(len(q) >= 3 and (q.startswith(tok) or tok.startswith(q)) for q in query)


def score_titles(sims: list[dict], query: set[str]) -> dict[str, float]:
    """How well the queries cover each title, 0..1, keyed by sim id: the IDF-weighted share of the
    title's words that the queries mention, x0.7 when the title's most distinctive word is missing
    ("Kepler's Laws" must not match a lesson on Ohm's law through "law")."""
    if not sims or not query:
        return {}
    toks = {s["id"]: title_tokens(s["title"]) for s in sims}
    df = Counter(t for ts in toks.values() for t in ts)
    n = len(sims)
    out = {}
    for sid, ts in toks.items():
        if not ts:
            continue
        idf = {t: math.log(1 + n / df[t]) for t in ts}
        w = {t: v * QUALIFIERS.get(t, 1.0) for t, v in idf.items()}
        matched = {t for t in ts if _matches(t, query)}
        if not matched:
            continue
        score = sum(w[t] for t in matched) / sum(w.values())
        if max(idf[t] for t in matched) < max(idf.values()) - 1e-9:
            score *= 0.7
        out[sid] = score
    return out


def parse_catalog(text: str) -> list[dict]:
    """``window.importedData = {...}`` -> [{id, title, lang, categories}]."""
    data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    out = []
    for lang, sims in (data.get("simsByLanguage") or {}).items():
        for s in sims:
            sid, title = str(s.get("id") or ""), _BIDI.sub("", str(s.get("title") or "")).strip()
            if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", sid) and title:
                out.append({"id": sid, "title": title, "lang": str(s.get("language") or lang),
                            "categories": [c.get("slug") for c in s.get("categories", []) if isinstance(c, dict)]})
    return out


class SimulationFinder:
    def __init__(self, cfg: Config, kb: KnowledgeBase):
        self.cfg = cfg
        self.kb = kb
        self._sims: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def enabled(self) -> bool:
        return bool(self.cfg.get("simulations.enabled", True))

    def collections(self) -> list[dict]:
        return [z for z in self.kb.catalog() if z.get("kind") == "simulations" and "error" not in z]

    def available(self) -> bool:
        return self.enabled() and bool(self.collections())

    def sims(self, z: dict) -> list[dict]:
        """Every simulation in one PhET archive (cached per file)."""
        with self._lock:
            if z["file"] in self._sims:
                return self._sims[z["file"]]
        a = self.kb._archive(z["file"])
        sims = []
        try:
            content, _, _ = self.kb.read_entry(z, "catalog.js")
            for s in parse_catalog(content.decode("utf-8", "replace")):
                path = f"{s['id']}_{s['lang']}.html"
                if a.has_entry_by_path(path):
                    thumb = f"{s['id']}.png"
                    sims.append({**s, "zim": z["name"], "path": path,
                                 "thumbnail": thumb if a.has_entry_by_path(thumb) else None})
        except (KeyError, ValueError):
            sims = []
        with self._lock:
            self._sims[z["file"]] = sims
        return sims

    def find(self, queries: list[tuple[str, str]], learner_lang: str, subject: str = "other",
             k: int | None = None) -> list[dict]:
        """queries: (text, language code). Returns the best-matching sims, learner language preferred."""
        k = int(self.cfg.get("simulations.max_results", 3)) if k is None else k
        min_score = float(self.cfg.get("simulations.min_score", 0.5))
        cats = SUBJECT_CATEGORIES.get(subject)
        by_lang: dict[str, dict] = {}
        for z in self.collections():
            by_lang[z["lang"]] = z                    # catalogue is sorted by file name: newest date wins
        scores: dict[str, float] = {}
        for lang, z in by_lang.items():
            if lang not in (learner_lang, "en"):
                continue
            q = {t for text, ql in queries
                 if ql == lang or (lang == "en" and text.isascii()) for t in query_tokens(text)}
            sims = {s["id"]: s for s in self.sims(z)}
            for sid, sc in score_titles(list(sims.values()), q).items():
                if cats and not cats.intersection(sims[sid]["categories"]):
                    sc *= 0.8                         # soft penalty: outside the router's subject
                scores[sid] = max(scores.get(sid, 0.0), sc)
        ranked = sorted((sid for sid, sc in scores.items() if sc >= min_score), key=lambda s: (-scores[s], len(s), s))
        en = {s["id"]: s for s in self.sims(by_lang["en"])} if "en" in by_lang else {}
        picked, seen_titles = [], set()
        for sid in ranked:                        # one sim per topic: "CCK: DC" and "CCK: DC - Virtual Lab"
            key = tuple(title_tokens(en[sid]["title"])) if sid in en else (sid,)
            if key not in seen_titles:
                seen_titles.add(key)
                picked.append(sid)
        out = []
        for sid in picked[:k]:
            for lang in (learner_lang, "en"):        # learner-language version of the sim when installed
                z = by_lang.get(lang)
                hit = next((s for s in self.sims(z) if s["id"] == sid), None) if z else None
                if hit:
                    out.append({**hit, "score": round(scores[sid], 3)})
                    break
        return out
