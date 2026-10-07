"""Wikipedia/Kiwix HTML -> clean paragraphs (selectolax when available, stdlib fallback)."""
from __future__ import annotations

import re
from html.parser import HTMLParser

_DROP_SELECTORS = ["script", "style", "table", "sup.reference", ".reference", ".reflist", ".references",
                   ".navbox", ".infobox", ".mw-editsection", "#toc", ".toc", ".hatnote", ".thumb", "figure",
                   ".metadata", ".mbox-small", ".noprint", "math", ".mwe-math-element"]
_STOP_HEADINGS = re.compile(r"^(references|external links|see also|further reading|notes|bibliography|sources|"
                            r"ಉಲ್ಲೇಖಗಳು|बाहरी कड़ियाँ|सन्दर्भ|संदर्भ|மேற்கோள்கள்|మూలాలు)$", re.I)
_WS = re.compile(r"\s+")


def _clean(s: str) -> str:
    s = re.sub(r"\[\d+\]|\[citation needed\]", "", s)
    return _WS.sub(" ", s).strip()


def html_to_paragraphs(html: str, max_paragraphs: int = 60) -> list[str]:
    try:
        from selectolax.parser import HTMLParser as Fast
    except ImportError:
        return _fallback(html, max_paragraphs)
    tree = Fast(html)
    for sel in _DROP_SELECTORS:
        for node in tree.css(sel):
            node.decompose()
    out: list[str] = []
    root = tree.body or tree.root
    if root is None:
        return out
    for node in root.css("h2, h3, p, li"):
        text = _clean(node.text(separator=" "))
        if node.tag in ("h2", "h3"):
            if _STOP_HEADINGS.match(text):
                break
            continue
        if len(text) >= 40:
            out.append(text)
        if len(out) >= max_paragraphs:
            break
    return out


class _Collector(HTMLParser):
    SKIP = {"script", "style", "table", "sup", "math", "figure"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth_skip = 0
        self.cur: list[str] | None = None
        self.paras: list[str] = []
        self.stop = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth_skip += 1
        elif tag in ("p", "li", "h2", "h3") and not self.depth_skip:
            self.cur = [tag]

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth_skip:
            self.depth_skip -= 1
        elif tag in ("p", "li", "h2", "h3") and self.cur is not None:
            kind, text = self.cur[0], _clean("".join(self.cur[1:]))
            self.cur = None
            if kind in ("h2", "h3"):
                if _STOP_HEADINGS.match(text):
                    self.stop = True
            elif len(text) >= 40 and not self.stop:
                self.paras.append(text)

    def handle_data(self, data):
        if self.cur is not None and not self.depth_skip:
            self.cur.append(data)


def _fallback(html: str, max_paragraphs: int) -> list[str]:
    c = _Collector()
    c.feed(html)
    return c.paras[:max_paragraphs]
