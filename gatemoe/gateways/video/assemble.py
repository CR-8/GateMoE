"""Join per-beat clips + narration into one MP4, with WebVTT/SRT subtitles built from the script.

Video: clips share codec/size/fps, so the concat demuxer joins them without re-encoding.
Audio: per-beat WAVs are padded/trimmed to the exact (frame-rounded) clip length and joined in
one filter graph, then encoded to AAC once (no per-segment AAC padding drift).
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _ts(seconds: float, sep: str) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_subtitles(texts: list[str], durations: list[float], vtt: Path, srt: Path) -> None:
    cues, t = [], 0.0
    for text, d in zip(texts, durations):
        cues.append((t, t + d, " ".join((text or "").split())))
        t += d
    esc = lambda c: c.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # VTT cue markup
    vtt.write_text("WEBVTT\n\n" + "".join(f"{_ts(a, '.')} --> {_ts(b, '.')}\n{esc(c)}\n\n" for a, b, c in cues if c),
                   encoding="utf-8")
    srt.write_text("".join(f"{i}\n{_ts(a, ',')} --> {_ts(b, ',')}\n{c}\n\n"
                           for i, (a, b, c) in enumerate([q for q in cues if q[2]], 1)), encoding="utf-8")


def assemble(clips: list[Path], durations: list[float], wavs: list[str | None], out: Path,
             ffmpeg: str = "ffmpeg", timeout: float = 900) -> None:
    work = out.parent
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{Path(c).resolve()}'\n" for c in clips), encoding="utf-8")
    video_only = work / "video_only.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    str(video_only)], check=True, capture_output=True, timeout=timeout)
    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(video_only)]
    graph = ""
    for i, (wav, d) in enumerate(zip(wavs, durations)):
        if wav:
            cmd += ["-i", str(wav)]
        else:  # silent beat (no TTS voice for this language)
            cmd += ["-f", "lavfi", "-t", f"{d:.6f}", "-i", "anullsrc=r=48000:cl=mono"]
        graph += f"[{i + 1}:a]aformat=sample_rates=48000:channel_layouts=mono,apad,atrim=0:{d:.6f}[a{i}];"
    graph += "".join(f"[a{i}]" for i in range(len(durations))) + f"concat=n={len(durations)}:v=0:a=1[aout]"
    cmd += ["-filter_complex", graph, "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac",
            "-b:a", "96k", "-movflags", "+faststart", "-shortest", str(out)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    video_only.unlink(missing_ok=True)
    lst.unlink(missing_ok=True)


def probe_duration(path: Path, ffprobe: str = "ffprobe") -> float:
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, timeout=60).stdout.strip()
    return float(out or 0)
