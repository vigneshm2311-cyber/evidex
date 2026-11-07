"""Background jobs for long model runs.

A draft takes minutes on a local model. Running it inside a Streamlit script run means any rerun
(a widget change, a reconnect after the phone screen locks) stops it and the work is lost. Jobs run
in their own thread instead; the page only reads their progress, and the draft is saved to the
database whatever happens to the browser.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterator

KEEP_S = 6 * 3600  # finished jobs are forgotten after this long (their drafts stay in the database)


@dataclass
class Job:
    id: str
    label: str
    started: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)
    done: bool = False
    error: str | None = None
    finished: float | None = None

    @property
    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started


_jobs: dict[str, Job] = {}
_guard = threading.Lock()


def start(run: Callable[[], Iterator[dict]], label: str = "", on_finish: Callable[[], None] | None = None) -> Job:
    """Run `run()` (a generator of pipeline events) in a background thread.
    `on_finish` is always called at the end, e.g. to release the shared model lock."""
    job = Job(id=uuid.uuid4().hex[:10], label=label)
    with _guard:
        _forget_old()
        _jobs[job.id] = job

    def work():
        try:
            for e in run():
                job.events.append(e)
        except Exception as e:  # keep the error visible to the page instead of losing it
            job.error = f"{type(e).__name__}: {e}"
            job.events.append({"run_id": "", "step": "job", "status": "error",
                               "data": {"error": job.error}, "ms": 0})
        finally:
            job.done = True
            job.finished = time.time()
            if on_finish:
                on_finish()

    threading.Thread(target=work, name=f"evidex-job-{job.id}", daemon=True).start()
    return job


def get(job_id: str | None) -> Job | None:
    return _jobs.get(job_id) if job_id else None


def running() -> list[Job]:
    return [j for j in _jobs.values() if not j.done]


def _forget_old():
    now = time.time()
    for k in [k for k, j in _jobs.items() if j.done and now - (j.finished or now) > KEEP_S]:
        del _jobs[k]
