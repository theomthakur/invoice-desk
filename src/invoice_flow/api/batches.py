"""Single-process durable batch queue. Never replay a batch stopped mid-payment."""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from uuid import uuid4

from ..runtime import App
from ..storage import claim_request_token, find_request_token
from .jobs import QueueFull, RetryError


class BatchRunner:
    def __init__(self, app: App, process: Callable):
        self.app, self.process = app, process
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self.app.db.transaction() as conn:
            conn.execute(
                "UPDATE batches SET lifecycle='interrupted',finished_at=?,result_json=? "
                "WHERE lifecycle='running'",
                (
                    self.app.db.now(),
                    json.dumps(
                        {
                            "error": "Processing stopped during a server restart. "
                            "Start a new sample batch to recheck the files; "
                            "existing receipts remain protected."
                        }
                    ),
                ),
            )
            queued = conn.execute(
                "SELECT batch_id FROM batches WHERE lifecycle='queued' ORDER BY created_at,batch_id"
            ).fetchall()
        for row in queued:
            self._queue.put(row["batch_id"])
        self._thread = threading.Thread(target=self._work, name="invoice-batch-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._cancel.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=5)

    def submit(
        self, ws: str, names: list[str], mode: str, token: str, payload: str
    ) -> tuple[str, bool]:
        """Claim the request token and queue row in the same write transaction."""
        with self.app.db.transaction() as conn:
            existing = find_request_token(conn, ws, token)
            if existing:
                previous = conn.execute(
                    "SELECT payload_hash FROM token_payloads WHERE workspace_id=? AND token=?",
                    (ws, token),
                ).fetchone()
                if previous and previous["payload_hash"] != payload:
                    raise RetryError(
                        409, "token_reused", "That token belongs to a different request."
                    )
                return existing, True
            active = conn.execute(
                "SELECT count(*) FROM batches WHERE lifecycle IN ('queued','running')"
            ).fetchone()[0]
            if active >= self.app.config.queue_capacity:
                raise QueueFull("The batch queue is full. Try again after processing finishes.")
            batch_id = "bat_" + uuid4().hex[:16]
            claim_request_token(conn, ws, token, batch_id, self.app.db.now())
            conn.execute(
                "INSERT INTO token_payloads (workspace_id,token,payload_hash) VALUES (?,?,?)",
                (ws, token, payload),
            )
            conn.execute(
                "INSERT INTO batches "
                "(batch_id,workspace_id,lifecycle,mode,document_ids_json,created_at) "
                "VALUES (?,?,'queued',?,?,?)",
                (batch_id, ws, mode, json.dumps(names), self.app.db.now()),
            )
        self._queue.put(batch_id)
        return batch_id, False

    def _work(self) -> None:
        while not self._stop.is_set():
            batch_id = self._queue.get()
            if batch_id is None:
                return
            with self.app.db.transaction() as conn:
                row = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
                if row is None or row["lifecycle"] != "queued":
                    continue
                conn.execute("UPDATE batches SET lifecycle='running' WHERE batch_id=?", (batch_id,))
            try:
                report = self.process(
                    row["workspace_id"],
                    json.loads(row["document_ids_json"]),
                    row["mode"],
                    self._cancel,
                )
                lifecycle = "interrupted" if self._cancel.is_set() else "completed"
            except (
                Exception
            ) as exc:  # One failed batch must not kill the worker or echo its source.
                lifecycle = "interrupted" if self._cancel.is_set() else "failed"
                report = {
                    "error": f"Batch processing stopped ({type(exc).__name__}). "
                    "Start a new sample batch to recheck the files."
                }
            if lifecycle == "interrupted":
                report["error"] = (
                    "Processing was interrupted. Start a new sample batch to recheck the files."
                )
            with self.app.db.transaction() as conn:
                conn.execute(
                    "UPDATE batches SET lifecycle=?,result_json=?,finished_at=? "
                    "WHERE batch_id=? AND lifecycle='running'",
                    (lifecycle, json.dumps(report), self.app.db.now(), batch_id),
                )
