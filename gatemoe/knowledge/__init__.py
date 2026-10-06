"""KNOWLEDGE GATEWAY: offline retrieval over Kiwix ZIM archives (a local piece of the internet).

    queries (English + learner language)
      -> pick ZIMs by language (+ English) and subject     (second-level routing: which collections)
      -> Xapian full-text search inside each ZIM (title search when a ZIM has no full-text index)
      -> fetch top articles, HTML -> clean text, ~200-word passages
      -> BM25 rerank across all passages -> top-k with citations (zim name + entry path)
No index is built on the Pi: every Kiwix ZIM ships its own full-text index.
"""
from __future__ import annotations

import math
import re
import threading
import time
import unicodedata
from collections import Counter
from pathlib import Path

from ..config import Config
from ..runtime.events import EventLog
from .htmltext import html_to_paragraphs

SUBJECT_HINTS = {
    "physics": ["physics"], "chemistry": ["chemistry", "chem"], "biology": ["biology", "bio", "medicine"],
    "mathematics": ["mathematics", "math", "maths"], "computer_science": ["computer", "devdocs", "stackoverflow", "python"],
    "electronics": ["electronics", "physics", "eng"], "electrical_engineering": ["electronics", "physics", "eng"],
    "mechanical_engineering": ["physics", "eng"], "civil_engineering": ["eng", "physics"],
    "earth_science": ["geo", "earth", "physics"],
}


def tokenize(text: str) -> list[str]:
    """Words for BM25: runs of letters/marks/digits (keeps Indic vowel signs); CJK as bigrams."""
    toks, cur = [], []
    for ch in text.lower():
        cat = unicodedata.category(ch)
        if "一" <= ch <= "鿿" or "぀" <= ch <= "ヿ":
            if cur:
                toks.append("".join(cur))
                cur = []
            toks.append(ch)
        elif cat[0] in ("L", "M", "N"):
            cur.append(ch)
        elif cur:
            toks.append("".join(cur))
            cur = []
    if cur:
        toks.append("".join(cur))
    out = []
    for i, t in enumerate(toks):  # join consecutive CJK chars into bigrams
        if len(t) == 1 and ("一" <= t <= "鿿" or "぀" <= t <= "ヿ"):
            if i + 1 < len(toks) and len(toks[i + 1]) == 1:
                out.append(t + toks[i + 1])
            continue
        if len(t) > 1 or t.isdigit():
            out.append(t)
    return out


def bm25_rank(query_tokens: list[str], docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> list[float]:
    n = len(docs)
    if not n:
        return []
    avgdl = sum(len(d) for d in docs) / n or 1.0
    df = Counter(t for d in docs for t in set(d))
    q = set(query_tokens)
    scores = []
    for d in docs:
        tf = Counter(d)
        s = 0.0
        for t in q:
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avgdl))
        scores.append(s)
    return scores


