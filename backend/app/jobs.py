"""Background jobs.

Two worker threads ("lanes"): `cv` runs model work one job at a time, `io` runs
ingest / hashing / thumbnails. Every job is a row in the `jobs` table so the UI can
poll progress. Handlers derive their work list from database state, so a job that
is cancelled or dies with the process can simply be run again and continues.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from .db import engine
from .models import Job, now

log = logging.getLogger(__name__)

LANES = ("cv", "io")


class JobCancelled(Exception):
    pass


@dataclass
class _Handler:
    fn: Callable[[JobContext, dict], dict | None]
    lane: str
    title: str


_REGISTRY: dict[str, _Handler] = {}


def job(type_: str, lane: str = "io", title: str | None = None):
    """Decorator registering a job handler: fn(ctx, params) -> result dict | None."""

    def deco(fn):
        _REGISTRY[type_] = _Handler(fn=fn, lane=lane, title=title or type_.replace("_", " "))
        return fn

    return deco


class JobContext:
    def __init__(self, runner: JobRunner, job_id: int):
        self.runner = runner
        self.job_id = job_id
        self._last_write = 0.0
        self._done = 0
        self._total = 0
        self._message = ""

    # ---- progress -----------------------------------------------------------
    def progress(self, done: int | None = None, total: int | None = None, message: str | None = None, force: bool = False) -> None:
        if done is not None:
            self._done = done
        if total is not None:
            self._total = total
        if message is not None:
            self._message = message
        t = time.monotonic()
        if force or t - self._last_write > 0.25 or (self._total and self._done >= self._total):
            self._last_write = t
            with Session(engine()) as s:
                row = s.get(Job, self.job_id)
                if row is not None:
                    row.done = self._done
                    row.total = self._total
                    row.message = self._message[:500]
                    s.add(row)
                    s.commit()

    def message(self, text: str) -> None:
        self.progress(message=text, force=True)

    # ---- cancellation --------------------------------------------------------
    @property
    def cancelled(self) -> bool:
        return self.job_id in self.runner._cancel_requests

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelled()

    # ---- db ------------------------------------------------------------------
    def session(self) -> Session:
        return Session(engine())


class JobRunner:
    def __init__(self) -> None:
        self._queues: dict[str, queue.Queue[int]] = {lane: queue.Queue() for lane in LANES}
        self._threads: list[tuple[str, threading.Thread]] = []
        self._stop = threading.Event()
        self._cancel_requests: set[int] = set()
        self._lock = threading.Lock()

    # ---- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        from . import config

        self._recover()
        for lane in LANES:
            n = config.CV_WORKERS if lane == "cv" else 1
            for i in range(n):
                name = f"jobs-{lane}" if n == 1 else f"jobs-{lane}-{i + 1}"
                t = threading.Thread(target=self._worker, args=(lane,), name=name, daemon=True)
                t.start()
                self._threads.append((lane, t))

    def stop(self) -> None:
        self._stop.set()
        for lane, _t in self._threads:
            self._queues[lane].put(-1)

    def _recover(self) -> None:
        """Jobs left queued/running by a previous process are marked failed; they are
        idempotent, so the user just runs them again."""
        with Session(engine()) as s:
            rows = s.exec(select(Job).where(Job.status.in_(["queued", "running"]))).all()
            from .models import ImportSession

            for sess in s.exec(select(ImportSession).where(ImportSession.status.in_(["queued", "running"]))).all():
                sess.status = "failed"
                s.add(sess)
            for r in rows:
                r.status = "failed"
                r.error = "Server restarted before this job finished. Run it again; it continues where it stopped."
                r.finished_at = now()
                s.add(r)
            s.commit()

    # ---- api -----------------------------------------------------------------
    def enqueue(self, type_: str, params: dict[str, Any] | None = None, dedupe: bool = True) -> Job:
        if type_ not in _REGISTRY:
            raise KeyError(f"unknown job type {type_}")
        h = _REGISTRY[type_]
        params = params or {}
        with Session(engine()) as s:
            if dedupe:
                existing = s.exec(
                    select(Job).where(Job.type == type_, Job.status.in_(["queued", "running"]))
                ).all()
                for e in existing:
                    if e.params == params:
                        return e
            row = Job(type=type_, lane=h.lane, status="queued", params=params, message="Queued")
            s.add(row)
            s.commit()
            s.refresh(row)
        self._queues[h.lane].put(row.id)
        return row

    def cancel(self, job_id: int) -> Job | None:
        with Session(engine()) as s:
            row = s.get(Job, job_id)
            if row is None:
                return None
            if row.status == "queued":
                row.status = "cancelled"
                row.message = "Cancelled"
                row.finished_at = now()
                s.add(row)
                s.commit()
                s.refresh(row)
                return row
            if row.status == "running":
                with self._lock:
                    self._cancel_requests.add(job_id)
                row.message = "Cancelling..."
                s.add(row)
                s.commit()
                s.refresh(row)
            return row

    @staticmethod
    def title_for(type_: str) -> str:
        h = _REGISTRY.get(type_)
        return h.title if h else type_

    # ---- worker --------------------------------------------------------------
    def _worker(self, lane: str) -> None:
        _lower_priority()
        q = self._queues[lane]
        while not self._stop.is_set():
            job_id = q.get()
            if job_id < 0:
                break
            try:
                self._run(job_id)
            except Exception:  # pragma: no cover - last-resort guard
                log.exception("job %s crashed outside handler", job_id)

    def _run(self, job_id: int) -> None:
        with Session(engine()) as s:
            row = s.get(Job, job_id)
            if row is None or row.status != "queued":
                return
            row.status = "running"
            row.started_at = now()
            row.message = "Starting"
            s.add(row)
            s.commit()
            type_ = row.type
            params = dict(row.params or {})
        h = _REGISTRY.get(type_)
        ctx = JobContext(self, job_id)
        status, result, error = "done", {}, None
        t0 = time.monotonic()
        try:
            if h is None:
                raise KeyError(f"unknown job type {type_}")
            result = h.fn(ctx, params) or {}
        except JobCancelled:
            status = "cancelled"
        except Exception as e:
            status = "failed"
            error = f"{type(e).__name__}: {e}"
            log.error("job %s (%s) failed:\n%s", job_id, type_, traceback.format_exc())
        finally:
            with self._lock:
                self._cancel_requests.discard(job_id)
        with Session(engine()) as s:
            row = s.get(Job, job_id)
            if row is not None:
                row.status = status
                row.done = ctx._done
                row.total = ctx._total
                row.result = result if isinstance(result, dict) else {"value": result}
                row.error = error
                row.finished_at = now()
                if status == "done":
                    row.message = result.get("message", ctx._message or "Done") if isinstance(result, dict) else "Done"
                elif status == "cancelled":
                    row.message = "Cancelled"
                else:
                    row.message = f"Failed: {error}"[:500]
                s.add(row)
                s.commit()
        log.info("job %s %s -> %s in %.1fs", job_id, type_, status, time.monotonic() - t0)
        if status == "done":
            self._continue_chain(params, result if isinstance(result, dict) else {})

    def _continue_chain(self, params: dict, result: dict) -> None:
        """A job may carry `chain`: [{"type", "params"}, ...] (jobs to run after it, in order),
        or return `chain` from its handler. The next one is queued with the rest of the chain."""
        chain = list(result.get("chain") or params.get("chain") or [])
        if not chain:
            return
        nxt, rest = chain[0], chain[1:]
        p = dict(nxt.get("params") or {})
        if rest:
            p["chain"] = rest
        try:
            self.enqueue(nxt["type"], p, dedupe=False)
        except Exception as e:
            log.warning("could not queue chained job %s: %s", nxt.get("type"), e)

    def enqueue_chain(self, steps: list[tuple[str, dict]]) -> Job:
        """Queue steps[0] now; each following step starts when the previous one finished OK."""
        (t0, p0), rest = steps[0], steps[1:]
        p = dict(p0)
        if rest:
            p["chain"] = [{"type": t, "params": pp} for t, pp in rest]
        return self.enqueue(t0, p, dedupe=False)


def _lower_priority() -> None:
    """Server mode: job threads run at a lower CPU priority so heavy work never starves the
    web requests or other apps on the machine. On Linux a thread is its own scheduling task,
    so this affects only the calling worker thread (and processes it starts)."""
    from . import config

    if config.JOB_NICE <= 0 or not hasattr(os, "setpriority"):
        return
    try:
        tid = threading.get_native_id()
        os.setpriority(os.PRIO_PROCESS, tid, min(19, os.getpriority(os.PRIO_PROCESS, tid) + config.JOB_NICE))
    except OSError as e:
        log.warning("could not lower job priority: %s", e)


_runner: JobRunner | None = None


def runner() -> JobRunner:
    global _runner
    if _runner is None:
        _runner = JobRunner()
    return _runner


def job_to_dict(j: Job) -> dict:
    def iso(d: datetime | None) -> str | None:
        return d.isoformat() if d else None

    return {
        "id": j.id,
        "type": j.type,
        "title": JobRunner.title_for(j.type),
        "lane": j.lane,
        "status": j.status,
        "done": j.done,
        "total": j.total,
        "message": j.message,
        "params": j.params,
        "result": j.result,
        "error": j.error,
        "created_at": iso(j.created_at),
        "started_at": iso(j.started_at),
        "finished_at": iso(j.finished_at),
    }
