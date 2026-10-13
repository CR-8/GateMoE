"""KDD delta layer: new knowledge between Kiwix snapshots, compiled into a small searchable ZIM.

The Kiwix archives are the *snapshot* layer: large, stable, refreshed only when Kiwix publishes a
newer snapshot (kdd.py: check_updates / install_snapshot). New research and the institute's own
material flow into the *delta* layer through a KDD pipeline:

    selection        arXiv (newest papers per category; CC0 metadata + abstracts) and an inbox folder
                     where staff drop papers, lab manuals and notes (.pdf .html .md .txt)
    preprocessing    text extraction, whitespace/LaTeX clean-up, length filter, language id,
                     exact (hash) and near-duplicate (5-word shingles) removal - within the batch and
                     against what the delta already holds
    transformation   subject (arXiv category or keyword vote over the router's subjects),
                     keyphrases, ~200-word chunks
    mining           novelty against the snapshot: each chunk's keyphrases are searched in the
                     snapshot archives; novelty = 1 - containment of the chunk's words in the best
                     snapshot passage. Chunks the snapshot already covers are rejected.
                     Ranking: novelty x demand (weak language/subject cells mined from lesson logs)
    interpretation   accepted documents -> data/kdd/store/<lang>.jsonl (provenance, licence, scores)
    deployment       `build` compiles gatemoe_delta_<lang>_<YYYY-MM>.zim (full-text indexed) into the
                     ZIM folder; KnowledgeBase always searches it next to the snapshots, picks it up
                     on its next search (no restart), and `prune` drops delta documents a newer
                     snapshot has absorbed.
"""
from __future__ import annotations

import hashlib
import html as htmlmod
import json
import os
import re
import shutil
import struct
import subprocess
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import Config
from . import KnowledgeBase, is_delta, tokenize
from .htmltext import html_to_paragraphs

ARXIV_API = "http://export.arxiv.org/api/query"
ARXIV_SUBJECT = [("cs.", "computer_science"), ("eess.SP", "electronics"), ("eess.SY", "electrical_engineering"),
                 ("eess.", "electrical_engineering"), ("physics.geo-ph", "earth_science"), ("physics.ao-ph", "earth_science"),
                 ("astro-ph.EP", "earth_science"), ("physics.chem-ph", "chemistry"), ("physics.bio-ph", "biology"),
                 ("physics.flu-dyn", "mechanical_engineering"), ("physics.", "physics"), ("cond-mat", "physics"),
                 ("quant-ph", "physics"), ("hep-", "physics"), ("astro-ph", "physics"), ("q-bio", "biology"),
                 ("math", "mathematics"), ("stat", "mathematics")]
SUBJECT_WORDS = {
    "physics": "force energy momentum quantum particle wave field relativity thermodynamics optics photon",
    "chemistry": "molecule reaction acid base bond catalyst compound organic oxidation electrolyte",
    "biology": "cell protein gene dna enzyme organism species tissue immune neuron",
    "mathematics": "theorem proof equation matrix integral derivative probability graph algebra topology",
    "computer_science": "algorithm software network learning neural model data program compiler database",
    "electronics": "transistor circuit amplifier semiconductor diode signal sensor chip voltage",
    "electrical_engineering": "power grid motor generator voltage current inverter transformer control",
    "mechanical_engineering": "engine turbine stress strain fluid heat machine gear vibration robot",
    "civil_engineering": "concrete beam bridge soil structure load foundation construction steel",
    "earth_science": "earthquake climate ocean atmosphere rock volcano soil glacier weather",
}
STOP = set("""a an the and or of to in on for with by from as at is are was were be been this that these those it its
we our you your they their can may also which such using use used based new paper study results show propose
proposed approach method methods into than between over under via while both however here there not no more most""".split())
_SHINGLE = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(text: str) -> str:
    text = re.sub(r"\$[^$]{0,200}\$", " ", text)            # inline LaTeX in arXiv abstracts
    text = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _content_tokens(text: str) -> list[str]:
    return [t for t in tokenize(text) if t not in STOP and not t.isdigit() and len(t) > 2]


