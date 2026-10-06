import sys, importlib.util
spec = importlib.util.spec_from_file_location("ct", sys.argv[2]); 
sys.argv = [sys.argv[0], sys.argv[1]]
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ct = importlib.util.module_from_spec(spec); spec.loader.exec_module(ct)
enc = ct.encode
SHORT = {"true": "yes", "false": "no"}
N = lambda ins: {"type": "noul", "instructions": ins, "criteria": SHORT}
gw = {k: N(k + "?") for k in ["notes", "flashcards", "quiz", "podcast", "video", "code"]}
A = {"domain": {"type": "choice", "instructions": "Subject?", "criteria": {k: None for k in ["math","physics","chemistry","biology","computing","history","geography","language","economics","arts","other"]}},
     "level": {"type": "score", "instructions": "Level?", "criteria": ["beginner", "intermediate", "advanced"]}, **gw,
     "video_engine": {"type": "choice", "instructions": "Video renderer?", "criteria": {"manim": None, "hyperframes": None, "none": None}},
     "needs_lookup": N("Needs encyclopedia lookup?"), "out_of_scope": N("Out of scope?")}
C = {"bundle": {"type": "choice", "instructions": "Which outputs does the learner want?",
     "criteria": {"notes_only": None, "notes_flashcards": None, "notes_quiz": None, "video_notes": None,
                  "podcast_notes": None, "code_notes": None, "full_pack": None, "other": None}}}
st = "I want to learn how photosynthesis works."
for name, qs in [("A compact 11q (short noul criteria, bare keys)", A), ("B gateways only (6 nouls, short)", gw), ("C one 8-way bundle choice", C)]:
    p, s, sc, sf = enc(st, qs)
    print(f"{name:48s} total={p+s+sc+sf:4d} (schema {sc})")
