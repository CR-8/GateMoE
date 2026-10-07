"""Concept maps (Graphviz) and PhET simulations (ZIM) - model-free."""
import json
import re
import shutil

import pytest
from fastapi.testclient import TestClient

from gatemoe.gateways.conceptmap import ConceptMapGateway, build_dot, dot_string, normalise
from gatemoe.knowledge import KnowledgeBase
from gatemoe.knowledge.phet import SimulationFinder, parse_catalog, score_titles

EDGES = [{"from": "Voltage", "label": "drives", "to": "Current"},
         {"from": "current", "label": "is limited by", "to": "Resistance"},      # same concept, other case
         {"from": "Resistance", "label": "", "to": "resistance"},              # self-loop -> dropped
         {"from": "Voltage", "label": "drives", "to": "Current"}]              # duplicate -> dropped


def test_concept_map_normalise_and_escape():
    nodes, links = normalise(EDGES)
    assert nodes == ["Voltage", "Current", "Resistance"] and links == [(0, 1, "drives"), (1, 2, "is limited by")]
    s = dot_string('say "hi" \\G\u202e\x07')
    assert s == '"say \\"hi\\" /G"'                      # quotes escaped, no DOT escapes, no bidi/control chars
    dot = build_dot(EDGES + [{"from": 'x"]; n0 -> n9 [label="', "label": "}", "to": "y"}], font="Noto Sans")
    edges = [ln for ln in dot.splitlines() if re.match(r"\s+n\d+ -> n\d+", ln)]
    assert len(edges) == 3 and not any(ln.lstrip().startswith("n9") for ln in dot.splitlines())
    assert 'label="x\\"]; n0 -> n9\\n[label=\\""' in dot          # injected text stays inside one quoted label
    with pytest.raises(ValueError):
        build_dot(EDGES[2:3])


@pytest.mark.skipif(not shutil.which("dot"), reason="graphviz not installed")
def test_concept_map_renders(cfg, tmp_path):
    out = ConceptMapGateway(cfg).render(EDGES, "kn", tmp_path, title="Ohm")
    svg = (tmp_path / out["svg"]).read_text()
    assert "<svg" in svg and "<script" not in svg and out["edges"] == 2


CATALOG = {"languageMappings": {"en": "English"}, "simsByLanguage": {"en": [
    {"id": "ohms-law", "language": "en", "title": "\u202aOhm's Law\u202c", "categories": [{"slug": "physics"}]},
    {"id": "keplers-laws", "language": "en", "title": "Kepler's Laws", "categories": [{"slug": "physics"}]},
    {"id": "coulombs-law", "language": "en", "title": "Coulomb's Law", "categories": [{"slug": "physics"}]},
    {"id": "hookes-law", "language": "en", "title": "Hooke's Law", "categories": [{"slug": "physics"}]},
    {"id": "fractions-intro", "language": "en", "title": "Fractions: Intro", "categories": [{"slug": "math"}]},
    {"id": "../evil", "language": "en", "title": "Evil", "categories": []}]}}


def test_phet_catalog_and_scoring():
    sims = parse_catalog("window.importedData = " + json.dumps(CATALOG) + ";")
    assert [s["id"] for s in sims] == ["ohms-law", "keplers-laws", "coulombs-law", "hookes-law", "fractions-intro"]
    assert sims[0]["title"] == "Ohm's Law"                           # bidi embedding marks stripped
    sc = score_titles(sims, {"ohm", "law", "explain"})
    assert sc["ohms-law"] == 1.0 and sc["keplers-laws"] < 0.5     # "law" alone is not enough
    assert score_titles(sims, {"fraction"})["fractions-intro"] == 1.0   # "Intro" is a stop word


def _make_phet_zim(path):
    from libzim.writer import Creator, Hint, Item, StringProvider

    class It(Item):
        def __init__(self, p, mime, content, title=""):
            super().__init__()
            self.p, self.mime, self.content, self.t = p, mime, content, title

        def get_path(self):
            return self.p

        def get_title(self):
            return self.t

        def get_mimetype(self):
            return self.mime

        def get_contentprovider(self):
            return StringProvider(self.content)

        def get_hints(self):
            return {Hint.FRONT_ARTICLE: self.mime == "text/html"}

    with Creator(str(path)).config_indexing(False, "eng") as c:
        c.set_mainpath("index.html")
        for k, v in {"Name": "phet_en_all", "Language": "eng", "Title": "PhET", "Creator": "t", "Publisher": "t",
                     "Date": "2026-08-01", "Description": "sims"}.items():
            c.add_metadata(k, v)
        c.add_item(It("index.html", "text/html", "<html>index</html>", "index"))
        c.add_item(It("catalog.js", "application/javascript", "window.importedData = " + json.dumps(CATALOG)))
        for s in CATALOG["simsByLanguage"]["en"][:5]:
            c.add_item(It(f"{s['id']}_en.html", "text/html", f"<script src='a.js'></script>{s['id']}", s["title"]))
        c.add_item(It("ohms-law.png", "image/png", "PNG"))
        c.add_item(It("a.js", "application/javascript", "console.log(1)"))


def test_phet_finder_and_sandboxed_route(cfg, tmp_path):
    zim_dir = cfg.path("paths.zim_dir")
    zim_dir.mkdir(parents=True, exist_ok=True)
    _make_phet_zim(zim_dir / "phet_en_all_2026-08.zim")
    kb = KnowledgeBase(cfg)
    assert kb.select("en", "physics") == []                       # sims are not text sources
    sims = SimulationFinder(cfg, kb).find([("Ohm's law", "en"), ("resistance", "en")], "en", "physics")
    assert [s["id"] for s in sims] == ["ohms-law"] and sims[0]["thumbnail"] == "ohms-law.png"
    assert sims[0]["path"] == "ohms-law_en.html" and sims[0]["zim"] == "phet_en_all"

    from gatemoe.jobs import JobManager
    from gatemoe.server.app import create_app
    client = TestClient(create_app(cfg, JobManager(cfg, lambda: None)))
    r = client.get("/zim/phet_en_all/ohms-law_en.html")
    assert r.status_code == 200 and "ohms-law" in r.text
    csp = r.headers["content-security-policy"]
    assert csp.startswith("sandbox allow-scripts;") and "allow-same-origin" not in csp and "connect-src 'none'" in csp
    assert client.get("/zim/phet_en_all/a.js").headers["content-type"].startswith("application/javascript")
    assert client.get("/zim/phet_en_all/").status_code == 200    # main page
    for bad in ("/zim/nope/x", "/zim/phet_en_all/missing.html", "/zim/phet_en_all/../../etc/passwd"):
        assert client.get(bad).status_code == 404
