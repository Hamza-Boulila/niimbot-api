"""In-memory print queue: requests from any number of clients print one at a time."""

import itertools
import logging
import queue
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Literal

from PIL import Image

from .service import PrinterService

JobStatus = Literal["queued", "printing", "done", "failed"]


@dataclass
class Job:
    id: str
    bitmap: Image.Image
    density: int | None
    copies: int
    source: str | None = None  # caller-supplied reference (order id, etc.)
    status: JobStatus = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict | None = None
    error: str | None = None
    seq: int = 0
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    def wait(self, timeout: float | None) -> bool:
        return self._done.wait(timeout)

    def to_dict(self, jobs_ahead: int | None = None) -> dict:
        d = {
            "id": self.id,
            "status": self.status,
            "source": self.source,
            "copies": self.copies,
            "width_px": self.bitmap.width,
            "height_px": self.bitmap.height,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }
        if jobs_ahead is not None:
            d["jobs_ahead"] = jobs_ahead
        return d


class PrintQueue:
    def __init__(self, service: PrinterService, history: int = 200):
        self.service = service
        self._history = history
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._queue: queue.Queue[Job | None] = queue.Queue()
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def submit(
        self,
        bitmap: Image.Image,
        density: int | None = None,
        copies: int = 1,
        source: str | None = None,
    ) -> Job:
        job = Job(uuid.uuid4().hex[:12], bitmap, density, copies, source)
        job.seq = next(self._seq)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > self._history:
                oldest = next(iter(self._jobs.values()))
                if oldest.status in ("queued", "printing"):
                    break
                self._jobs.popitem(last=False)
        self._queue.put(job)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> list[Job]:
        with self._lock:
            return list(reversed(self._jobs.values()))[:limit]

    def jobs_ahead(self, job: Job) -> int | None:
        """How many unfinished jobs will print before this one (None once finished)."""
        if job.status not in ("queued", "printing"):
            return None
        with self._lock:
            return sum(
                1 for j in self._jobs.values()
                if j.status in ("queued", "printing") and j.seq < job.seq
            )

    @property
    def pending(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status in ("queued", "printing"))

    def stop(self):
        self._queue.put(None)
        self._worker.join(timeout=5)

    def _run(self):
        while (job := self._queue.get()) is not None:
            job.status = "printing"
            job.started_at = time.time()
            logging.info(f"Job {job.id} printing ({job.copies} copies)")
            try:
                job.result = self.service.print_bitmap(job.bitmap, job.density, job.copies)
                job.status = "done"
            except Exception as e:
                logging.exception(f"Job {job.id} failed")
                job.error = f"{type(e).__name__}: {e}"
                job.status = "failed"
            finally:
                job.finished_at = time.time()
                job._done.set()
