"""Child-process entry point: render beat spec(s) to mp4 (no hold), or only validate them.

    python -I manim_render_beat.py --check --spec beat.json [--spec b2.json ...]   # validate only
    python -I manim_render_beat.py --spec beat.json --out beat.mp4 \
        [--spec b2.json --out b2.mp4 ...]            # batch: pay the ~1 s import once
        [--width 1280 --height 720 --fps 24] [--work DIR] [--png]

Prints one JSON line per beat on stdout:
    {"i": 0, "ok": true, "out": "...", "scene": "GraphBeat", "render_s": 1.9, "frames": 73, "duration_s": 3.04}
    {"i": 1, "ok": false, "kind": "spec", "error": "..."}     (bad spec / bad Typst -> feed back to the LLM)
    {"i": 2, "ok": false, "kind": "render", "error": "..."}
Exit code: 0 all ok, 3 if any spec error (and no render error), 4 if any render error.
Run it under the ManimEngine sandbox (timeout + prlimit), never inside the server process.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # -I drops the script dir from sys.path; add only our own dir


def render_one(i, spec_path, out, a, work, templates, tempconfig) -> dict:
    t0 = time.monotonic()
    os.environ["GATEMOE_BEAT_JSON"] = os.path.abspath(spec_path)
    try:
        spec = templates.load_spec()
    except templates.SpecError as e:
        return {"i": i, "ok": False, "kind": "spec", "error": str(e)}
    cls = templates.SCENES[spec["type"]]
    beat_dir = os.path.join(work, f"b{i}")
    opts = {
        "pixel_width": a.width,
        "pixel_height": a.height,
        "frame_rate": a.fps,
        "media_dir": beat_dir,
        "disable_caching": True,      # skip partial-movie hashing/cache lookups
        "verbosity": "ERROR",
        "progress_bar": "none",
        "preview": False,
        "write_to_movie": not a.png,
        "save_last_frame": a.png,
        "format": "png" if a.png else "mp4",
        "output_file": "beat",
        "renderer": "cairo",
        "max_inflight_encoders": a.encoders,  # >1 overlaps x264 encoding with cairo drawing
    }
    if a.cache:  # persistent content-hashed SVG caches (Typst + Pango) shared across beats
        opts["tex_dir"] = os.path.join(a.cache, "typst")
        opts["text_dir"] = os.path.join(a.cache, "text")
    try:
        with tempconfig(opts):
            scene = cls()
            scene.spec = spec  # already validated; setup() won't reload
            scene.render()
            fw = scene.renderer.file_writer
            src = fw.image_file_path if a.png else fw.movie_file_path
            dur = float(scene.time)
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        shutil.move(str(src), out)
        return {"i": i, "ok": True, "out": out, "scene": cls.__name__,
                "render_s": round(time.monotonic() - t0, 3),
                "frames": int(round(dur * a.fps)), "duration_s": round(dur, 3)}
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        # Typst compile errors are spec errors too (the LLM wrote bad math)
        kind = "spec" if isinstance(e, templates.SpecError) or "TypstError" in err else "render"
        return {"i": i, "ok": False, "kind": kind, "error": err[:2000],
                "trace": traceback.format_exc()[-3000:]}
    finally:
        shutil.rmtree(beat_dir, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", action="append", required=True)
    ap.add_argument("--out", action="append", default=[])
    ap.add_argument("--check", action="store_true", help="validate the specs only (no rendering)")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--work", default=None, help="scratch media_dir root (deleted afterwards)")
    ap.add_argument("--png", action="store_true", help="save last frame as PNG instead of mp4")
    ap.add_argument("--encoders", type=int, default=1, help="manim max_inflight_encoders")
    ap.add_argument("--cache", default=None, help="persistent dir for Typst/Pango SVG caches")
    a = ap.parse_args()
    if not a.check and len(a.spec) != len(a.out):
        ap.error("--spec and --out must be given the same number of times")

    work = a.work or tempfile.mkdtemp(prefix="gm_manim_")
    t_imp = time.monotonic()
    import manim_templates as templates  # noqa: E402
    from manim import tempconfig  # noqa: E402

    print(json.dumps({"import_s": round(time.monotonic() - t_imp, 3)}), flush=True)
    worst = 0
    if a.check:
        for i, sp in enumerate(a.spec):
            try:
                with open(sp, encoding="utf-8") as fh:
                    templates.validate_spec(json.load(fh))
                print(json.dumps({"i": i, "ok": True}), flush=True)
            except Exception as e:  # noqa: BLE001
                worst = 3
                print(json.dumps({"i": i, "ok": False, "kind": "spec", "error": f"{type(e).__name__}: {e}"[:1500]},
                                 ensure_ascii=False), flush=True)
        return worst
    try:
        for i, (sp, out) in enumerate(zip(a.spec, a.out)):
            r = render_one(i, sp, out, a, work, templates, tempconfig)
            print(json.dumps(r, ensure_ascii=False), flush=True)
            if not r["ok"]:
                worst = max(worst, 3 if r["kind"] == "spec" else 4)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return worst


if __name__ == "__main__":
    sys.exit(main())
