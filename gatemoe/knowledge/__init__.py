"""KNOWLEDGE GATEWAY: offline retrieval over Kiwix ZIM archives (a local piece of the internet).

    queries (English + learner language)
      -> pick ZIMs by language (+ English) and subject     (second-level routing: which collections)
      -> Xapian full-text search inside each ZIM (title search when a ZIM has no full-text index)
      -> fetch top articles, HTML -> clean text, ~200-word passages
      -> BM25 rerank across all passages -> top-k with citations (zim name + entry path)
No index is built on the Pi: every Kiwix ZIM ships its own full-text index.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
import unicodedata
from collections import Counter

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


def zim_kind(name: str) -> str:
    """'simulations' for PhET archives (interactive apps, not text to retrieve), else 'text'."""
    return "simulations" if name.lower().startswith("phet") else "text"


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
                    name = meta.get("Name") or f.stem
                    cat.append({"name": name, "file": f.name, "title": meta.get("Title", f.stem),
                                "kind": zim_kind(name),
                                "lang": self._iso3.get(iso3, iso3[:2] or "en"), "iso3": iso3,
                                "date": meta.get("Date"), "size_mb": round(f.stat().st_size / 2**20),
                                "articles": a.article_count, "fulltext": a.has_fulltext_index})
                    self._archives[f.name] = a
                except Exception as exc:  # a broken/partial download must not hide the others
                    cat.append({"name": f.stem, "file": f.name, "error": str(exc), "lang": "?", "fulltext": False,
                                "kind": zim_kind(f.stem)})
            self._catalog, self._stamp = cat, stamp
            return cat

    def _archive(self, fname: str):
        if fname not in self._archives:
            from libzim.reader import Archive
            self._archives[fname] = Archive(str(self.dir / fname))
        return self._archives[fname]

    def by_name(self, name: str) -> dict | None:
        """Catalogue entry for a ZIM ``Name`` (the newest file wins when several dates are installed)."""
        hits = [z for z in self.catalog() if z.get("name") == name and "error" not in z]
        return hits[-1] if hits else None

    def read_entry(self, z: dict, path: str, max_redirects: int = 5) -> tuple[bytes, str, str]:
        """(content, mimetype, final path) of one ZIM entry; KeyError if it does not exist."""
        a = self._archive(z["file"])
        if not path or not a.has_entry_by_path(path):
            raise KeyError(path)
        entry = a.get_entry_by_path(path)
        for _ in range(max_redirects):
            if not entry.is_redirect:
                break
            entry = entry.get_redirect_entry()
        if entry.is_redirect:
            raise KeyError(path)
        item = entry.get_item()
        return bytes(item.content), item.mimetype, entry.path

    def select(self, lang: str, subject: str) -> list[dict]:
        """Which collections to search (learner language first, then English), subject-matching first."""
        langs = [lang] + (["en"] if self.cfg["knowledge.include_english"] and lang != "en" else [])
        hints = SUBJECT_HINTS.get(subject, [])
        all_hints = {h for hs in SUBJECT_HINTS.values() for h in hs}

        def fits(name: str) -> bool:   # general archives always; topical ones only for their subject
            name = name.lower()
            return not hints or any(h in name for h in hints) or not any(h in name for h in all_hints)

        chosen = []
        for lg in langs:
            zims = [z for z in self.catalog()
                    if z.get("lang") == lg and "error" not in z and z.get("kind") == "text" and fits(z["name"])]
            zims.sort(key=lambda z: (not any(h in z["name"].lower() for h in hints), -z.get("size_mb", 0)))
            chosen += zims[: int(self.cfg.get("knowledge.max_zims_per_language", 3))]
        return chosen

    # -- search ------------------------------------------------------------------------------
    def _search_one(self, z: dict, query: str, k: int, relax: bool = True) -> list[str]:
        """Full-text search; with ``relax`` progressively looser (Xapian matches ALL words of a query,
        and there is no stemming for most Indic languages): full query -> first two words -> each of
        the first three words -> title suggestions."""
        from libzim.suggestion import SuggestionSearcher

        a = self._archive(z["file"])
        words = list(dict.fromkeys(w for w in tokenize(query) if len(w) > 1))   # original order, deduped
        attempts = [query]
        if relax:
            if len(words) > 2:
                attempts.append(" ".join(words[:2]))    # learner requests usually start with the topic
            attempts += [w for w in words[:3] if w not in attempts]
        found: list[str] = []
        if z.get("fulltext"):
            from libzim.search import Query, Searcher
            for q in attempts:
                found = list(Searcher(a).search(Query().set_query(q)).getResults(0, k))
                if found:
                    return found
        for q in [query] + (words[:2] if relax else []):
            for path in SuggestionSearcher(a).suggest(q).getResults(0, k):
                if path not in found:
                    found.append(path)
            if len(found) >= k:
                break
        return found[:k]

    def _resolve(self, z: dict, path: str) -> str | None:
        """Final path of an entry after following redirects (None if missing or a redirect loop)."""
        try:
            entry = self._archive(z["file"]).get_entry_by_path(path)
        except KeyError:
            return None
        for _ in range(5):
            if not entry.is_redirect:
                return entry.path
            entry = entry.get_redirect_entry()
        return None

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
        html = bytes(item.content).decode("utf-8", "replace")
        # openZIM MindTouch scraper (LibreTexts): "index/page_N" is a meta-refresh stub for a
        # single-page app; the text is "content/page_content_N.json" -> {"htmlBody": ...}
        m = re.fullmatch(r"index/page_(\d+)", entry.path)
        if m and len(html) < 2000:
            a = self._archive(z["file"])
            side = f"content/page_content_{m.group(1)}.json"
            if a.has_entry_by_path(side):
                try:
                    html = json.loads(bytes(a.get_entry_by_path(side).get_item().content)).get("htmlBody") or ""
                except (ValueError, AttributeError):
                    html = ""
        paras = html_to_paragraphs(html)
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
            targets = [z for z in zims if z["lang"] == qlang]   # English queries -> English ZIMs, native -> native
            # exact query in every archive first; loosen it only if no archive matched it at all
            # (relaxing per archive turns "Ohm's law examples" into "examples" in a computing ZIM)
            for relax in (False, True):
                found = 0
                for z in targets:
                    try:
                        for rank, path in enumerate(self._search_one(z, q, k, relax=relax)):
                            hits.append((rank, z, path))
                            found += 1
                    except Exception:
                        continue
                if found:
                    break
        # Reciprocal-rank fusion over queries and archives, on the resolved article (redirects such as
        # "Ohm" -> "Ohm's_law" merge): an article several queries agree on outranks a one-off top hit.
        fused: dict[tuple[str, str], float] = {}
        where: dict[tuple[str, str], dict] = {}
        for rank, z, path in hits:
            final = self._resolve(z, path)
            if final is None:
                continue
            key = (z["file"], final)
            fused[key] = fused.get(key, 0.0) + 1.0 / (1 + rank)
            where[key] = z
        by_lang: dict[str, list[tuple[float, dict, str]]] = {}
        for key, score in sorted(fused.items(), key=lambda kv: -kv[1]):
            z = where[key]
            by_lang.setdefault(z["lang"], []).append((score, z, key[1]))
        # every language gets its own share of the article budget: English queries are more numerous
        # and would otherwise fill all k slots before one learner-language article is fetched
        langs = list(dict.fromkeys(lg for lg in [learner_lang] + sorted(by_lang) if lg in by_lang))
        picked: list[tuple[float, dict, str]] = []
        for gi, lg in enumerate(langs):
            remaining = k - len(picked)
            quota = remaining if gi == len(langs) - 1 else max(1, math.ceil(remaining / (len(langs) - gi)))
            picked += by_lang[lg][:quota]
        if len(picked) < k:                                # hand unused slots back, best first
            have = {(z["file"], path) for _, z, path in picked}
            spare = sorted((t for lg in langs for t in by_lang[lg] if (t[1]["file"], t[2]) not in have),
                           key=lambda t: -t[0])
            picked += spare[: k - len(picked)]
        top_fused = {lg: by_lang[lg][0][0] for lg in langs}
        t_search = time.perf_counter() - t0
        passages, words = [], int(self.cfg["knowledge.passage_words"])
        for score, z, path in picked:
            try:
                art = self._article(z, path)
            except Exception:
                continue
            if not art:
                continue
            title, paras = art
            for text in split_passages(paras, words):   # fused article score as a prior (0..1]
                passages.append({"title": title, "zim": z["name"], "path": path, "lang": z["lang"], "text": text,
                                 "_prior": score / top_fused[z["lang"]]})
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
            events.emit("kb_search", zims=[z["name"] for z in zims], articles=len(picked),
                        passages=len(top), search_s=round(t_search, 3), total_s=round(time.perf_counter() - t0, 3))
        return top
