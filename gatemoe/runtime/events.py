"""Per-job event log: stage timings, model swaps, LLM calls and system samples.

Every event is appended to ``events.jsonl`` in the job folder (the raw data for the
"theoretical vs realised savings" analysis) and fanned out to live subscribers (SSE).
"""
from __future__ import annotations

import json
import queue
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class Cancelled(Exception):
    """Raised inside a running job when the user cancels it."""


class EventLog:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.t0 = time.time()
        self.events: list[dict] = []
        self.stage_times: dict[str, float] = {}
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.cancel_flag = threading.Event()
        self.current_stage: str | None = None
        self.closed = False

    # -- emission -------------------------------------------------------------------------
    def emit(self, kind: str, **data: Any) -> dict:
        ev = {"t": round(time.time() - self.t0, 3), "ts": time.time(), "kind": kind, **data}
        with self._lock:
            self.events.append(ev)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass
        return ev

    @contextmanager
    def stage(self, name: str, **data: Any) -> Iterator[dict]:
        """Time a pipeline stage. Yields a dict the caller may fill with result fields."""
        self.check_cancel()
        prev = self.current_stage
        self.current_stage = name
        self.emit("stage_start", stage=name, **data)
        t = time.perf_counter()
        extra: dict = {}
        try:
            yield extra
        except Cancelled:
            self.emit("stage_end", stage=name, ok=False, cancelled=True,
                      seconds=round(time.perf_counter() - t, 3))
            raise
        except Exception as exc:  # recorded, then re-raised for the caller to decide
            self.emit("stage_end", stage=name, ok=False, error=f"{type(exc).__name__}: {exc}",
                      seconds=round(time.perf_counter() - t, 3), **extra)
            raise
        else:
            secs = round(time.perf_counter() - t, 3)
            self.stage_times[name] = self.stage_times.get(name, 0.0) + secs
            self.emit("stage_end", stage=name, ok=True, seconds=secs, **extra)
        finally:
            self.current_stage = prev

    def check_cancel(self) -> None:
        if self.cancel_flag.is_set():
            raise Cancelled("cancelled by user")

    # -- subscription -----------------------------------------------------------------------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            for ev in self.events[-900:]:  # replay history so late subscribers see everything
                q.put_nowait(ev)
            if self.closed:
                q.put_nowait({"kind": "eof"})
            else:
                self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def close(self) -> None:
        with self._lock:
            self.closed = True
            subs = list(self._subs)
            self._subs.clear()
        for q in subs:
            for _ in range(2):              # a full queue (slow client) must still receive eof
                try:
                    q.put_nowait({"kind": "eof"})
                    break
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass

    # -- summaries --------------------------------------------------------------------------
    def summary(self) -> dict:
        loads = [e for e in self.events if e["kind"] == "model_load"]
        unloads = [e for e in self.events if e["kind"] == "model_unload"]
        llm = [e for e in self.events if e["kind"] == "llm_call"]
        sys_ev = [e for e in self.events if e["kind"] == "sys"]
        mem = [e["mem_available_mb"] for e in sys_ev if e.get("mem_available_mb") is not None]
        temps = [e["temp_c"] for e in sys_ev if e.get("temp_c") is not None]
        return {
            "wall_seconds": round(time.time() - self.t0, 3),
            "stage_seconds": {k: round(v, 3) for k, v in self.stage_times.items()},
            "model_loads": len(loads),
            "model_load_seconds": round(sum(e.get("seconds", 0) for e in loads), 3),
            "model_peak_rss_mb": max([e.get("peak_rss_mb") or 0 for e in unloads] or [0]),
            "llm_calls": len(llm),
            "prompt_tokens": sum(e.get("prompt_n", 0) or 0 for e in llm),
            "cached_prompt_tokens": sum(e.get("cache_n", 0) or 0 for e in llm),
            "generated_tokens": sum(e.get("predicted_n", 0) or 0 for e in llm),
            "min_mem_available_mb": min(mem) if mem else None,
            "max_temp_c": max(temps) if temps else None,
            "throttled_seen": any(e.get("throttled") not in (None, "0x0") for e in sys_ev),
        }
