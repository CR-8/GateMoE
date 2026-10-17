"""Subject knowledge packs: live Kiwix listing -> plan of field archives; with many archives
installed, each lesson searches the ones whose titles match its terms."""
from libzim.writer import Creator, Hint, Item, StringProvider

from gatemoe.knowledge import KnowledgeBase
from gatemoe.knowledge.kdd import field_plan, parse_listing

PRE = """<pre><a href="../">../</a>
<a href="wikipedia_en_history_nopic_2025-06.zim">wikipedia_en_history_nopic_2025-06.zim</a>     2025-06-10 08:12   812M
<a href="wikipedia_en_history_nopic_2025-09.zim">wikipedia_en_history_nopic_2025-09.zim</a>     2025-09-11 09:40   1.2G
<a href="wikipedia_en_100_maxi_2025-09.zim">wikipedia_en_100_maxi_2025-09.zim</a>     2025-09-01 01:00    20M
<a href="wikipedia_en_football_nopic_2025-08.zim">wikipedia_en_football_nopic_2025-08.zim</a>     2025-08-02 10:00   2.6G
<a href="wikipedia_en_music_maxi_2025-08.zim">wikipedia_en_music_maxi_2025-08.zim</a>     2025-08-02 10:00   3.4G
<a href="wikipedia_en_music_nopic_2025-08.zim">wikipedia_en_music_nopic_2025-08.zim</a>     2025-08-02 10:00   600M
<a href="wikipedia_en_physics_nopic_2025-08.zim">wikipedia_en_physics_nopic_2025-08.zim</a>   2025-08-03 10:00   430M
<a href="wikipedia_en_all_nopic_2025-08.zim">wikipedia_en_all_nopic_2025-08.zim</a>   2025-08-03 10:00   48G
<a href="wikipedia_hi_all_nopic_2025-08.zim">wikipedia_hi_all_nopic_2025-08.zim</a>   2025-08-03 10:00   1.1G
</pre>"""

TABLE = """<table><tr><th>Name</th><th>Last modified</th><th>Size</th></tr>
<tr><td><a href="libretexts.org_en_bio_2025-01.zim">libretexts.org_en_bio_2025-01.zim</a></td><td align="right">2025-01-05 10:11 </td><td align="right">1.9G</td></tr>
<tr><td><a href="libretexts.org_en_human_2025-01.zim">libretexts.org_en_human_2025-01.zim</a></td><td align="right">2025-01-05 10:11 </td><td align="right">950M</td></tr>
</table>"""


def _rows(listing, project):
    from gatemoe.knowledge.kdd import _DATED
    latest = {}
    for f, s in parse_listing(listing).items():
        m = _DATED.match(f)
        if m and (m["prefix"] not in latest or m["date"] > latest[m["prefix"]]["date"]):
            latest[m["prefix"]] = {"prefix": m["prefix"], "file": f, "date": m["date"], "size_mb": s,
                                   "url": f"https://download.kiwix.org/zim/{project}/{f}"}
    return sorted(latest.values(), key=lambda r: r["prefix"])


def test_listing_parser_reads_both_layouts():
    pre = parse_listing(PRE)
    assert pre["wikipedia_en_history_nopic_2025-09.zim"] == 1228.8 and pre["wikipedia_en_physics_nopic_2025-08.zim"] == 430.0
    tab = parse_listing(TABLE)
    assert tab == {"libretexts.org_en_bio_2025-01.zim": 1945.6, "libretexts.org_en_human_2025-01.zim": 950.0}


