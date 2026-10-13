"""Knowledge discovery for knowledge updates: find what the local knowledge cache is missing from the
institute's own usage, recommend archives, and refresh / add Kiwix snapshots safely.

A KDD loop over the lesson logs a GateMoE node already keeps:

    selection       every lesson.json (request, language, router subject, plan key terms, sources)
    preprocessing   per lesson: passages found, how many in the learner's language, which archives
    transformation  coverage records -> cells (language x subject)
    mining          weak cells (no learner-language passage / too few passages), frequent terms there
    interpretation  ranked recommendations: Kiwix archives that are not installed for the weak cells,
                    and newer snapshots of installed archives

    gatemoe kdd gaps [jobs_dir ...]          report + recommendations (offline)
    gatemoe kdd snapshots                    which installed archives have newer snapshots (needs internet)
    gatemoe kdd snapshots --apply [--add X]  download, verify, swap in place (no re-indexing: every ZIM
                                             carries its own full-text index; nodes pick it up on the next search)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

from ..config import Config

KIWIX = "https://download.kiwix.org/zim/"
_DATED = re.compile(r"^(?P<prefix>.+)_(?P<date>\d{4}-\d{2})\.zim$")

# candidate archives per subject (English) - verified against the live Kiwix listing before download
SUBJECT_ARCHIVES = {
    "physics": ["wikipedia_en_physics_nopic", "libretexts.org_en_phys"],
    "chemistry": ["wikipedia_en_chemistry_nopic", "libretexts.org_en_chem"],
    "biology": ["libretexts.org_en_bio", "wikipedia_en_medicine_nopic"],
    "mathematics": ["wikipedia_en_mathematics_nopic", "libretexts.org_en_math"],
    "computer_science": ["wikipedia_en_computer_nopic", "libretexts.org_en_eng"],
    "electronics": ["libretexts.org_en_eng", "wikipedia_en_physics_nopic"],
    "electrical_engineering": ["libretexts.org_en_eng", "wikipedia_en_physics_nopic"],
    "mechanical_engineering": ["libretexts.org_en_eng", "wikipedia_en_physics_nopic"],
    "civil_engineering": ["libretexts.org_en_eng"],
    "earth_science": ["libretexts.org_en_geo"],
}


def project_of(prefix: str) -> str:
    """Kiwix download folder of an archive name prefix (as scripts/download_models.sh)."""
    if prefix.startswith("libretexts.org_"):
        return "libretexts"
    if ".stackexchange.com_" in prefix or prefix.startswith("stackoverflow.com_"):
        return "stack_exchange"
    return prefix.split("_", 1)[0]


def installed_prefixes(zim_dir: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = defaultdict(list)
    for f in sorted(zim_dir.glob("*.zim")) if zim_dir.exists() else []:
        m = _DATED.match(f.name)
        out[m.group("prefix") if m else f.stem].append(f)
    return out


# ------------------------------------------------------------------------------------- mining
def records_from_lessons(roots: list[Path]) -> list[dict]:
    """Coverage records from lesson.json files under the given folders (any depth)."""
    recs = []
    for root in roots:
        for p in sorted(Path(root).rglob("lesson.json")):
            try:
                lesson = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if "sources" not in lesson:          # retrieval did not run (out of scope, knowledge off, ...)
                continue
            lang = (lesson.get("language") or {}).get("lang") or "en"
            src = lesson.get("sources") or []
            recs.append({"lang": lang, "subject": (lesson.get("route") or {}).get("subject") or "other",
                         "passages": len(src), "learner_passages": sum(1 for s in src if s.get("lang") == lang),
                         "zims": sorted({s.get("zim") for s in src if s.get("zim")}),
                         "terms": list((lesson.get("plan") or {}).get("key_terms") or []),
                         "request": lesson.get("request", "")[:200]})
    return recs


def mine(records: list[dict], min_passages: int = 2, weak_share: float = 0.3, min_n: int = 3) -> dict:
    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in records:
        cells[(r["lang"], r["subject"])].append(r)
    table, weak = [], []
    for (lang, subj), rs in sorted(cells.items()):
        def is_weak(r):
            return r["passages"] < min_passages or (lang != "en" and r["learner_passages"] == 0)
        w = [r for r in rs if is_weak(r)]
        row = {"lang": lang, "subject": subj, "n": len(rs),
               "passages_mean": round(sum(r["passages"] for r in rs) / len(rs), 2),
               "learner_language_share": round(sum(1 for r in rs if r["learner_passages"]) / len(rs), 3),
               "weak_share": round(len(w) / len(rs), 3),
               "frequent_terms_in_weak": [t for t, _ in Counter(t.lower() for r in w for t in r.get("terms", [])
                                                               ).most_common(8)]}
        table.append(row)
        if len(rs) >= min_n and row["weak_share"] >= weak_share:
            weak.append(row)
    weak.sort(key=lambda r: -(r["weak_share"] * r["n"]))
    return {"records": len(records), "cells": table, "weak_cells": weak}


def recommend(mined: dict, zim_dir: Path) -> list[dict]:
    have = set(installed_prefixes(zim_dir))
    recs: dict[str, dict] = {}
    for cell in mined["weak_cells"]:
        cands = []
        if cell["lang"] != "en":
            cands += [f"wikipedia_{cell['lang']}_all_nopic", f"wikibooks_{cell['lang']}_all_nopic"]
        cands += SUBJECT_ARCHIVES.get(cell["subject"], [])
        for c in cands:
            if c in have:
                continue
            r = recs.setdefault(c, {"archive": c, "demand": 0.0, "cells": []})
            r["demand"] = round(r["demand"] + cell["weak_share"] * cell["n"], 2)
            r["cells"].append(f"{cell['lang']}/{cell['subject']}")
    return sorted(recs.values(), key=lambda r: -r["demand"])


# ------------------------------------------------------------------------------------ updates
def _opener():
    return urllib.request.build_opener()          # honours http(s)_proxy from the environment


def latest_snapshot(prefix: str, timeout: float = 30) -> dict | None:
    """Newest dated file for ``prefix`` on download.kiwix.org with its size (HEAD), or None."""
    url = KIWIX + project_of(prefix) + "/"
    listing = _opener().open(url, timeout=timeout).read().decode("utf-8", "replace")
    files = sorted(set(re.findall(re.escape(prefix) + r"_\d{4}-\d{2}\.zim", listing)))
    if not files:
        return None
    name = files[-1]
    size = None
    try:
        req = urllib.request.Request(url + name, method="HEAD")
        with _opener().open(req, timeout=timeout) as r:
            size = int(r.headers.get("Content-Length") or 0) or None
    except Exception:
        pass
    return {"prefix": prefix, "file": name, "url": url + name, "date": _DATED.match(name).group("date"),
            "size_mb": round(size / 2**20, 1) if size else None}


def check_updates(zim_dir: Path) -> list[dict]:
    out = []
    for prefix, files in installed_prefixes(zim_dir).items():
        cur = files[-1].name
        m = _DATED.match(cur)
        try:
            lat = latest_snapshot(prefix)
        except Exception as exc:
            out.append({"prefix": prefix, "installed": cur, "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        out.append({"prefix": prefix, "installed": cur, "installed_date": m.group("date") if m else None,
                    "latest": lat["file"] if lat else None, "latest_size_mb": lat["size_mb"] if lat else None,
                    "update_available": bool(lat and m and lat["date"] > m.group("date"))})
    return out


def install_snapshot(zim_dir: Path, snap: dict, keep_old: bool = False, min_free_gb: float = 4.0) -> dict:
    """Download -> verify (opens, same Name) -> move into place -> drop the older snapshot.
    Searches keep working throughout; the catalogue picks the new file up by its mtime."""
    from libzim.reader import Archive

    zim_dir.mkdir(parents=True, exist_ok=True)
    need = (snap.get("size_mb") or 0) / 1024 + min_free_gb
    free = shutil.disk_usage(zim_dir).free / 2**30
    if free < need:
        return {"archive": snap["file"], "ok": False, "error": f"needs {need:.1f} GB free, {free:.1f} GB available"}
    dst = zim_dir / snap["file"]
    part = zim_dir / (snap["file"] + ".part")
    t = time.perf_counter()
    if shutil.which("curl"):
        res = subprocess.run(["curl", "-fL", "--retry", "5", "--retry-delay", "5", "-C", "-", "-s", "-o", str(part),
                              snap["url"]], capture_output=True, text=True)
        if res.returncode != 0:
            return {"archive": snap["file"], "ok": False, "error": f"curl rc={res.returncode} {res.stderr[-300:]}"}
    else:
        with _opener().open(snap["url"], timeout=60) as r, part.open("wb") as fh:
            shutil.copyfileobj(r, fh, 16 << 20)
    download_s = time.perf_counter() - t
    t = time.perf_counter()
    try:
        a = Archive(str(part))
        name = a.get_metadata("Name").decode("utf-8", "replace") if "Name" in a.metadata_keys else ""
        ok = a.article_count > 0 and (not name or name.startswith(snap["prefix"].split("_")[0]))
        fulltext = a.has_fulltext_index
        del a
    except Exception as exc:
        part.unlink(missing_ok=True)
        return {"archive": snap["file"], "ok": False, "error": f"verify failed: {exc}"[:300]}
    verify_s = time.perf_counter() - t
    if not ok:
        part.unlink(missing_ok=True)
        return {"archive": snap["file"], "ok": False, "error": "verify failed: empty archive or wrong Name"}
    os.replace(part, dst)
    removed = []
    if not keep_old:
        for f in installed_prefixes(zim_dir).get(snap["prefix"], []):
            if f.name != dst.name:
                f.unlink(missing_ok=True)
                removed.append(f.name)
    return {"archive": snap["file"], "ok": True, "size_mb": round(dst.stat().st_size / 2**20, 1),
            "download_s": round(download_s, 1), "verify_s": round(verify_s, 2), "fulltext_index": fulltext,
            "removed": removed}


# ---------------------------------------------------------------------------------------- CLI
def cmd_gaps(cfg: Config, roots: list[str], as_json: bool = False) -> int:
    dirs = [Path(r) for r in roots] or [cfg.path("paths.jobs_dir")]
    mined = mine(records_from_lessons(dirs))
    recs = recommend(mined, cfg.path("paths.zim_dir"))
    if as_json:
        print(json.dumps({**mined, "recommendations": recs}, ensure_ascii=False, indent=1))
        return 0
    print(f"{mined['records']} lessons with retrieval\n")
    print(f"{'language/subject':<34}{'n':>4}{'passages':>10}{'own-lang':>10}{'weak':>7}")
    for c in mined["cells"]:
        print(f"{c['lang'] + '/' + c['subject']:<34}{c['n']:>4}{c['passages_mean']:>10}"
              f"{c['learner_language_share']:>10}{c['weak_share']:>7}")
    print("\nrecommended archives (not installed), by demand:")
    for r in recs or [{"archive": "(none - coverage looks fine)", "demand": "", "cells": []}]:
        print(f"  {r['archive']:<40} demand {r['demand']}  {', '.join(r['cells'])}")
    print("\nAdd one with:  gatemoe kdd snapshots --apply --add <archive>")
    return 0


def cmd_update(cfg: Config, apply: bool = False, add: list[str] | None = None) -> int:
    zim_dir = cfg.path("paths.zim_dir")
    rows = check_updates(zim_dir)
    for r in rows:
        flag = "UPDATE" if r.get("update_available") else ("error " if r.get("error") else "ok    ")
        print(f"{flag} {r['prefix']:<40} installed {r.get('installed_date')}  latest {r.get('latest')} "
              f"({r.get('latest_size_mb')} MB) {r.get('error') or ''}")
    if not apply:
        return 0
    todo = [latest_snapshot(r["prefix"]) for r in rows if r.get("update_available")]
    for name in add or []:
        snap = latest_snapshot(name)
        if not snap:
            print(f"not found on Kiwix: {name}")
            continue
        todo.append(snap)
    for snap in todo:
        if snap:
            print(json.dumps(install_snapshot(zim_dir, snap)))
    return 0