def keyphrases(title: str, text: str, k: int = 6) -> list[str]:
    toks = _content_tokens(text)
    c = Counter(toks)
    for t in _content_tokens(title):
        c[t] += 3                                            # title words matter most
    bi = Counter(f"{a} {b}" for a, b in zip(toks, toks[1:]))
    out = [b for b, n in bi.most_common(3) if n > 1]
    out += [w for w, _ in c.most_common(k) if not any(w in b for b in out)]
    return out[:k]


def classify_subject(doc: dict) -> str:
    cat = doc.get("category") or ""
    for prefix, subj in ARXIV_SUBJECT:
        if cat.startswith(prefix):
            return subj
    toks = Counter(_content_tokens(doc.get("title", "") + " " + doc.get("text", "")))
    votes = {s: sum(toks[w] for w in ws.split()) for s, ws in SUBJECT_WORDS.items()}
    best = max(votes, key=votes.get)
    return best if votes[best] > 0 else "other"


def shingles(text: str) -> set[int]:
    toks = _content_tokens(text)
    return {int(hashlib.md5(" ".join(toks[i:i + _SHINGLE]).encode()).hexdigest()[:12], 16)   # stable across runs
            for i in range(max(0, len(toks) - _SHINGLE + 1))}


def chunks(text: str, words: int = 200) -> list[str]:
    ws = text.split()
    if len(ws) <= words * 1.5:
        return [text]
    return [" ".join(ws[i:i + words]) for i in range(0, len(ws), words)]


# ------------------------------------------------------------------------------- selection
def fetch_arxiv(categories: list[str], per_category: int = 25, timeout: float = 60, pause_s: float = 3.0,
                log=print) -> list[dict]:
    """Newest submissions per arXiv category (Atom API; arXiv asks for <= 1 request / 3 s)."""
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    docs = []
    opener = urllib.request.build_opener()
    for i, cat in enumerate(categories):
        if i:
            time.sleep(pause_s)
        q = urllib.parse.urlencode({"search_query": f"cat:{cat}", "sortBy": "submittedDate", "sortOrder": "descending",
                                    "max_results": per_category})
        try:
            raw = opener.open(f"{ARXIV_API}?{q}", timeout=timeout).read()
            root = ET.fromstring(raw)
        except Exception as exc:
            log(f"arXiv {cat}: {type(exc).__name__}: {exc}")
            continue
        for e in root.findall("a:entry", ns):
            aid = (e.findtext("a:id", "", ns) or "").rsplit("/abs/", 1)[-1]
            prim = e.find("arxiv:primary_category", ns)
            docs.append({"source": f"arxiv:{cat}", "id": "arxiv-" + re.sub(r"[^A-Za-z0-9.]+", "-", aid),
                         "title": _norm(e.findtext("a:title", "", ns)), "text": _norm(e.findtext("a:summary", "", ns)),
                         "url": f"https://arxiv.org/abs/{aid}", "published": e.findtext("a:published", "", ns),
                         "authors": [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)][:6],
                         "category": prim.get("term") if prim is not None else cat,
                         "license": "arXiv metadata and abstract, CC0 1.0"})
    return docs


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages[:60])
    except ImportError:
        pass
    except Exception:
        return ""
    if shutil.which("pdftotext"):
        res = subprocess.run(["pdftotext", "-l", "60", str(path), "-"], capture_output=True, text=True, timeout=120)
        return res.stdout if res.returncode == 0 else ""
    return ""


