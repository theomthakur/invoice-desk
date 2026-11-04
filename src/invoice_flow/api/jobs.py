"""Durable job queue on SQLite with one bounded local worker pool.

SQLite is the authority. A queued run is a row in ``runs`` (lifecycle ``queued``); the in-memory
queue only wakes the worker. On startup:
* ``queued`` runs are re-queued and processed normally;
* ``running`` runs are marked ``interrupted``. They are NOT blindly resumed. A retry creates a
  new run (linked through ``attempt_of``) that goes through the same payable and payment gate, so
  an interrupted run can never create a second payment.

There is no distributed lease or recovery. This is a single-process prototype and the docs say so.
"""

from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass

from ..ledger import load_document
from ..runtime import App, EventRecorder
from ..schemas import Outcome, RunLifecycle, RunResult
from ..service import execute_run
from ..storage import create_run, ensure_workspace, update_run


class QueueFull(Exception):
    pass


@dataclass(frozen=True)
class Job:
    run_id: str
    workspace_id: str
    document_id: str
    mode: str


class JobRunner:
    def __init__(self, app: App) -> None:
        self.app = app
        self._queue: queue.Queue[Job | None] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._cancels: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._stopping = threading.Event()

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self.recover()
        for index in range(max(1, self.app.config.max_concurrent_runs)):
            thread = threading.Thread(
                target=self._work, name=f"invoice-worker-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def stop(self, wait: float = 5.0) -> None:
        self._stopping.set()
        with self._lock:
            for event in self._cancels.values():
                event.set()
        for _ in self._threads:
            self._queue.put(None)
        for thread in self._threads:
            thread.join(timeout=wait)

    def recover(self) -> None:
        """Re-queue queued runs; mark runs that were mid-flight when the app stopped."""
        with self.app.db.transaction() as conn:
            stale = conn.execute(
                "SELECT run_id, workspace_id FROM runs WHERE lifecycle = 'running'"
            ).fetchall()
            queued = conn.execute(
                "SELECT run_id, workspace_id, document_id, mode FROM runs"
                " WHERE lifecycle = 'queued'"
                " ORDER BY started_at"
            ).fetchall()
        for row in stale:
            update_run(
                self.app.db,
                row["run_id"],
                lifecycle="interrupted",
                terminal_reason="The server restarted while this run was in progress. "
                "It was not resumed. Start it again to retry; the payment gate prevents "
                "any second payment.",
                finished_at=self.app.db.now(),
            )
            EventRecorder(self.app.db, row["run_id"])(
                "error", "run_interrupted", {"reason": "server restart"}
            )
        for row in queued:
            if row["document_id"]:
                self._queue.put(
                    Job(
                        row["run_id"],
                        row["workspace_id"],
                        row["document_id"],
                        row["mode"] or "mock",
                    )
                )

    # ------------------------------------------------------------------ submitting

    def pending(self) -> int:
        with self.app.db.read() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM runs WHERE lifecycle IN ('queued','running')"
            ).fetchone()[0]

    def submit(self, *, workspace_id: str, document_id: str, mode: str) -> str:
        """Create a durable queued run and wake a worker. Raises :class:`QueueFull` at capacity."""
        if self.pending() >= self.app.config.queue_capacity:
            raise QueueFull(f"The queue is full ({self.app.config.queue_capacity} runs).")
        with self.app.db.transaction() as conn:
            ensure_workspace(
                conn,
                workspace_id,
                "local" if workspace_id == "local" else "session",
                self.app.db.now(),
            )
            run_id = create_run(
                conn,
                workspace_id=workspace_id,
                now=self.app.db.now(),
                document_id=document_id,
                mode=mode,
                policy_version=self.app.policy.policy_version,
                lifecycle="queued",
            )
        EventRecorder(self.app.db, run_id)("intake", "run_queued", {"mode": mode})
        self._queue.put(Job(run_id, workspace_id, document_id, mode))
        return run_id

    def cancel(self, run_id: str) -> None:
        with self._lock:
            event = self._cancels.get(run_id)
        if event is not None:
            event.set()

    # ------------------------------------------------------------------ worker

    def _work(self) -> None:
        while not self._stopping.is_set():
            job = self._queue.get()
            if job is None:
                return
            cancel = threading.Event()
            with self._lock:
                self._cancels[job.run_id] = cancel
            try:
                self._run(job, cancel)
            except Exception as exc:  # noqa: BLE001 - the worker must survive any one run
                update_run(
                    self.app.db,
                    job.run_id,
                    lifecycle="failed",
                    terminal_reason=f"Unexpected error ({type(exc).__name__}).",
                    finished_at=self.app.db.now(),
                )
            finally:
                with self._lock:
                    self._cancels.pop(job.run_id, None)

    def _run(self, job: Job, cancel: threading.Event) -> None:
        with self.app.db.read() as conn:
            row = conn.execute(
                "SELECT lifecycle FROM runs WHERE run_id = ?", (job.run_id,)
            ).fetchone()
            source = load_document(conn, job.workspace_id, job.document_id)
        if row is None or row["lifecycle"] != "queued":
            return  # already handled (for example reset or interrupted)
        if source is None:
            update_run(
                self.app.db,
                job.run_id,
                lifecycle="failed",
                terminal_reason="The document is no longer available. (document_missing)",
                finished_at=self.app.db.now(),
            )
            return
        execute_run(
            self.app,
            workspace_id=job.workspace_id,
            source=source,
            mode=job.mode,
            run_id=job.run_id,
            cancel=cancel,
        )

    # ------------------------------------------------------------------ reading

    def result(self, workspace_id: str, run_id: str) -> RunResult | None:
        """The stored result, or a minimal live view while the run is still in progress."""
        with self.app.db.read() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE workspace_id = ? AND run_id = ?", (workspace_id, run_id)
            ).fetchone()
            filename = None
            if row is not None and row["document_id"]:
                doc = conn.execute(
                    "SELECT filename FROM documents WHERE workspace_id = ? AND document_id = ?",
                    (workspace_id, row["document_id"]),
                ).fetchone()
                filename = doc["filename"] if doc else None
        if row is None:
            return None
        if row["result_json"]:
            stored = RunResult.model_validate_json(row["result_json"])
            return stored.model_copy(
                update={
                    "lifecycle": RunLifecycle(row["lifecycle"]),
                    "outcome": Outcome(row["outcome"]) if row["outcome"] else None,
                    "terminal_reason": row["terminal_reason"],
                }
            )
        return RunResult(
            run_id=row["run_id"],
            workspace_id=row["workspace_id"],
            lifecycle=RunLifecycle(row["lifecycle"]),
            outcome=Outcome(row["outcome"]) if row["outcome"] else None,
            terminal_reason=row["terminal_reason"],
            document_id=row["document_id"],
            filename=filename,
            mode=row["mode"],
            provider=row["provider"],
            model=row["model"],
            policy_version=row["policy_version"],
        )


def usage_json(snapshot: dict) -> str:
    return json.dumps(snapshot)