def split_passages(paragraphs: list[str], words: int) -> list[str]:
    out, cur, n = [], [], 0
    for p in paragraphs:
        w = max(1, len(p.split()), len(p) // 6)  # scripts without spaces count by characters
        if n and n + w > words:
            out.append("\n".join(cur))
            cur, n = [], 0
        cur.append(p)
        n += w
    if cur:
        out.append("\n".join(cur))
    return out


class KnowledgeBase:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.dir = cfg.path("paths.zim_dir")
        self._iso3 = {v.get("iso3"): k for k, v in cfg.languages.items()}
        self._catalog: list[dict] | None = None
        self._stamp = None
        self._archives: dict[str, object] = {}
        self._lock = threading.Lock()

    # -- catalogue ---------------------------------------------------------------------------
    def catalog(self) -> list[dict]:
        files = sorted(self.dir.glob("*.zim")) if self.dir.exists() else []
        stamp = tuple((f.name, f.stat().st_mtime) for f in files)
        with self._lock:
            if self._catalog is not None and stamp == self._stamp:
                return self._catalog
            from libzim.reader import Archive

            cat = []
            for f in files:
                try:
                    a = Archive(str(f))
                    meta = {}
                    for key in ("Name", "Title", "Language", "Description", "Date", "Flavour"):
                        if key in a.metadata_keys:
                            meta[key] = a.get_metadata(key).decode("utf-8", "replace")
                    iso3 = (meta.get("Language") or "").split(",")[0].strip()
                    cat.append({"name": meta.get("Name") or f.stem, "file": f.name, "title": meta.get("Title", f.stem),
                                "lang": self._iso3.get(iso3, iso3[:2] or "en"), "iso3": iso3,
                                "date": meta.get("Date"), "size_mb": round(f.stat().st_size / 2**20),
                                "articles": a.article_count, "fulltext": a.has_fulltext_index})
                    self._archives[f.name] = a
                except Exception as exc:  # a broken/partial download must not hide the others
                    cat.append({"name": f.stem, "file": f.name, "error": str(exc), "lang": "?", "fulltext": False})
            self._catalog, self._stamp = cat, stamp
            return cat

    def _archive(self, fname: str):
        if fname not in self._archives:
            from libzim.reader import Archive
            self._archives[fname] = Archive(str(self.dir / fname))
        return self._archives[fname]

    def select(self, lang: str, subject: str) -> list[dict]:
        """Which collections to search (learner language first, then English), subject-matching first."""
        langs = [lang] + (["en"] if self.cfg["knowledge.include_english"] and lang != "en" else [])
        hints = SUBJECT_HINTS.get(subject, [])
        chosen = []
        for lg in langs:
            zims = [z for z in self.catalog() if z.get("lang") == lg and "error" not in z]
            zims.sort(key=lambda z: (not any(h in z["name"].lower() for h in hints), -z.get("size_mb", 0)))
            chosen += zims[: int(self.cfg.get("knowledge.max_zims_per_language", 3))]
        return chosen

    # -- search ------------------------------------------------------------------------------
    def _search_one(self, z: dict, query: str, k: int) -> list[str]:
        """Full-text search with progressive relaxation (Xapian matches ALL words of a query, and
        there is no stemming for most Indic languages): full query -> first two words -> each of
        the first three words -> title suggestions."""
        from libzim.suggestion import SuggestionSearcher

        a = self._archive(z["file"])
        words = list(dict.fromkeys(w for w in tokenize(query) if len(w) > 1))   # original order, deduped
        attempts = [query]
        if len(words) > 2:
            attempts.append(" ".join(words[:2]))        # learner requests usually start with the topic
        attempts += [w for w in words[:3] if w not in attempts]
        found: list[str] = []
        if z.get("fulltext"):
            from libzim.search import Query, Searcher
            for q in attempts:
                found = list(Searcher(a).search(Query().set_query(q)).getResults(0, k))
                if found:
                    return found
        for q in [query] + words[:2]:
            for path in SuggestionSearcher(a).suggest(q).getResults(0, k):
                if path not in found:
                    found.append(path)
            if len(found) >= k:
                break
        return found[:k]

    def _article(self, z: dict, path: str) -> tuple[str, list[str]] | None:
        a = self._archive(z["file"])
        entry = a.get_entry_by_path(path)
        for _ in range(5):
            if not entry.is_redirect:
                break
            entry = entry.get_redirect_entry()
        item = entry.get_item()
        if not item.mimetype.startswith("text/html"):
            return None
        paras = html_to_paragraphs(bytes(item.content).decode("utf-8", "replace"))
        return (entry.title, paras) if paras else None

    def search(self, queries: list[tuple[str, str]], learner_lang: str, subject: str = "other",
               events: EventLog | None = None) -> list[dict]:
        t0 = time.perf_counter()
        zims = self.select(learner_lang, subject)
        if not zims:
            if events:
                events.emit("kb_search", zims=[], passages=0, note="no ZIM archives installed")
            return []
        k = int(self.cfg["knowledge.max_articles"])
        hits: list[tuple[int, dict, str]] = []           # (rank, zim, path)
        for q, qlang in queries:
            for z in zims:
                if qlang != z["lang"]:          # English queries -> English ZIMs, native -> native
                    continue
                try:
                    for rank, path in enumerate(self._search_one(z, q, k)):
                        hits.append((rank, z, path))
                except Exception:
                    continue
        seen, ordered = set(), []
        for rank, z, path in sorted(hits, key=lambda h: h[0]):   # interleave queries/zims by rank
            key = (z["file"], path)
            if key not in seen:
                seen.add(key)
                ordered.append((z, path))
        t_search = time.perf_counter() - t0
        passages, words = [], int(self.cfg["knowledge.passage_words"])
        for art_rank, (z, path) in enumerate(ordered[: k]):
            try:
                art = self._article(z, path)
            except Exception:
                continue
            if not art:
                continue
            title, paras = art
            for text in split_passages(paras, words):
                passages.append({"title": title, "zim": z["name"], "path": path, "lang": z["lang"], "text": text,
                                 "_prior": 1.0 / (1 + art_rank)})   # trust Xapian's article ranking a little
        # BM25 within each language (scores are not comparable across scripts), then split the quota:
        # learner-language passages first, English ones after (often the more complete source).
        n_total = int(self.cfg["knowledge.max_passages"])
        groups: dict[str, list[dict]] = {}
        for p in passages:
            groups.setdefault(p["lang"], []).append(p)
        order = [learner_lang] + [lg for lg in groups if lg != learner_lang]
        langs = [lg for lg in order if lg in groups]
        top: list[dict] = []
        for gi, lg in enumerate(langs):
            q_tokens = [t for q, ql in queries if ql == lg for t in tokenize(q)] or \
                [t for q, _ in queries for t in tokenize(q)]
            group = groups[lg]
            for p, sc in zip(group, bm25_rank(q_tokens, [tokenize(p["title"] + " " + p["text"]) for p in group])):
                p["score"] = round(sc + p.pop("_prior", 0.0), 3)
            remaining = n_total - len(top)
            quota = remaining if gi == len(langs) - 1 else max(1, math.ceil(remaining / (len(langs) - gi)))
            top += sorted(group, key=lambda p: p["score"], reverse=True)[:quota]
        if events:
            events.emit("kb_search", zims=[z["name"] for z in zims], articles=len(ordered[:k]),
                        passages=len(top), search_s=round(t_search, 3), total_s=round(time.perf_counter() - t0, 3))
        return top
