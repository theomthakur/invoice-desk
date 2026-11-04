"""The frozen API contract (docs/openapi.json) must match the running code."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import export_openapi  # noqa: E402

SCHEMA = json.loads((ROOT / "docs" / "openapi.json").read_text())

# The routes the shared contract promises (implementation/00-START-HERE...).
EXPECTED = {
    ("post", "/api/v1/session"),
    ("get", "/api/v1/session"),
    ("get", "/api/v1/config"),
    ("get", "/api/v1/samples"),
    ("post", "/api/v1/documents"),
    ("post", "/api/v1/runs"),
    ("post", "/api/v1/batches"),
    ("get", "/api/v1/batches/{batch_id}"),
    ("get", "/api/v1/runs/{run_id}"),
    ("post", "/api/v1/runs/{run_id}/retry"),
    ("post", "/api/v1/runs/{run_id}/correction"),
    ("get", "/api/v1/runs/{run_id}/events"),
    ("get", "/api/v1/runs/{run_id}/source"),
    ("get", "/api/v1/runs/{run_id}/original"),
    ("get", "/api/v1/runs/{run_id}/preview"),
    ("get", "/api/v1/runs/{run_id}/export"),
    ("get", "/api/v1/runs/{run_id}/review-options"),
    ("post", "/api/v1/runs/{run_id}/review"),
    ("get", "/api/v1/runs/{run_id}/routing"),
    ("post", "/api/v1/runs/{run_id}/routing"),
    ("get", "/api/v1/history"),
    ("get", "/api/v1/invoices/lookup"),
    ("post", "/api/v1/workspace/reset"),
    ("get", "/api/v1/healthz"),
}


def test_the_checked_in_contract_is_current():
    """Fails when the API changes without `python scripts/export_openapi.py` being re-run."""
    assert (ROOT / "docs" / "openapi.json").read_text() == export_openapi.current_schema(), (
        "docs/openapi.json is out of date. Run: python scripts/export_openapi.py"
    )


def test_every_promised_route_exists_and_nothing_extra_leaks():
    actual = {(m, p) for p, ops in SCHEMA["paths"].items() for m in ops}
    assert actual == EXPECTED


def test_every_error_response_uses_the_shared_error_shape():
    for path, ops in SCHEMA["paths"].items():
        for method, op in ops.items():
            for code, response in op["responses"].items():
                if code.startswith(("4", "5")):
                    ref = response["content"]["application/json"]["schema"]["$ref"]
                    assert ref.endswith("/ErrorResponse"), (method, path, code)
    assert "HTTPValidationError" not in SCHEMA["components"]["schemas"]


def test_money_is_never_a_json_number_anywhere_in_the_contract():
    offenders = []

    def walk(node, trail=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in (
                    "amount",
                    "total",
                    "subtotal",
                    "tax",
                    "shipping",
                    "unit_price",
                    "price",
                    "approval_threshold",
                ) and isinstance(value, dict):
                    kinds = {value.get("type")} | {a.get("type") for a in value.get("anyOf", [])}
                    if kinds & {"number", "integer"}:
                        offenders.append(f"{trail}/{key}")
                walk(value, f"{trail}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{trail}[{index}]")

    walk(SCHEMA["components"]["schemas"])
    assert offenders == []


def test_request_bodies_reject_unknown_fields():
    schemas = SCHEMA["components"]["schemas"]
    for name in ("CreateRunRequest", "CreateBatchRequest", "ReviewRequest"):
        assert schemas[name].get("additionalProperties") is False, name


def test_no_request_model_lets_a_client_choose_the_workspace_or_a_path():
    schemas = SCHEMA["components"]["schemas"]
    for name in ("CreateRunRequest", "CreateBatchRequest", "ReviewRequest"):
        fields = set(schemas[name]["properties"])
        assert not fields & {"workspace_id", "owner", "path", "file_path", "base_url", "url"}, name


def test_the_contract_declares_the_public_run_shape_for_the_frontend():
    run = SCHEMA["components"]["schemas"]["RunResult"]["properties"]
    for field in (
        "run_id",
        "lifecycle",
        "outcome",
        "findings",
        "approval",
        "receipt",
        "usage",
        "content_hash",
        "review_summary",
        "version_id",
        "mode",
        "provider",
        "model",
    ):
        assert field in run, field
    usage = SCHEMA["components"]["schemas"]["Usage"]["properties"]
    assert {"model_calls", "tool_calls", "usage_complete", "estimated_cost_usd"} <= set(usage)