def read_inbox(inbox: Path) -> list[dict]:
    """Documents staff dropped in the inbox folder: .pdf .html .htm .md .txt (subfolders too)."""
    docs = []
    for f in sorted(inbox.rglob("*")) if inbox.exists() else []:
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        try:
            if ext == ".pdf":
                text = _pdf_text(f)
            elif ext in (".html", ".htm"):
                text = "\n".join(html_to_paragraphs(f.read_text(encoding="utf-8", errors="replace"), 400))
            elif ext in (".md", ".txt"):
                text = f.read_text(encoding="utf-8", errors="replace")
            else:
                continue
        except OSError:
            continue
        lines = [ln.strip("# ").strip() for ln in text.splitlines() if ln.strip()]
        title = lines[0][:150] if lines and len(lines[0]) < 150 else f.stem.replace("_", " ")
        docs.append({"source": "inbox", "id": "inbox-" + hashlib.sha1(str(f.relative_to(inbox)).encode()).hexdigest()[:12],
                     "title": title, "text": _norm(text), "url": f"inbox/{f.relative_to(inbox)}",
                     "published": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat(),
                     "license": "institute material (local use)", "file": str(f)})
    return docs


# --------------------------------------------------------------------------- the KDD pipeline
class DeltaKDD:
    def __init__(self, cfg: Config, kb: KnowledgeBase | None = None, log=print):
        self.cfg = cfg
        self.kb = kb or KnowledgeBase(cfg)
        self.log = log
        self.root = cfg.path("kdd.store_dir") if cfg.get("kdd.store_dir") else cfg.path("paths.data_dir") / "kdd"
        self.store_dir = self.root / "store"
        self.runs_dir = self.root / "runs"
        self.zim_dir = cfg.path("paths.zim_dir")

    # -- store --------------------------------------------------------------------------------
    def store(self, lang: str) -> list[dict]:
        p = self.store_dir / f"{lang}.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _write_store(self, lang: str, docs: list[dict]) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        p = self.store_dir / f"{lang}.jsonl"
        tmp = p.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in docs), encoding="utf-8")
        tmp.replace(p)

    def langs(self) -> list[str]:
        return sorted(p.stem for p in self.store_dir.glob("*.jsonl")) if self.store_dir.exists() else []

    # -- mining: novelty against the snapshot layer ---------------------------------------------
    def snapshot_novelty(self, title: str, text: str, lang: str, subject: str) -> tuple[float, dict]:
        """1 - max containment of a chunk's words in the best snapshot passage (delta excluded),
        averaged over the document's first chunks."""
        tree = self.cfg.tree.setdefault("knowledge", {})
        was = tree.get("delta", True)
        tree["delta"] = False                                    # compare with the snapshots only
        try:
            scores, best = [], {}
            for ch in chunks(text)[:6]:
                kp = keyphrases(title, ch, 6)
                queries = [(title, lang), (" ".join(kp[:4]), lang)]
                if lang != "en" and self.cfg.get("knowledge.include_english", True):
                    queries.append((" ".join(kp[:4]), "en"))
                try:
                    passages = self.kb.search(queries, learner_lang=lang, subject=subject)
                except Exception:
                    passages = []
                a = set(_content_tokens(ch))
                top = 0.0
                for p in passages:
                    b = set(_content_tokens(p["title"] + " " + p["text"]))
                    c = len(a & b) / len(a) if a else 0.0
                    if c > top:
                        top, best = c, {"title": p["title"], "zim": p["zim"], "containment": round(c, 3)}
                scores.append(1.0 - top)
            return (round(sum(scores) / len(scores), 4) if scores else 1.0), best
        finally:
            tree["delta"] = was

    # -- one run --------------------------------------------------------------------------------
    def ingest(self, docs: list[dict], demand: dict[str, float] | None = None, dry_run: bool = False) -> dict:
        """Preprocess, transform and mine ``docs``; accepted ones are appended to the store."""
        from ..langid import detect_language

        t0 = time.perf_counter()
        min_words = int(self.cfg.get("kdd.min_words", 40))
        novelty_min = float(self.cfg.get("kdd.novelty_min", 0.5))
        dup_jaccard = float(self.cfg.get("kdd.near_duplicate_jaccard", 0.8))
        existing = {lg: self.store(lg) for lg in self.langs()}
        seen_hash = {d["hash"] for ds in existing.values() for d in ds}
        seen_sh = [(d["id"], set(d.get("shingles") or [])) for ds in existing.values() for d in ds]
        decisions, accepted = [], []
        for d in docs:
            text = _norm(d.get("text", ""))
            rec = {"id": d["id"], "source": d.get("source"), "title": d.get("title", "")[:200]}
            if len(text.split()) < min_words:
                decisions.append({**rec, "decision": "rejected", "reason": "too short"})
                continue
            h = hashlib.sha1(text.lower().encode()).hexdigest()
            if h in seen_hash:
                decisions.append({**rec, "decision": "rejected", "reason": "exact duplicate"})
                continue
            sh = shingles(text)
            near = next((i for i, s in seen_sh if sh and s and len(sh & s) / len(sh | s) >= dup_jaccard), None)
            if near:
                decisions.append({**rec, "decision": "rejected", "reason": f"near duplicate of {near}"})
                continue
            lang = d.get("lang") or detect_language(d.get("title", "") + " " + text[:500],
                                                    list(self.cfg.languages))["lang"]
            subject = d.get("subject") or classify_subject({**d, "text": text})
            t = time.perf_counter()
            novelty, best = self.snapshot_novelty(d.get("title", ""), text, lang, subject)
            rec.update(lang=lang, subject=subject, novelty=novelty, best_snapshot_match=best,
                       novelty_s=round(time.perf_counter() - t, 3))
            if novelty < novelty_min:
                decisions.append({**rec, "decision": "rejected", "reason": "covered by snapshot"})
                continue
            score = round(novelty * (1.0 + (demand or {}).get(f"{lang}/{subject}", 0.0)), 4)
            doc = {**{k: d.get(k) for k in ("id", "source", "title", "url", "published", "authors", "category",
                                            "license")},
                   "text": text, "lang": lang, "subject": subject, "keyphrases": keyphrases(d.get("title", ""), text),
                   "novelty": novelty, "score": score, "hash": h, "shingles": sorted(sh)[:400],
                   "added": _now().isoformat(timespec="seconds")}
            accepted.append(doc)
            seen_hash.add(h)
            seen_sh.append((doc["id"], sh))
            decisions.append({**rec, "decision": "accepted", "score": score})
        if not dry_run:
            by_lang: dict[str, list[dict]] = {}
            for doc in accepted:
                by_lang.setdefault(doc["lang"], []).append(doc)
            for lg, new in by_lang.items():
                self._write_store(lg, existing.get(lg, []) + new)
        report = {"time": _now().isoformat(timespec="seconds"), "candidates": len(docs), "accepted": len(accepted),
                  "rejected": Counter(x.get("reason") for x in decisions if x["decision"] == "rejected"),
                  "seconds": round(time.perf_counter() - t0, 2), "decisions": decisions}
        if not dry_run:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            (self.runs_dir / f"ingest-{int(time.time())}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return report

    # -- deployment: compile the delta ZIM ----------------------------------------------------
    def build(self) -> list[dict]:
        out = []
        max_age = int(self.cfg.get("kdd.max_age_days", 365))
        cutoff = _now() - timedelta(days=max_age)
        for lang in self.langs():
            docs = [d for d in self.store(lang) if datetime.fromisoformat(d["added"]) >= cutoff]
            self._write_store(lang, docs)
            out.append(self._build_zim(lang, docs))
        return out

    def _build_zim(self, lang: str, docs: list[dict]) -> dict:
        from libzim.writer import Creator, Hint, Item, StringProvider

        iso3 = (self.cfg.languages.get(lang) or {}).get("iso3", lang)
        name = f"gatemoe_delta_{lang}"
        final = self.zim_dir / f"{name}_{_now():%Y-%m}.zim"
        old = [f for f in self.zim_dir.glob(f"{name}_*.zim") if f != final]
        if not docs:
            for f in old + [final]:
                f.unlink(missing_ok=True)
            return {"lang": lang, "docs": 0, "archive": None}
        self.zim_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.zim_dir / f".{name}.building"
        tmp.unlink(missing_ok=True)

        class Doc(Item):
            def __init__(self, path, title, body):
                super().__init__()
                self.path, self.title, self.body = path, title, body

            def get_path(self):
                return self.path

            def get_title(self):
                return self.title

            def get_mimetype(self):
                return "text/html"

            def get_contentprovider(self):
                return StringProvider(self.body)

            def get_hints(self):
                return {Hint.FRONT_ARTICLE: True}

        esc = htmlmod.escape
        t = time.perf_counter()
        creator = Creator(str(tmp)).config_indexing(True, iso3)
        with creator as c:
            c.set_mainpath("index")
            for k, v in {"Name": name, "Title": f"GateMoE new knowledge ({lang})", "Language": iso3,
                         "Description": "New research and institute material added between Kiwix snapshots",
                         "Creator": "GateMoE KDD", "Publisher": "GateMoE", "Date": f"{_now():%Y-%m-%d}",
                         "Tags": "_category:gatemoe;_ftindex:yes;_pictures:no", "Flavour": "delta"}.items():
                c.add_metadata(k, v)
            c.add_illustration(48, _png48())
            rows = []
            for d in sorted(docs, key=lambda d: -d.get("score", 0)):
                path = f"doc/{d['id']}"
                meta = " · ".join(x for x in [d.get("source", ""), (d.get("published") or "")[:10],
                                              ", ".join(d.get("authors") or [])[:200]] if x)
                paras = "".join(f"<p>{esc(p)}</p>" for p in re.split(r"\n{1,}", d["text"]) if p.strip()) \
                    if "\n" in d["text"] else "".join(f"<p>{esc(ch)}</p>" for ch in chunks(d["text"], 120))
                body = (f"<html><head><meta charset=utf-8><title>{esc(d['title'])}</title></head><body>"
                        f"<h1>{esc(d['title'])}</h1><p>{esc(meta)}. Source: {esc(d.get('url') or '')}. "
                        f"Licence: {esc(d.get('license') or '')}.</p>{paras}</body></html>")
                c.add_item(Doc(path, d["title"], body))
                rows.append(f"<li><a href='{esc(path)}'>{esc(d['title'])}</a> ({esc(d.get('source', ''))})</li>")
            c.add_item(Doc("index", f"GateMoE new knowledge ({lang})",
                           f"<html><head><meta charset=utf-8><title>New knowledge</title></head><body><h1>New "
                           f"knowledge ({len(docs)} documents)</h1><ul>{''.join(rows)}</ul></body></html>"))
        os.replace(tmp, final)                                  # atomic: searches never see a half archive
        for f in old:
            f.unlink(missing_ok=True)
        return {"lang": lang, "docs": len(docs), "archive": final.name, "size_mb": round(final.stat().st_size / 2**20, 2),
                "build_s": round(time.perf_counter() - t, 2)}

    # -- prune: what a newer snapshot now covers leaves the delta -------------------------------
    def prune(self) -> dict:
        novelty_min = float(self.cfg.get("kdd.novelty_min", 0.5))
        out = {}
        for lang in self.langs():
            keep, drop = [], []
            for d in self.store(lang):
                n, _ = self.snapshot_novelty(d["title"], d["text"], lang, d.get("subject", "other"))
                (keep if n >= novelty_min else drop).append({**d, "novelty": n})
            self._write_store(lang, keep)
            out[lang] = {"kept": len(keep), "dropped": [d["id"] for d in drop]}
        return out

    def status(self) -> dict:
        return {"store": {lg: len(self.store(lg)) for lg in self.langs()},
                "delta_archives": sorted(p.name for p in self.zim_dir.glob("gatemoe_delta_*.zim")),
                "snapshot_archives": sorted(p.name for p in self.zim_dir.glob("*.zim") if not is_delta(p.name)),
                "runs": len(list(self.runs_dir.glob("*.json"))) if self.runs_dir.exists() else 0}


def _png48() -> bytes:
    """A plain 48x48 PNG (Kiwix tools expect an illustration in every archive)."""
    raw = b"".join(b"\x00" + bytes((40, 90, 160)) * 48 for _ in range(48))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 48, 48, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
