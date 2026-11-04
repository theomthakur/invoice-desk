from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from invoice_flow.api_models import (
    BatchFileResult,
    BatchResult,
    CreateBatchRequest,
    CreateRunRequest,
    ErrorResponse,
    ReviewRequest,
)
from invoice_flow.schemas import RunLifecycle, RunResult

TOKEN = "tok-12345678"
HASH = "a" * 64


def test_run_request_needs_exactly_one_source():
    assert CreateRunRequest(sample_id="invoice_1001.txt", request_token=TOKEN).mode == "mock"
    assert CreateRunRequest(document_id="doc_abc", request_token=TOKEN).document_id == "doc_abc"
    with pytest.raises(ValidationError):
        CreateRunRequest(request_token=TOKEN)
    with pytest.raises(ValidationError):
        CreateRunRequest(document_id="d", sample_id="s", request_token=TOKEN)


@pytest.mark.parametrize(
    "bad",
    ["/etc/passwd", "../../secret", "a/b", "a b", "", "x" * 81, "doc;rm -rf"],
)
def test_ids_that_look_like_paths_are_refused(bad):
    # a browser can only name a server-side document; it can never name a file path
    with pytest.raises(ValidationError):
        CreateRunRequest(document_id=bad, request_token=TOKEN)
    with pytest.raises(ValidationError):
        CreateRunRequest(sample_id=bad, request_token=TOKEN)


def test_clients_cannot_choose_the_workspace_or_pre_approve():
    for extra in ({"workspace_id": "someone-else"}, {"approved": True}, {"amount": "1.00"}):
        with pytest.raises(ValidationError):
            CreateRunRequest(sample_id="s1", request_token=TOKEN, **extra)
        with pytest.raises(ValidationError):
            ReviewRequest(
                version_id="ver_1",
                content_hash=HASH,
                decision="approve",
                comment="ok",
                idempotency_token=TOKEN,
                **extra,
            )


def test_request_token_is_required_and_bounded():
    for bad in ("short", "has space!!", "x" * 81):
        with pytest.raises(ValidationError):
            CreateRunRequest(sample_id="s1", request_token=bad)
    with pytest.raises(ValidationError):
        CreateBatchRequest()


def test_mode_is_a_closed_set():
    with pytest.raises(ValidationError):
        CreateRunRequest(sample_id="s1", request_token=TOKEN, mode="deterministic-magic")


def test_review_request_validation():
    ok = ReviewRequest(
        version_id="ver_1",
        content_hash=HASH,
        decision="approve",
        comment="  Called vendor.  ",
        resolved_codes=["VENDOR_IDENTITY_CHANGE"],
        idempotency_token=TOKEN,
    )
    assert ok.comment == "Called vendor."
    base = dict(
        version_id="ver_1",
        content_hash=HASH,
        decision="approve",
        comment="x",
        idempotency_token=TOKEN,
    )
    for override in (
        {"comment": "   "},
        {"comment": "x" * 2001},
        {"decision": "maybe"},
        {"content_hash": "not-a-hash"},
        {"resolved_codes": ["lowercase"]},
        {"resolved_codes": ["X'; DROP TABLE"]},
    ):
        with pytest.raises(ValidationError):
            ReviewRequest(**{**base, **override})


def test_batch_amounts_must_be_decimal_strings():
    ok = BatchResult(
        batch_id="b1",
        lifecycle=RunLifecycle.COMPLETED,
        files=[BatchFileResult(file="a.json", kind="process", amount="1890.00", currency="USD")],
        counts={"paid": 1},
        paid_by_currency={"USD": "1890.00", "EUR": "0.00"},
    )
    payload = json.loads(ok.model_dump_json())
    assert payload["paid_by_currency"] == {"USD": "1890.00", "EUR": "0.00"}  # never summed together
    for bad in ("12abc", "", "NaN", "1e5"):
        with pytest.raises(ValidationError):
            BatchFileResult(file="a", kind="process", amount=bad)
        with pytest.raises(ValidationError):
            BatchResult(
                batch_id="b",
                lifecycle=RunLifecycle.COMPLETED,
                files=[],
                counts={},
                paid_by_currency={"USD": bad},
            )
    with pytest.raises(ValidationError):
        BatchFileResult(file="a", kind="process", amount=12.5)  # a JSON number is not allowed


def test_error_shape_is_fixed():
    body = ErrorResponse.model_validate(
        {
            "error": {
                "code": "stale_decision",
                "message": "The invoice changed.",
                "retryable": False,
                "request_id": "req_1",
            }
        }
    )
    assert body.error.code == "stale_decision"
    with pytest.raises(ValidationError):
        ErrorResponse.model_validate({"error": {"code": "x", "message": "y"}})  # no request_id
    with pytest.raises(ValidationError):
        ErrorResponse.model_validate({"detail": "Not Found"})  # FastAPI's default shape is refused


def test_run_result_defaults_serialize_cleanly():
    result = RunResult(run_id="run_1", workspace_id="local", lifecycle=RunLifecycle.QUEUED)
    payload = json.loads(result.model_dump_json())
    assert payload["schema_version"] == "1" and payload["outcome"] is None
    assert payload["findings"] == [] and payload["usage"]["model_calls"] == 0
