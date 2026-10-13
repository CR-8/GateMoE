"""KDD knowledge updates: usage-gap mining, novelty filter against a snapshot, delta ZIM build + search."""
import json

from gatemoe.knowledge import KnowledgeBase
from gatemoe.knowledge import kdd
from gatemoe.knowledge.delta import DeltaKDD, keyphrases, read_inbox

SNAPSHOT_ARTICLES = {
    "Ohm's law": "Ohm's law states that the current through a conductor between two points is directly proportional "
                 "to the voltage across the two points. Introducing the constant of proportionality, the resistance, "
                 "one arrives at the usual mathematical equation that describes this relationship. The law was named "
                 "after the German physicist Georg Ohm, who described measurements of applied voltage and current "
                 "through simple electrical circuits containing various lengths of wire.",
    "Photosynthesis": "Photosynthesis is a biological process used by many cellular organisms to convert light energy "
                      "into chemical energy, which is stored in organic compounds that can later be metabolized "
                      "through cellular respiration to fuel the organism's activities. Most plants, algae and "
                      "cyanobacteria perform photosynthesis; such organisms are called photoautotrophs.",
}
NEW_PAPER = ("Tactile transformers for dexterous robotic grasping", (
    "We present a tactile transformer that fuses fingertip pressure arrays with wrist camera tokens to predict "
    "grasp stability for a low-cost three-finger gripper. Trained on forty thousand self-supervised grasp trials "
    "collected overnight, the policy recovers from slip events within eighty milliseconds and transfers to "
    "deformable household objects without fine-tuning. Ablations show that tactile tokens matter most for "
    "transparent and reflective items where the camera alone fails."))


def make_snapshot(path, articles, lang="eng", name="wikipedia_en_test_nopic"):
    from libzim.writer import Creator, Hint, Item, StringProvider

    class A(Item):
        def __init__(self, p, t, b):
            super().__init__()
            self.p, self.t, self.b = p, t, b

        def get_path(self):
            return self.p

        def get_title(self):
            return self.t

        def get_mimetype(self):
            return "text/html"

        def get_contentprovider(self):
            return StringProvider(self.b)

        def get_hints(self):
            return {Hint.FRONT_ARTICLE: True}

    with Creator(str(path)).config_indexing(True, lang) as c:
        c.set_mainpath(next(iter(articles)).replace(" ", "_"))
        for k, v in {"Name": name, "Title": "test", "Language": lang, "Description": "t", "Creator": "t",
                     "Publisher": "t", "Date": "2026-01-01"}.items():
            c.add_metadata(k, v)
        for title, text in articles.items():
            c.add_item(A(title.replace(" ", "_"), title, f"<html><body><h1>{title}</h1><p>{text}</p></body></html>"))


def test_delta_kdd_loop(cfg, tmp_path):
    zim_dir = cfg.path("paths.zim_dir")
    make_snapshot(zim_dir / "wikipedia_en_test_nopic_2026-01.zim", SNAPSHOT_ARTICLES)
    kb = KnowledgeBase(cfg)
    d = DeltaKDD(cfg, kb, log=lambda *a: None)
    docs = [
        {"id": "dup-ohm", "source": "test", "title": "Ohm's law", "text": SNAPSHOT_ARTICLES["Ohm's law"], "lang": "en"},
        {"id": "paper-1", "source": "arxiv:cs.RO", "title": NEW_PAPER[0], "text": NEW_PAPER[1], "lang": "en",
         "category": "cs.RO", "url": "https://arxiv.org/abs/x", "license": "CC0"},
        {"id": "paper-1-again", "source": "arxiv:cs.RO", "title": NEW_PAPER[0], "text": NEW_PAPER[1], "lang": "en"},
        {"id": "short", "source": "test", "title": "x", "text": "too short", "lang": "en"},
    ]
    rep = d.ingest(docs)
    dec = {x["id"]: x for x in rep["decisions"]}
    assert dec["dup-ohm"]["decision"] == "rejected" and dec["dup-ohm"]["reason"] == "covered by snapshot"
    assert dec["paper-1"]["decision"] == "accepted" and dec["paper-1"]["subject"] == "computer_science"
    assert dec["paper-1-again"]["reason"] == "exact duplicate"
    assert dec["short"]["reason"] == "too short"
    built = d.build()
    assert built[0]["docs"] == 1 and built[0]["archive"].startswith("gatemoe_delta_en_")
    # the delta archive is searched next to the snapshot (and can be switched off)
    hits = kb.search([("tactile transformer grasping", "en")], learner_lang="en", subject="computer_science")
    assert any(h["zim"] == "gatemoe_delta_en" for h in hits)
    cfg.tree["knowledge"]["delta"] = False
    hits = kb.search([("tactile transformer grasping", "en")], learner_lang="en", subject="computer_science")
    assert not any(h["zim"] == "gatemoe_delta_en" for h in hits)
    cfg.tree["knowledge"]["delta"] = True
    # a newer snapshot that contains the paper makes prune drop it from the delta
    make_snapshot(zim_dir / "wikipedia_en_test_nopic_2026-02.zim", {**SNAPSHOT_ARTICLES, NEW_PAPER[0]: NEW_PAPER[1]})
    (zim_dir / "wikipedia_en_test_nopic_2026-01.zim").unlink()
    assert d.prune()["en"]["dropped"] == ["paper-1"]
    assert d.build()[0]["archive"] is None
    assert d.status()["delta_archives"] == []


def test_inbox_and_keyphrases(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "lab_manual.md").write_text("# Op-amp lab\n\nBuild an inverting amplifier with a gain of ten ...")
    (inbox / "notes.html").write_text("<html><body><p>" + "Kirchhoff current law at every node. " * 5 + "</p></body></html>")
    docs = read_inbox(inbox)
    assert {x["title"] for x in docs} == {"Op-amp lab", "notes"}
    assert "transformer" in keyphrases(*NEW_PAPER)[:6] or "tactile" in keyphrases(*NEW_PAPER)[:6]


def test_gap_mining_recommends_missing_archives(cfg, tmp_path):
    jobs = tmp_path / "jobs"
    for i, (lang, subj, src) in enumerate([("kn", "biology", []), ("kn", "biology", [{"lang": "en"}]),
                                           ("kn", "biology", []), ("en", "physics", [{"lang": "en"}] * 5)]):
        (jobs / str(i)).mkdir(parents=True)
        (jobs / str(i) / "lesson.json").write_text(json.dumps(
            {"language": {"lang": lang}, "route": {"subject": subj}, "sources": src, "plan": {"key_terms": ["enzyme"]}}))
    mined = kdd.mine(kdd.records_from_lessons([jobs]))
    assert [(c["lang"], c["subject"]) for c in mined["weak_cells"]] == [("kn", "biology")]
    recs = [r["archive"] for r in kdd.recommend(mined, cfg.path("paths.zim_dir"))]
    assert recs[0] == "wikipedia_kn_all_nopic" and "libretexts.org_en_bio" in recs
    assert kdd.project_of("libretexts.org_en_bio") == "libretexts"
