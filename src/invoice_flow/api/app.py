"""FastAPI application: the HTTP contract under ``/api/v1``.

Every route uses the same service as the CLI. Security rules enforced here:
* the workspace comes only from the signed session cookie;
* every lookup is scoped to that workspace, so another workspace's id returns 404;
* mutations need a CSRF token and a same-origin request;
* errors always use ``{error: {code, message, retryable, request_id}}``;
* nothing secret is ever returned.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..api_models import (
    BatchAccepted,
    BatchFileResult,
    BatchResult,
    ConfigInfo,
    CreateBatchRequest,
    CreateRunRequest,
    DocumentInfo,
    ErrorResponse,
    EventsPage,
    ExportReport,
    HistoryItem,
    HistoryPage,
    LimitsInfo,
    ReviewOptions,
    ReviewRequest,
    ReviewResult,
    RunAccepted,
    SampleInfo,
    SessionInfo,
)
from ..findings import FindingCode, Severity, is_reviewer_resolvable
from ..identity import content_hash
from ..ledger import (
    LedgerError,
    conflict_selection_available,
    get_payment,
    load_document,
    load_version_state,
    store_document,
)
from ..money import format_amount
from ..parsers import SourceError, parse_bytes
from ..parsers.common import safe_filename
from ..parsers.pdf import pdf_page_count, render_pdf_page
from ..runtime import App, AppConfig
from ..schemas import RunEvent, RunLifecycle, RunResult, SourceFormat
from ..service import process_batch, submit_review
from ..storage import (
    claim_request_token,
    ensure_workspace,
    find_request_token,
    list_events,
    reset_workspace,
)
from .jobs import JobRunner, QueueFull
from .security import (
    CSRF_HEADER,
    SESSION_COOKIE,
    RateLimiter,
    SessionManager,
    local_session_secret,
    passcode_ok,
    same_origin,
)

API = "/api/v1"
PAGE_MAX = 200
HISTORY_CURSOR_MAX = 256


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


def _error_body(request: Request, code: str, message: str, retryable: bool) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "request_id": getattr(request.state, "request_id", "req_unknown"),
        }
    }


def _history_cursor(started_at: str, run_id: str) -> str:
    payload = json.dumps([started_at, run_id], separators=(",", ":")).encode("ascii")
    return "h1." + base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _history_position(cursor: str) -> tuple[str, str | None]:
    """Validate bounded composite cursors, accepting legacy UTC timestamp boundaries."""
    try:
        run_id = None
        if cursor.startswith("h1."):
            encoded = cursor[3:].encode("ascii")
            payload = json.loads(
                base64.b64decode(
                    encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True
                )
            )
            if not isinstance(payload, list) or len(payload) != 2:
                raise ValueError
            timestamp, run_id = payload
            if (
                not isinstance(run_id, str)
                or re.fullmatch(r"run_[A-Za-z0-9_-]{1,76}", run_id) is None
            ):
                raise ValueError
        else:
            timestamp = cursor
        if (
            not isinstance(timestamp, str)
            or re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})",
                timestamp,
            )
            is None
        ):
            raise ValueError
        moment = datetime.fromisoformat(timestamp)
        return moment.astimezone(UTC).isoformat(timespec="microseconds"), run_id
    except (ValueError, TypeError, UnicodeError, OverflowError, binascii.Error) as exc:
        raise ApiError(422, "invalid_request", "Invalid history cursor.") from exc


def create_app(
    app_ctx: App | None = None,
    *,
    static_dir: Path | None = None,
    start_worker: bool = True,
    extra_allowed_origins: set[str] | None = None,
) -> FastAPI:
    ctx = app_ctx or App.create()
    config: AppConfig = ctx.config
    secret = config.session_secret
    if not secret:
        if config.app_env == "production":
            raise RuntimeError("SESSION_SECRET is required in production.")
        secret = local_session_secret(config.data_dir)
    sessions = SessionManager(secret, config.session_ttl_seconds)
    limiter = RateLimiter(config.rate_limit_per_minute)
    runner = JobRunner(ctx)
    allowed_origins = extra_allowed_origins or set()
    secure_cookie = config.app_env == "production"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_worker:
            runner.start()
        yield
        runner.stop()

    api = FastAPI(
        title="invoice-flow",
        version="0.2.0",
        lifespan=lifespan,
        docs_url=f"{API}/docs",
        openapi_url=f"{API}/openapi.json",
        redoc_url=None,
        responses={
            code: {"model": ErrorResponse, "description": text}
            for code, text in {
                401: "No valid session, or a bad access code.",
                403: "Cross-site request, or a missing or invalid CSRF token.",
                404: "No such record in this workspace.",
                409: "Conflict: stale decision, reused request token, or active run.",
                413: "The upload is larger than the configured limit.",
                422: "Invalid input.",
                429: "Rate limited, or the run queue is full. Retry later.",
                503: "The requested mode is unavailable.",
            }.items()
        },
    )
    api.state.ctx, api.state.runner, api.state.sessions = ctx, runner, sessions

    # ------------------------------------------------------------------ errors

    @api.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = "req_" + uuid.uuid4().hex[:12]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @api.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse(
            _error_body(request, exc.code, exc.message, exc.retryable), status_code=exc.status
        )

    @api.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'][1:]) or 'body'}: {e['msg']}"
            for e in exc.errors()[:4]
        )
        return JSONResponse(
            _error_body(request, "invalid_request", problems, False), status_code=422
        )

    @api.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(
            _error_body(
                request,
                code,
                "Not found." if exc.status_code == 404 else str(exc.detail)[:120],
                False,
            ),
            status_code=exc.status_code,
        )

    @api.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        return JSONResponse(
            _error_body(request, "internal_error", "An unexpected error occurred.", False),
            status_code=500,
        )

    # ------------------------------------------------------------------ dependencies

    def workspace(request: Request) -> str:
        data = sessions.read(request.cookies.get(SESSION_COOKIE))
        if data is None:
            raise ApiError(401, "no_session", "Start a session first (POST /api/v1/session).")
        return data.workspace_id

    def mutation(request: Request, ws: str = Depends(workspace)) -> str:
        host = request.headers.get("host")
        if not same_origin(
            request.headers.get("origin"), request.headers.get("referer"), host, allowed_origins
        ):
            raise ApiError(403, "bad_origin", "Cross-site requests are not accepted.")
        if not sessions.csrf_ok(ws, request.headers.get(CSRF_HEADER)):
            raise ApiError(403, "csrf_failed", "Missing or invalid CSRF token.")
        if not limiter.allow(f"{ws}|mutation"):
            raise ApiError(429, "rate_limited", "Too many requests. Slow down.", retryable=True)
        return ws

    # ------------------------------------------------------------------ session / config

    def _session_info(ws: str, csrf: bool) -> SessionInfo:
        return SessionInfo(
            workspace_id=ws,
            persistence=config.storage_persistence,
            modes_enabled=config.modes_enabled(for_session=True),
            csrf_token=sessions.csrf_for(ws) if csrf else None,
        )

    @api.post(f"{API}/session", response_model=SessionInfo)
    async def create_session(request: Request, response: Response):
        host = request.headers.get("host")
        if not same_origin(
            request.headers.get("origin"), request.headers.get("referer"), host, allowed_origins
        ):
            raise ApiError(403, "bad_origin", "Cross-site requests are not accepted.")
        code_supplied = request.headers.get("x-demo-code")
        if not limiter.allow(f"session|{request.client.host if request.client else 'x'}"):
            raise ApiError(429, "rate_limited", "Too many requests. Slow down.", retryable=True)
        if not passcode_ok(code_supplied, config.demo_access_code):
            raise ApiError(401, "access_code_required", "A valid access code is required.")
        existing = sessions.read(request.cookies.get(SESSION_COOKIE))
        ws = existing.workspace_id if existing else sessions.new_workspace_id()
        with ctx.db.transaction() as conn:
            ensure_workspace(conn, ws, "session", ctx.db.now())
        response.set_cookie(
            SESSION_COOKIE,
            sessions.issue(ws),
            max_age=sessions.ttl,
            httponly=True,
            secure=secure_cookie,
            samesite="strict",
            path="/",
        )
        return _session_info(ws, csrf=True)

    @api.get(f"{API}/session", response_model=SessionInfo)
    async def get_session(ws: str = Depends(workspace)):
        return _session_info(ws, csrf=False)

    @api.get(f"{API}/config", response_model=ConfigInfo)
    async def get_config():
        p = ctx.policy
        return ConfigInfo(
            policy_version=p.policy_version,
            limits=LimitsInfo(
                max_file_bytes=p.limits.max_file_bytes,
                max_pdf_pages=p.limits.max_pdf_pages,
                max_line_items=p.limits.max_line_items,
            ),
            supported_extensions=p.limits.allowed_extensions,
            enabled_modes=config.modes_enabled(for_session=True),
            approval_threshold=format_amount(p.approval.scrutiny_threshold),
            approval_currency=p.approval.scrutiny_currency,
            payment_currencies=p.payment.auto_pay_currencies,
        )

    def _sample_files() -> dict[str, Path]:
        folder = ctx.samples_dir
        if folder is None or not folder.is_dir():
            return {}
        allowed = set(ctx.policy.limits.allowed_extensions)
        return {
            p.name: p
            for p in sorted(folder.iterdir())
            if p.is_file() and p.suffix.lower() in allowed and p.name.startswith("invoice_")
        }

    @api.get(f"{API}/samples", response_model=list[SampleInfo])
    async def samples(_: str = Depends(workspace)):
        return [
            SampleInfo(
                sample_id=name, name=name, format=SourceFormat(path.suffix.lower().lstrip("."))
            )
            for name, path in _sample_files().items()
        ]

    # ------------------------------------------------------------------ documents

    def _read_upload_name(name: str | None) -> str:
        cleaned = safe_filename(name or "upload")
        if not Path(cleaned).suffix:
            raise ApiError(422, "unsupported_extension", "The file needs an extension.")
        return cleaned

    @api.post(f"{API}/documents", response_model=DocumentInfo, status_code=201)
    async def upload(file: UploadFile = File(...), ws: str = Depends(mutation)):
        name = _read_upload_name(file.filename)
        limit = ctx.policy.limits.max_file_bytes
        data = await file.read(limit + 1)  # never read more than the limit allows
        if len(data) > limit:
            raise ApiError(413, "too_large", f"The file is larger than {limit} bytes.")
        try:
            parsed = parse_bytes(Path(name), data, ctx.policy)
        except SourceError as exc:
            raise ApiError(422, exc.code, exc.message) from exc
        with ctx.db.transaction() as conn:
            store_document(conn, ws, parsed.source, ctx.db.now())
        s = parsed.source
        return DocumentInfo(
            document_id=s.document_id, filename=s.filename, format=s.format, size_bytes=s.size_bytes
        )

    # ------------------------------------------------------------------ runs

    def _payload_hash(*parts: object) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()

    def _check_mode(mode: str) -> None:
        if mode not in config.modes_enabled(for_session=True):
            raise ApiError(
                503,
                "mode_unavailable",
                "Live mode is not available on this server."
                if mode == "live"
                else "That mode is not available.",
            )

    def _dedupe(ws: str, token: str, payload: str) -> str | None:
        """Existing run for this token, or ``None``. Same token, different payload: 409."""
        with ctx.db.read() as conn:
            existing = find_request_token(conn, ws, token)
            if existing is None:
                return None
            row = conn.execute(
                "SELECT payload_hash FROM token_payloads WHERE workspace_id = ? AND token = ?",
                (ws, token),
            ).fetchone()
        if row is not None and row["payload_hash"] != payload:
            raise ApiError(
                409, "token_reused", "That request token was already used for a different request."
            )
        return existing

    def _remember(ws: str, token: str, run_id: str, payload: str) -> str:
        with ctx.db.transaction() as conn:
            owner = claim_request_token(conn, ws, token, run_id, ctx.db.now())
            conn.execute(
                "INSERT OR IGNORE INTO token_payloads (workspace_id, token, payload_hash)"
                " VALUES (?,?,?)",
                (ws, token, payload),
            )
        return owner

    def _sample_document(ws: str, sample_id: str) -> str:
        path = _sample_files().get(sample_id)
        if path is None:
            raise ApiError(404, "not_found", "No such sample.")
        from ..parsers import load_source

        try:
            parsed = load_source(path, ctx.policy, root=path.parent)
        except SourceError as exc:
            raise ApiError(422, exc.code, exc.message) from exc
        with ctx.db.transaction() as conn:
            store_document(conn, ws, parsed.source, ctx.db.now())
        return parsed.source.document_id

    @api.post(f"{API}/runs", response_model=RunAccepted, status_code=202)
    async def create_run(body: CreateRunRequest, response: Response, ws: str = Depends(mutation)):
        payload = _payload_hash("run", body.document_id, body.sample_id, body.mode)
        existing = _dedupe(ws, body.request_token, payload)
        if existing is not None:
            response.status_code = 200
            current = runner.result(ws, existing)
            return RunAccepted(
                run_id=existing,
                already_existed=True,
                lifecycle=current.lifecycle if current else RunLifecycle.QUEUED,
            )
        _check_mode(body.mode)
        if body.mode == "live":
            try:
                ctx.check_live_allowed()
            except Exception as exc:
                raise ApiError(
                    503,
                    getattr(exc, "code", "live_unavailable"),
                    getattr(exc, "message", "Live mode is unavailable."),
                ) from exc
        document_id = body.document_id or _sample_document(ws, body.sample_id or "")
        with ctx.db.read() as conn:
            if load_document(conn, ws, document_id) is None:
                raise ApiError(404, "not_found", "No such document.")
        try:
            run_id = runner.submit(workspace_id=ws, document_id=document_id, mode=body.mode)
        except QueueFull as exc:
            raise ApiError(429, "queue_full", str(exc), retryable=True) from exc
        owner = _remember(ws, body.request_token, run_id, payload)
        if owner != run_id:  # lost a race with a parallel click: report the winner
            runner.cancel(run_id)
            response.status_code = 200
            return RunAccepted(run_id=owner, already_existed=True, lifecycle=RunLifecycle.QUEUED)
        return RunAccepted(run_id=run_id, lifecycle=RunLifecycle.QUEUED)

    @api.get(f"{API}/runs/{{run_id}}", response_model=RunResult)
    async def get_run(run_id: str, ws: str = Depends(workspace)):
        result = runner.result(ws, run_id)
        if result is None:
            raise ApiError(404, "not_found", "No such run.")
        return result

    @api.get(f"{API}/runs/{{run_id}}/events", response_model=EventsPage)
    async def get_events(
        run_id: str,
        after_seq: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=PAGE_MAX),
        ws: str = Depends(workspace),
    ):
        current = runner.result(ws, run_id)
        if current is None:
            raise ApiError(404, "not_found", "No such run.")
        with ctx.db.read() as conn:
            rows = list_events(conn, ws, run_id, after_seq=after_seq, limit=limit)
        events = [
            RunEvent(
                seq=r["seq"],
                timestamp=r["timestamp"],
                stage=r["stage"],
                type=r["type"],
                payload=r["payload"],
            )
            for r in rows
        ]
        return EventsPage(
            run_id=run_id,
            lifecycle=current.lifecycle,
            events=events,
            next_after_seq=events[-1].seq if events else after_seq,
        )

    @api.get(f"{API}/runs/{{run_id}}/source")
    async def get_source(run_id: str, ws: str = Depends(workspace)):
        result = runner.result(ws, run_id)
        if result is None or not result.document_id:
            raise ApiError(404, "not_found", "No such run.")
        with ctx.db.read() as conn:
            doc = load_document(conn, ws, result.document_id)
        if doc is None:
            raise ApiError(404, "not_found", "No such document.")
        # Always text/plain: an uploaded file is never rendered as HTML, SVG or script.
        headers = {
            "Content-Disposition": f'attachment; filename="{safe_filename(doc.filename)}.txt"'
        }
        if doc.original_pdf is not None:
            headers["X-Original-Available"] = "pdf"
        return PlainTextResponse(
            doc.text,
            headers=headers,
        )

    def _original_document(run_id: str, ws: str):
        result = runner.result(ws, run_id)
        if result is None or not result.document_id:
            raise ApiError(404, "not_found", "No such run.")
        with ctx.db.read() as conn:
            doc = load_document(conn, ws, result.document_id)
        if doc is None or doc.format is not SourceFormat.PDF or doc.original_pdf is None:
            raise ApiError(404, "not_found", "The original PDF is not available for this run.")
        if (
            len(doc.original_pdf) != doc.size_bytes
            or hashlib.sha256(doc.original_pdf).hexdigest() != doc.byte_hash
        ):
            raise ApiError(409, "source_changed", "The stored original failed its integrity check.")
        return doc

    @api.get(
        f"{API}/runs/{{run_id}}/original",
        responses={200: {"content": {"application/pdf": {}}, "description": "Original PDF bytes."}},
        response_class=Response,
    )
    async def get_original(run_id: str, ws: str = Depends(workspace)):
        doc = _original_document(run_id, ws)
        try:
            pages = pdf_page_count(doc.original_pdf)
        except SourceError as exc:
            raise ApiError(422, exc.code, exc.message) from exc
        return Response(
            doc.original_pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename(doc.filename)}"',
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "X-PDF-Pages": str(pages),
            },
        )

    @api.get(
        f"{API}/runs/{{run_id}}/preview",
        responses={
            200: {"content": {"image/png": {}}, "description": "Original PDF page preview."}
        },
        response_class=Response,
    )
    async def get_preview(
        run_id: str, page: int = Query(1, ge=1, le=20), ws: str = Depends(workspace)
    ):
        doc = _original_document(run_id, ws)
        try:
            preview = render_pdf_page(doc.original_pdf, page, ctx.policy)
        except SourceError as exc:
            raise ApiError(
                404 if exc.code == "page_not_found" else 422, exc.code, exc.message
            ) from exc
        return Response(preview, media_type="image/png")

    @api.get(f"{API}/runs/{{run_id}}/export", response_model=ExportReport)
    async def export_run(run_id: str, ws: str = Depends(workspace)):
        result = runner.result(ws, run_id)
        if result is None:
            raise ApiError(404, "not_found", "No such run.")
        with ctx.db.read() as conn:
            rows = list_events(conn, ws, run_id, after_seq=0, limit=500)
        events = [
            RunEvent(
                seq=r["seq"],
                timestamp=r["timestamp"],
                stage=r["stage"],
                type=r["type"],
                payload=r["payload"],
            )
            for r in rows
        ]
        return ExportReport(
            policy_version=ctx.policy.policy_version,
            run=result,
            events=events,
            findings=result.findings,
        )

    @api.get(f"{API}/runs/{{run_id}}/review-options", response_model=ReviewOptions)
    async def review_options(run_id: str, ws: str = Depends(workspace)):
        current = runner.result(ws, run_id)
        if current is None:
            raise ApiError(404, "not_found", "No such run.")
        if current.outcome != "needs_review":
            raise ApiError(409, "not_reviewable", "This run is not waiting for review.")
        has_version = bool(current.version_id and current.content_hash)
        findings = list(current.findings)
        can_select_conflict = False
        version_approvable = False
        if has_version:
            with ctx.db.read() as conn:
                try:
                    state = load_version_state(
                        conn, ws, current.version_id, ctx.policy, ctx.db.clock().date()
                    )
                except LedgerError:
                    state = None
                if state is not None:
                    can_select_conflict = conflict_selection_available(conn, ws, state)
                    paid = get_payment(conn, ws, state.payable_id) if state.payable_id else None
                    version_approvable = (
                        state.status == "active"
                        and (not state.identity_flag or can_select_conflict)
                        and paid is None
                        and state.report.normalized is not None
                        and content_hash(state.report.normalized) == current.content_hash
                    )
                    # Selection persists even if the bank fails. The stored run may still
                    # describe the earlier conflict; offer a normal retry for the winner.
                    if state.identity_flag != "revision_conflict":
                        findings = [f for f in findings if f.code != FindingCode.REVISION_CONFLICT]
                    findings.extend(state.report.findings)
        review_codes = {f.code for f in findings if f.severity == Severity.REVIEW}
        resolvable = {
            code
            for code in review_codes
            if (
                can_select_conflict
                if code == FindingCode.REVISION_CONFLICT
                else is_reviewer_resolvable(code)
            )
        }
        blocked = review_codes - resolvable
        has_error = any(f.severity == Severity.ERROR for f in findings)
        return ReviewOptions(
            version_id=current.version_id,
            content_hash=current.content_hash,
            resolvable_codes=sorted(code.value for code in resolvable),
            blocked_codes=sorted(code.value for code in blocked),
            can_approve=version_approvable and not blocked and not has_error,
            can_reject=has_version,
        )

    @api.post(f"{API}/runs/{{run_id}}/review", response_model=ReviewResult)
    async def review(run_id: str, body: ReviewRequest, ws: str = Depends(mutation)):
        current = runner.result(ws, run_id)
        if current is None:
            raise ApiError(404, "not_found", "No such run.")
        payload = _payload_hash(
            "review",
            run_id,
            body.version_id,
            body.decision,
            body.comment,
            sorted(body.resolved_codes),
        )
        if _dedupe(ws, body.idempotency_token, payload) is not None:
            return ReviewResult(
                run=runner.result(ws, run_id),
                accepted=True,
                message="This decision was already recorded.",
            )
        outcome = submit_review(
            ctx,
            workspace_id=ws,
            run_id=run_id,
            version_id=body.version_id,
            content_hash_seen=body.content_hash,
            decision=body.decision,
            comment=body.comment,
            resolved_codes=list(body.resolved_codes),
        )
        if not outcome.accepted:
            status = (
                409
                if outcome.code in ("stale_decision", "not_reviewable", "content_changed")
                else 422
            )
            if outcome.code == "not_found":
                status = 404
            raise ApiError(status, outcome.code or "review_refused", outcome.message)
        _remember(ws, body.idempotency_token, run_id, payload)
        return ReviewResult(run=outcome.run, accepted=True, message=outcome.message)

    # ------------------------------------------------------------------ batches and history

    @api.post(f"{API}/batches", status_code=202, response_model=BatchAccepted)
    async def create_batch(
        body: CreateBatchRequest, response: Response, ws: str = Depends(mutation)
    ):
        _check_mode(body.mode)
        samples_map = _sample_files()
        names = body.sample_ids if body.sample_ids is not None else list(samples_map)
        unknown = [n for n in names if n not in samples_map]
        if unknown:
            raise ApiError(404, "not_found", "Unknown sample in the request.")
        payload = _payload_hash("batch", sorted(names), body.mode)
        existing = _dedupe(ws, body.request_token, payload)
        batch_id = existing or "bat_" + uuid.uuid4().hex[:16]
        if existing is not None:
            response.status_code = 200
            return BatchAccepted(batch_id=batch_id, already_existed=True)
        _remember(ws, body.request_token, batch_id, payload)
        with ctx.db.transaction() as conn:
            conn.execute(
                "INSERT INTO batches (batch_id, workspace_id, lifecycle, mode, document_ids_json,"
                " created_at) VALUES (?,?,?,?,?,?)",
                (batch_id, ws, "queued", body.mode, json.dumps(names), ctx.db.now()),
            )
        import threading

        threading.Thread(
            target=_run_batch, args=(batch_id, ws, names, body.mode), daemon=True
        ).start()
        return BatchAccepted(batch_id=batch_id, already_existed=False)

    def _run_batch(batch_id: str, ws: str, names: list[str], mode: str) -> None:
        from ..storage import update_run  # noqa: F401

        files = _sample_files()
        with ctx.db.transaction() as conn:
            conn.execute("UPDATE batches SET lifecycle = 'running' WHERE batch_id = ?", (batch_id,))
        try:
            report = process_batch([files[n] for n in names], app=ctx, mode=mode, workspace_id=ws)
            lifecycle, body = "completed", json.dumps(report)
        except Exception as exc:  # noqa: BLE001 - recorded, never raised into the thread
            lifecycle, body = "failed", json.dumps({"error": type(exc).__name__})
        with ctx.db.transaction() as conn:
            conn.execute(
                "UPDATE batches SET lifecycle = ?, result_json = ?, finished_at = ?"
                " WHERE batch_id = ?",
                (lifecycle, body, ctx.db.now(), batch_id),
            )

    @api.get(f"{API}/batches/{{batch_id}}", response_model=BatchResult)
    async def get_batch(batch_id: str, ws: str = Depends(workspace)):
        with ctx.db.read() as conn:
            row = conn.execute(
                "SELECT * FROM batches WHERE workspace_id = ? AND batch_id = ?", (ws, batch_id)
            ).fetchone()
        if row is None:
            raise ApiError(404, "not_found", "No such batch.")
        report = json.loads(row["result_json"]) if row["result_json"] else {}
        files = [
            BatchFileResult(
                file=f["file"],
                kind=f["kind"],
                outcome=f.get("outcome"),
                run_id=f.get("run_id"),
                related_file=f.get("related_file"),
                reason=f.get("reason") or "",
                amount=f.get("amount"),
                currency=f.get("currency"),
            )
            for f in report.get("files", [])
        ]
        return BatchResult(
            batch_id=batch_id,
            lifecycle=RunLifecycle(row["lifecycle"]),
            files=files,
            counts=report.get("counts", {}),
            paid_by_currency=report.get("paid_by_currency", {}),
        )

    @api.get(f"{API}/history", response_model=HistoryPage)
    async def history(
        limit: int = Query(25, ge=1, le=100),
        cursor: str | None = Query(None, max_length=HISTORY_CURSOR_MAX),
        ws: str = Depends(workspace),
    ):
        sql = (
            "SELECT r.run_id, r.lifecycle, r.outcome, r.started_at, r.result_json, d.filename"
            " FROM runs r LEFT JOIN documents d ON d.workspace_id = r.workspace_id"
            " AND d.document_id = r.document_id WHERE r.workspace_id = ?"
        )
        params: list[Any] = [ws]
        if cursor is not None:
            started_at, run_id = _history_position(cursor)
            if run_id is None:  # Timestamp-only cursors emitted by older servers.
                sql += " AND r.started_at < ?"
                params.append(started_at)
            else:
                sql += " AND (r.started_at, r.run_id) < (?, ?)"
                params.extend((started_at, run_id))
        sql += " ORDER BY r.started_at DESC, r.run_id DESC LIMIT ?"
        params.append(limit + 1)
        with ctx.db.read() as conn:
            rows = conn.execute(sql, params).fetchall()
        page = rows[:limit]
        items = []
        for row in page:
            invoice = None
            if row["result_json"]:
                invoice = RunResult.model_validate_json(row["result_json"]).invoice
            items.append(
                HistoryItem(
                    run_id=row["run_id"],
                    filename=row["filename"],
                    lifecycle=RunLifecycle(row["lifecycle"]),
                    outcome=row["outcome"],
                    invoice_number=invoice.invoice_number if invoice else None,
                    vendor=invoice.vendor_name if invoice else None,
                    amount=format_amount(invoice.total) if invoice else None,
                    currency=invoice.currency if invoice else None,
                    started_at=row["started_at"],
                )
            )
        return HistoryPage(
            items=items,
            next_cursor=(
                _history_cursor(page[-1]["started_at"], page[-1]["run_id"])
                if len(rows) > limit and page
                else None
            ),
        )

    @api.post(f"{API}/workspace/reset")
    async def reset(ws: str = Depends(mutation)):
        try:
            reset_workspace(ctx.db, ws)
        except RuntimeError as exc:
            raise ApiError(409, "run_active", str(exc)) from exc
        return {"reset": True}

    @api.get(f"{API}/healthz")
    async def healthz():
        return {"status": "ok"}

    # ------------------------------------------------------------------ frontend (last)

    @api.get(f"{API}/{{rest:path}}", include_in_schema=False)
    async def api_not_found(rest: str):
        raise ApiError(404, "not_found", "No such API route.")

    if static_dir is not None and static_dir.is_dir():
        index = static_dir / "index.html"
        assets = static_dir.resolve()

        @api.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            candidate = (static_dir / path).resolve()
            if path and candidate.is_file() and assets in candidate.parents:
                return FileResponse(candidate)
            if "." in Path(path).name:  # a missing asset must be a real 404, never HTML
                raise ApiError(404, "not_found", "Not found.")
            if index.is_file():
                return FileResponse(index)
            raise ApiError(404, "not_found", "Not found.")

    return api