def test_field_plan_prefers_text_only_skips_installed_and_general():
    se = [{"prefix": "physics.stackexchange.com_en_all", "file": "physics.stackexchange.com_en_all_2025-07.zim",
           "date": "2025-07", "size_mb": 900.0, "url": "u"},
          {"prefix": "gaming.stackexchange.com_en_all", "file": "gaming.stackexchange.com_en_all_2025-07.zim",
           "date": "2025-07", "size_mb": 500.0, "url": "u"}]
    src = {"wikipedia": _rows(PRE, "wikipedia"), "libretexts": _rows(TABLE, "libretexts"), "stack_exchange": se}
    plan = field_plan("en", count=50, max_gb=60, installed={"wikipedia_en_physics_nopic"}, sources=src)
    files = [r["file"] for r in plan["archives"]]
    assert files == ["wikipedia_en_history_nopic_2025-09.zim",
                     "libretexts.org_en_bio_2025-01.zim", "libretexts.org_en_human_2025-01.zim",
                     "physics.stackexchange.com_en_all_2025-07.zim"]    # fields of study only: no football, music, gaming
    small = field_plan("en", count=50, max_gb=2.0, installed=set(), sources=src)
    assert small["total_gb"] <= 2.0 and small["skipped"]
    assert len(field_plan("en", count=2, max_gb=60, installed=set(), sources=src)["archives"]) == 2
    assert all("previous" not in (r.get("previous") or {}) for r in plan["archives"])


class _It(Item):
    def __init__(self, path, title, html):
        super().__init__()
        self.p, self.t, self.h = path, title, html

    def get_path(self):
        return self.p

    def get_title(self):
        return self.t

    def get_mimetype(self):
        return "text/html"

    def get_contentprovider(self):
        return StringProvider(self.h)

    def get_hints(self):
        return {Hint.FRONT_ARTICLE: True}


def _zim(path, name, titles):
    with Creator(str(path)).config_indexing(True, "eng") as c:
        c.set_mainpath(titles[0].replace(" ", "_"))
        for k, v in {"Name": name, "Language": "eng", "Title": name, "Creator": "t", "Publisher": "t",
                     "Date": "2025-09-01", "Description": "d"}.items():
            c.add_metadata(k, v)
        for t in titles:
            c.add_item(_It(t.replace(" ", "_"), t, f"<html><body><p>{t} is a topic. " * 20 + "</p></body></html>"))


def test_many_archives_lesson_terms_pick_the_archive(cfg):
    zim_dir = cfg.path("paths.zim_dir")
    zim_dir.mkdir(parents=True, exist_ok=True)
    _zim(zim_dir / "wikipedia_en_history_nopic_2025-09.zim", "wikipedia_en_history_nopic",
         ["French Revolution", "French Revolutionary Wars", "Reign of Terror", "Napoleon"])
    _zim(zim_dir / "wikipedia_en_music_nopic_2025-08.zim", "wikipedia_en_music_nopic",
         ["Sonata", "Symphony", "Raga", "Jazz"])
    _zim(zim_dir / "wikipedia_en_geography_nopic_2025-08.zim", "wikipedia_en_geography_nopic",
         ["River", "Delta", "Plateau", "Monsoon"])
    _zim(zim_dir / "wikipedia_en_physics_nopic_2025-08.zim", "wikipedia_en_physics_nopic",
         ["Ohm's law", "Resistance", "Voltage", "Current"])
    cfg.tree["knowledge"]["max_zims_per_language"] = 1
    kb = KnowledgeBase(cfg)
    pick = kb.select("en", "other", probe=[("French Revolution", "en"), ("Reign of Terror", "en")])
    assert [z["name"] for z in pick] == ["wikipedia_en_history_nopic"]
    pick = kb.select("en", "other", probe=[("monsoon", "en")])
    assert [z["name"] for z in pick] == ["wikipedia_en_geography_nopic"]
    assert [z["name"] for z in kb.select("en", "physics", probe=[("resistance", "en")])] == ["wikipedia_en_physics_nopic"]
    assert kb.select("en", "other", probe=[("photosynthesis", "en")]) == []     # nothing matches, no general archive
    cfg.tree["knowledge"]["select"] = "size"                                     # the rule paper 1 measured
    assert len(KnowledgeBase(cfg).select("en", "physics")) == 1
