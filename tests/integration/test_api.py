"""The HTTP API, end to end: real FastAPI app, real worker thread, real SQLite, simulated reader.

Two TestClients on one app are two different browsers (separate cookie jars).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import SAMPLES
from invoice_flow.api import create_app
from invoice_flow.api.jobs import JobRunner
from invoice_flow.config import load_policy
from invoice_flow.runtime import App, AppConfig
from invoice_flow.schemas import RunResult
from invoice_flow.storage import Database, create_run, ensure_workspace
from workflow_helpers import payments

ORIGIN = "http://testserver"
SECRET = "unit-test-session-secret-0123456789"
KEY = "sk-or-v1-THISMUSTNEVERLEAKFROMTHEAPI0000"
TERMINAL = {"completed", "failed", "interrupted"}
V1 = "/api/v1"


def make_ctx(tmp_path: Path, **config) -> App:
    config.setdefault("session_secret", SECRET)
    cfg = AppConfig(data_dir=tmp_path, **config)
    ctx = App.create(
        cfg, db=Database(tmp_path / "api.db"), policy=load_policy(), samples_dir=SAMPLES
    )
    ctx.mock_payment = lambda vendor, amount: {"status": "success"}
    return ctx


class Browser:
    """One browser: a cookie jar plus the CSRF token it was given."""

    def __init__(self, api, *, login: bool = True, demo_code: str | None = None) -> None:
        self.client = TestClient(api, base_url=ORIGIN)
        self.csrf: str | None = None
        self.workspace: str | None = None
        if login:
            self.login(demo_code)

    def login(self, demo_code: str | None = None):
        headers = {"origin": ORIGIN}
        if demo_code:
            headers["x-demo-code"] = demo_code
        response = self.client.post(f"{V1}/session", headers=headers)
        if response.status_code == 200:
            self.csrf = response.json()["csrf_token"]
            self.workspace = response.json()["workspace_id"]
        return response

    def get(self, path, **kw):
        return self.client.get(f"{V1}{path}", **kw)

    def post(self, path, *, csrf: str | None = "auto", origin: str | None = ORIGIN, **kw):
        headers = dict(kw.pop("headers", {}))
        if origin:
            headers["origin"] = origin
        if csrf == "auto":
            csrf = self.csrf
        if csrf:
            headers["x-csrf-token"] = csrf
        return self.client.post(f"{V1}{path}", headers=headers, **kw)

    def start(self, sample: str, token: str | None = None, mode: str = "mock"):
        token = token or f"tok-{sample}-{time.monotonic_ns()}"
        return self.post("/runs", json={"sample_id": sample, "mode": mode, "request_token": token})

    def wait(self, run_id: str, timeout: float = 20.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            body = self.get(f"/runs/{run_id}").json()
            if body["lifecycle"] in TERMINAL:
                return body
            time.sleep(0.02)
        raise AssertionError(f"run {run_id} did not finish: {body}")

    def run(self, sample: str, **kw) -> dict:
        response = self.start(sample, **kw)
        assert response.status_code in (200, 202), response.text
        return self.wait(response.json()["run_id"])


@pytest.fixture
def ctx(tmp_path):
    return make_ctx(tmp_path)


@pytest.fixture
def api(ctx):
    app = create_app(ctx)
    with TestClient(app, base_url=ORIGIN):  # runs the lifespan: starts and stops the worker
        yield app


@pytest.fixture
def alice(api):
    return Browser(api)


@pytest.fixture
def bob(api):
    return Browser(api)


def assert_error(response, status: int, code: str | None = None):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == {
        "code",
        "message",
        "retryable",
        "request_id",
    }
    assert body["error"]["request_id"].startswith("req_")
    if code:
        assert body["error"]["code"] == code
    return body["error"]


# ====================================================================== sessions and security


def test_session_is_created_with_a_hardened_cookie(api):
    browser = Browser(api, login=False)
    response = browser.login()
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"].startswith("ws_") and body["csrf_token"]
    assert body["modes_enabled"] == ["mock"] and body["persistence"] in ("ephemeral", "persistent")
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "max-age=" in cookie


def test_hosted_session_does_not_claim_ephemeral_storage_is_persistent(tmp_path):
    api = create_app(make_ctx(tmp_path, app_env="production", storage_persistence="ephemeral"))
    response = Browser(api, login=False).login()
    assert response.status_code == 200
    assert response.json()["persistence"] == "ephemeral"
    assert "secure" in response.headers["set-cookie"].lower()


def test_persistent_storage_is_only_reported_when_explicitly_configured(tmp_path):
    api = create_app(make_ctx(tmp_path, app_env="production", storage_persistence="persistent"))
    response = Browser(api, login=False).login()
    assert response.status_code == 200
    assert response.json()["persistence"] == "persistent"


def test_each_browser_gets_its_own_workspace(alice, bob):
    assert alice.workspace != bob.workspace
    assert alice.get("/session").json()["workspace_id"] == alice.workspace
    assert alice.get("/session").json()["csrf_token"] is None  # only issued by POST /session


def test_logging_in_again_keeps_the_same_workspace(alice):
    again = alice.login()
    assert again.json()["workspace_id"] == alice.workspace


def test_requests_without_a_session_are_unauthorized(api):
    anonymous = Browser(api, login=False)
    for path in ("/session", "/samples", "/history", "/runs/run_x", "/runs/run_x/events"):
        assert_error(anonymous.get(path), 401, "no_session")
    assert_error(anonymous.post("/runs", json={}, csrf=None), 401, "no_session")


def test_a_tampered_or_foreign_cookie_is_rejected(api, alice):
    forged = Browser(api, login=False)
    forged.client.cookies.set("invoice_flow_session", "ws_forged.AAAA.BBBB")
    assert_error(forged.get("/samples"), 401, "no_session")
    other = TestClient(
        create_app(
            make_ctx(
                Path(alice.client.app.state.ctx.db.path).parent / "x",
                session_secret="a-completely-different-secret-xyz",
            )
        ),
        base_url=ORIGIN,
    )
    other.cookies.set("invoice_flow_session", alice.client.cookies.get("invoice_flow_session"))
    assert other.get(f"{V1}/samples").status_code == 401  # signed with another secret


def test_a_validly_signed_cookie_must_still_name_a_workspace(api):
    """A correct signature is not enough: the payload must be a server-issued workspace id."""
    sessions = api.state.sessions
    for payload in (
        {"w": "local"},
        {"w": "admin"},
        {"w": 123},
        {"w": None},
        {"x": "ws_a"},
        "ws_abc",
    ):
        signed = sessions._serializer.dumps(payload)
        intruder = Browser(api, login=False)
        intruder.client.cookies.set("invoice_flow_session", signed)
        assert_error(intruder.get("/samples"), 401, "no_session")
    # and the privileged local workspace can never be claimed through a session cookie
    local = Browser(api, login=False)
    local.client.cookies.set("invoice_flow_session", sessions._serializer.dumps({"w": "local"}))
    assert_error(local.get("/history"), 401, "no_session")


def test_an_expired_session_is_rejected(api, alice):
    api.state.sessions._ttl = -1  # every cookie is now older than its max age
    assert_error(alice.get("/samples"), 401, "no_session")


def test_mutations_need_a_csrf_token(alice):
    body = {"sample_id": "invoice_1006.csv", "request_token": "tok-12345678"}
    assert_error(alice.post("/runs", json=body, csrf=None), 403, "csrf_failed")
    assert_error(alice.post("/runs", json=body, csrf="not-the-token"), 403, "csrf_failed")


def test_a_csrf_token_from_another_session_does_not_work(alice, bob):
    body = {"sample_id": "invoice_1006.csv", "request_token": "tok-12345678"}
    assert_error(alice.post("/runs", json=body, csrf=bob.csrf), 403, "csrf_failed")


def test_cross_site_requests_are_refused_even_with_a_valid_token(alice):
    body = {"sample_id": "invoice_1006.csv", "request_token": "tok-12345678"}
    assert_error(alice.post("/runs", json=body, origin="https://evil.example"), 403, "bad_origin")
    assert_error(alice.post("/runs", json=body, origin=None), 403, "bad_origin")
    assert_error(
        alice.post(
            "/runs", json=body, origin=None, headers={"referer": "https://evil.example/page"}
        ),
        403,
        "bad_origin",
    )


def test_a_same_origin_referer_is_accepted_when_origin_is_absent(alice):
    body = {"sample_id": "invoice_1006.csv", "request_token": "tok-referer01"}
    response = alice.post("/runs", json=body, origin=None, headers={"referer": f"{ORIGIN}/app"})
    assert response.status_code == 202


def test_login_itself_is_origin_checked(api):
    browser = Browser(api, login=False)
    response = browser.client.post(f"{V1}/session", headers={"origin": "https://evil.example"})
    assert_error(response, 403, "bad_origin")


def test_demo_access_code_gates_login(tmp_path):
    api = create_app(make_ctx(tmp_path, demo_access_code="open-sesame"))
    with TestClient(api, base_url=ORIGIN):
        assert_error(Browser(api, login=False).login(), 401, "access_code_required")
        assert_error(Browser(api, login=False).login("wrong"), 401, "access_code_required")
        good = Browser(api, login=False)
        assert good.login("open-sesame").status_code == 200
        assert good.get("/samples").status_code == 200


def test_requests_are_rate_limited(tmp_path):
    api = create_app(make_ctx(tmp_path, rate_limit_per_minute=3))
    with TestClient(api, base_url=ORIGIN):
        browser = Browser(api)
        codes = [
            browser.start("invoice_1005.json", token=f"tok-rate-{i}-xx").status_code
            for i in range(5)
        ]
    assert 429 in codes and codes[0] == 202
    limited = browser.start("invoice_1005.json", token="tok-rate-last-xx")
    assert_error(limited, 429, "rate_limited")
    assert limited.json()["error"]["retryable"] is True


def test_security_headers_and_request_ids(alice):
    response = alice.get("/samples")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"].startswith("req_")


# ====================================================================== secrets never leave


def test_no_secret_is_ever_returned(tmp_path):
    api = create_app(
        make_ctx(
            tmp_path,
            openrouter_api_key=KEY,
            model_budget_usd=5.0,
            demo_access_code="open-sesame-code",
        )
    )
    with TestClient(api, base_url=ORIGIN):
        browser = Browser(api, demo_code="open-sesame-code")
        responses = [
            browser.get("/config"),
            browser.get("/session"),
            browser.get("/samples"),
            browser.get("/healthz"),
            browser.get("/runs/run_nope"),
            browser.get("/nope"),
            browser.client.get(f"{V1}/openapi.json"),
            browser.start("invoice_1006.csv", mode="live"),  # live is disabled for sessions
            browser.post("/runs", json={"bad": 1}),
        ]
        run = browser.run("invoice_1006.csv")
        responses += [
            browser.get(f"/runs/{run['run_id']}"),
            browser.get(f"/runs/{run['run_id']}/export"),
        ]
    blob = " ".join(r.text for r in responses) + " ".join(
        f"{k}={v}" for r in responses for k, v in r.headers.items()
    )
    for secret in (KEY, SECRET, "open-sesame-code", "THISMUSTNEVERLEAK"):
        assert secret not in blob


def test_config_is_public_and_complete(alice):
    body = alice.get("/config").json()
    assert body["approval_threshold"] == "10000.00" and body["payment_currencies"] == ["USD"]
    assert ".pdf" in body["supported_extensions"] and body["enabled_modes"] == ["mock"]
    assert body["limits"]["max_file_bytes"] == 2000000 and body["policy_version"]
    assert (
        Browser(alice.client.app, login=False).get("/config").status_code == 200
    )  # no login needed


def test_live_mode_is_refused_when_not_enabled(alice):
    error = assert_error(alice.start("invoice_1006.csv", mode="live"), 503, "mode_unavailable")
    assert "Live mode" in error["message"]


def test_live_mode_needs_a_key_and_a_cap_even_if_sessions_are_allowed(tmp_path):
    api = create_app(make_ctx(tmp_path, allow_live_in_sessions=True))
    with TestClient(api, base_url=ORIGIN):
        assert_error(Browser(api).start("invoice_1006.csv", mode="live"), 503, "mode_unavailable")


# ====================================================================== running invoices


def test_a_run_goes_from_queued_to_paid_with_a_valid_result(alice):
    response = alice.start("invoice_1006.csv")
    assert response.status_code == 202
    accepted = response.json()
    assert accepted["lifecycle"] == "queued" and accepted["already_existed"] is False
    done = alice.wait(accepted["run_id"])
    result = RunResult.model_validate(done)  # the response matches the shared contract
    assert result.outcome.value == "paid" and result.receipt.simulated is True
    assert result.provider == "simulated" and result.usage.estimated_cost_usd is None
    assert result.receipt.amount == 2750 and result.workspace_id == alice.workspace
    assert json.loads(json.dumps(done))["receipt"]["amount"] == "2750.00"  # a string, not a number


def test_text_invoice_through_the_api_uses_the_reader_and_a_tool_call(alice):
    done = alice.run("invoice_1001.txt")
    assert done["outcome"] == "paid" and done["usage"]["tool_calls"] == 1
    page = alice.get(f"/runs/{done['run_id']}/events").json()
    kinds = [e["type"] for e in page["events"]]
    assert {
        "run_queued",
        "model_request",
        "tool_call",
        "proposal",
        "critique",
        "payment_made",
    } <= set(kinds)


def test_rejections_and_holds_are_completed_runs_not_errors(alice):
    for sample, outcome in (
        ("invoice_1007.csv", "rejected"),
        ("invoice_1014.xml", "needs_review"),
        ("invoice_1009.json", "rejected"),
    ):
        done = alice.run(sample)
        assert done["lifecycle"] == "completed" and done["outcome"] == outcome
        assert done["receipt"] is None


def test_events_are_paged_by_sequence(alice):
    run = alice.run("invoice_1001.txt")
    first = alice.get(f"/runs/{run['run_id']}/events", params={"limit": 3}).json()
    assert [e["seq"] for e in first["events"]] == [1, 2, 3] and first["next_after_seq"] == 3
    second = alice.get(
        f"/runs/{run['run_id']}/events", params={"after_seq": first["next_after_seq"], "limit": 100}
    ).json()
    assert second["events"][0]["seq"] == 4
    seqs = [e["seq"] for e in first["events"] + second["events"]]
    assert seqs == list(range(1, len(seqs) + 1))
    assert second["lifecycle"] == "completed"
    empty = alice.get(f"/runs/{run['run_id']}/events", params={"after_seq": 9999}).json()
    assert empty["events"] == [] and empty["next_after_seq"] == 9999


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 201}, {"after_seq": -1}, {"limit": "x"}]
)
def test_event_paging_parameters_are_bounded(alice, params):
    run = alice.run("invoice_1006.csv")
    assert_error(alice.get(f"/runs/{run['run_id']}/events", params=params), 422, "invalid_request")


def test_a_finished_run_is_still_readable_after_reload(alice, api):
    run = alice.run("invoice_1006.csv")
    reloaded = Browser(api, login=False)
    reloaded.client.cookies.update(alice.client.cookies)  # the same browser, a new page load
    again = reloaded.get(f"/runs/{run['run_id']}").json()
    assert (
        again["outcome"] == "paid"
        and again["receipt"]["payment_id"] == run["receipt"]["payment_id"]
    )


def test_the_source_is_served_as_a_plain_text_attachment(alice):
    run = alice.run("invoice_1001.txt")
    response = alice.get(f"/runs/{run['run_id']}/source")
    assert response.status_code == 200 and "Widgets Inc." in response.text
    assert response.headers["content-type"].startswith("text/plain")
    assert "attachment" in response.headers["content-disposition"]


def test_the_export_is_a_complete_sanitized_report(alice):
    run = alice.run("invoice_1006.csv")
    export = alice.get(f"/runs/{run['run_id']}/export").json()
    assert export["run"]["run_id"] == run["run_id"] and export["policy_version"]
    assert export["events"] and export["events"][-1]["type"] == "run_finished"


def test_double_clicking_returns_the_same_run(alice, ctx):
    token = "tok-double-click-1"
    first = alice.start("invoice_1006.csv", token=token)
    second = alice.start("invoice_1006.csv", token=token)
    assert first.status_code == 202 and second.status_code == 200
    assert second.json()["run_id"] == first.json()["run_id"] and second.json()["already_existed"]
    alice.wait(first.json()["run_id"])
    assert len(payments(ctx)) == 1


def test_a_reused_token_with_a_different_request_is_a_conflict(alice):
    token = "tok-reuse-conflict"
    assert alice.start("invoice_1006.csv", token=token).status_code == 202
    assert_error(alice.start("invoice_1015.csv", token=token), 409, "token_reused")


def test_the_same_token_in_two_workspaces_does_not_collide(alice, bob):
    token = "tok-shared-by-two"
    a, b = alice.start("invoice_1006.csv", token=token), bob.start("invoice_1006.csv", token=token)
    assert a.status_code == b.status_code == 202 and a.json()["run_id"] != b.json()["run_id"]


# ====================================================================== bodies and uploads


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"request_token": "tok-12345678"},
        {"sample_id": "a", "document_id": "b", "request_token": "tok-12345678"},
        {"sample_id": "../../etc/passwd", "request_token": "tok-12345678"},
        {"document_id": "/etc/passwd", "request_token": "tok-12345678"},
        {"sample_id": "invoice_1006.csv", "request_token": "short"},
        {"sample_id": "invoice_1006.csv", "request_token": "tok-12345678", "mode": "turbo"},
        {"sample_id": "invoice_1006.csv", "request_token": "tok-12345678", "workspace_id": "ws_x"},
        {"sample_id": "invoice_1006.csv", "request_token": "tok-12345678", "approved": True},
    ],
)
def test_invalid_run_bodies_are_rejected_with_the_error_shape(alice, body):
    assert_error(alice.post("/runs", json=body), 422, "invalid_request")


def test_malformed_json_and_wrong_content_type(alice):
    response = alice.post(
        "/runs", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert_error(response, 422, "invalid_request")
    assert_error(
        alice.post(
            "/runs", content=b"x=1", headers={"content-type": "application/x-www-form-urlencoded"}
        ),
        422,
        "invalid_request",
    )


def test_unknown_samples_and_documents_are_404(alice):
    assert_error(alice.start("invoice_9999.txt"), 404, "not_found")
    body = {"document_id": "doc_doesnotexist", "request_token": "tok-12345678"}
    assert_error(alice.post("/runs", json=body), 404, "not_found")


def test_samples_are_an_allowlist(alice):
    names = {s["sample_id"] for s in alice.get("/samples").json()}
    assert "invoice_1006.csv" in names and len(names) == 20
    assert all(n.startswith("invoice_") for n in names)
    assert {s["format"] for s in alice.get("/samples").json()} == {
        "txt",
        "pdf",
        "json",
        "csv",
        "xml",
    }


def upload(browser, name: str, data: bytes, ctype: str = "application/octet-stream"):
    return browser.post("/documents", files={"file": (name, data, ctype)})


def test_upload_then_run_a_new_invoice(alice, ctx):
    data = json.dumps(
        {
            "invoice_number": "UP-1",
            "vendor": {"name": "Upload Vendor"},
            "date": "2026-10-01",
            "due_date": "2026-11-01",
            "currency": "USD",
            "line_items": [{"item": "WidgetA", "quantity": 2, "unit_price": "250.00"}],
            "subtotal": "500.00",
            "total": "500.00",
        }
    ).encode()
    response = upload(alice, "my invoice.json", data)
    assert response.status_code == 201
    info = response.json()
    assert info["filename"] == "my invoice.json" and info["format"] == "json"
    run = alice.post(
        "/runs", json={"document_id": info["document_id"], "request_token": "tok-upload-run"}
    )
    done = alice.wait(run.json()["run_id"])
    assert done["outcome"] == "paid" and done["invoice"]["vendor_name"] == "Upload Vendor"
    assert len(payments(ctx)) == 1


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("../../etc/passwd.json", "passwd.json"),
        ("..\\..\\win\\x.json", "x.json"),
        ("in<voice>.json", "in_voice_.json"),
        ("a" * 300 + ".json", None),
    ],
)
def test_hostile_upload_names_are_sanitized(alice, name, expected):
    body = json.dumps({"invoice_number": "N-1"}).encode()
    response = upload(alice, name, body)
    assert response.status_code == 201, response.text
    stored = response.json()["filename"]
    assert "/" not in stored and "\\" not in stored and ".." not in stored and len(stored) <= 130
    if expected:
        assert stored == expected


def test_upload_rejects_bad_files_with_specific_codes(alice):
    cases = [
        ("noext", b"hello", "unsupported_extension"),
        ("x.exe", b"MZ\x00", "unsupported_extension"),
        ("x.json", b"<invoice/>", "extension_mismatch"),
        ("x.pdf", b"not a pdf", "extension_mismatch"),
        ("x.txt", b"abc\x00def", "binary_content"),
        ("x.json", b'{"a": 1, "a": 2}', "duplicate_key"),
        ("x.json", b'{"total": NaN}', "non_finite_number"),
        ("x.json", b"{broken", "malformed"),
        ("x.xml", b'<!DOCTYPE a [<!ENTITY b "x">]><invoice>&b;</invoice>', "unsafe_xml"),
        ("x.txt", b"   ", "empty"),
    ]
    for name, data, code in cases:
        assert_error(upload(alice, name, data), 422, code)


def test_oversized_uploads_are_refused_without_reading_everything(alice):
    limit = alice.client.app.state.ctx.policy.limits.max_file_bytes
    assert_error(upload(alice, "big.txt", b"a" * (limit + 1)), 413, "too_large")
    assert upload(alice, "ok.json", json.dumps({"invoice_number": "A"}).encode()).status_code == 201


def test_upload_needs_csrf_and_a_session(api, alice):
    files = {"file": ("x.json", b"{}", "application/json")}
    assert_error(
        alice.client.post(f"{V1}/documents", files=files, headers={"origin": ORIGIN}),
        403,
        "csrf_failed",
    )
    assert_error(
        Browser(api, login=False).client.post(
            f"{V1}/documents", files=files, headers={"origin": ORIGIN}
        ),
        401,
        "no_session",
    )


def test_uploaded_html_or_svg_is_only_ever_served_as_text(alice):
    html = b"<html><script>alert(1)</script></html>"
    assert_error(upload(alice, "page.html", html), 422, "unsupported_extension")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert_error(upload(alice, "x.svg", svg), 422, "unsupported_extension")
    xml = b"<invoice><notes>&lt;script&gt;alert(1)&lt;/script&gt;</notes></invoice>"
    doc = upload(alice, "x.xml", xml).json()
    run = alice.post(
        "/runs", json={"document_id": doc["document_id"], "request_token": "tok-xss-test1"}
    )
    done = alice.wait(run.json()["run_id"])
    served = alice.get(f"/runs/{done['run_id']}/source")
    assert served.headers["content-type"].startswith("text/plain")


# ====================================================================== workspace isolation


def test_another_workspace_cannot_read_a_run_in_any_way(alice, bob):
    run = alice.run("invoice_1001.txt")
    rid = run["run_id"]
    for path in (
        f"/runs/{rid}",
        f"/runs/{rid}/events",
        f"/runs/{rid}/source",
        f"/runs/{rid}/export",
    ):
        assert_error(bob.get(path), 404, "not_found")
        assert alice.get(path).status_code == 200
    assert bob.get("/history").json()["items"] == []


def test_another_workspace_cannot_use_a_document_id(alice, bob):
    doc = upload(alice, "a.json", json.dumps({"invoice_number": "A-1"}).encode()).json()
    body = {"document_id": doc["document_id"], "request_token": "tok-steal-doc1"}
    assert_error(bob.post("/runs", json=body), 404, "not_found")
    assert alice.post("/runs", json={**body, "request_token": "tok-own-doc001"}).status_code == 202


def held_run(browser, sample="invoice_1012.txt"):
    run = browser.run(sample)
    assert run["outcome"] == "needs_review", run
    return run


def review_body(run, **over):
    body = {
        "version_id": run["version_id"],
        "content_hash": run["content_hash"],
        "decision": "approve",
        "comment": "Confirmed the rename with the vendor by phone.",
        "resolved_codes": ["VENDOR_IDENTITY_CHANGE"],
        "idempotency_token": f"rev-{time.monotonic_ns()}",
    }
    body.update(over)
    return body


def test_another_workspace_cannot_review_a_run(alice, bob, ctx):
    run = held_run(alice)
    assert_error(bob.post(f"/runs/{run['run_id']}/review", json=review_body(run)), 404, "not_found")
    assert payments(ctx) == []
    assert alice.get(f"/runs/{run['run_id']}").json()["outcome"] == "needs_review"


def test_reset_only_affects_the_callers_workspace(alice, bob):
    a, b = alice.run("invoice_1006.csv"), bob.run("invoice_1006.csv")
    assert alice.post("/workspace/reset").json() == {"reset": True}
    assert_error(alice.get(f"/runs/{a['run_id']}"), 404, "not_found")
    assert alice.get("/history").json()["items"] == []
    assert bob.get(f"/runs/{b['run_id']}").json()["outcome"] == "paid"  # untouched
    again = alice.run("invoice_1006.csv")  # the workspace is reusable, and may pay again
    assert again["outcome"] == "paid"


def test_reset_is_refused_while_a_run_is_active(alice, ctx):
    with ctx.db.transaction() as conn:
        ensure_workspace(conn, alice.workspace, "session", ctx.db.now())
        create_run(conn, workspace_id=alice.workspace, now=ctx.db.now(), lifecycle="running")
    assert_error(alice.post("/workspace/reset"), 409, "run_active")


def test_reset_needs_csrf(alice):
    assert_error(alice.post("/workspace/reset", csrf=None), 403, "csrf_failed")


# ====================================================================== human review


def test_review_options_are_scoped_and_describe_the_server_policy(alice, bob):
    run = held_run(alice)
    path = f"/runs/{run['run_id']}/review-options"
    assert_error(bob.get(path), 404, "not_found")
    response = alice.get(path)
    assert response.status_code == 200
    options = response.json()
    assert options["version_id"] == run["version_id"]
    assert options["content_hash"] == run["content_hash"]
    assert "VENDOR_IDENTITY_CHANGE" in options["resolvable_codes"]
    assert options["can_approve"] and options["can_reject"]


def test_review_options_do_not_offer_approval_for_unsupported_currency(alice):
    run = held_run(alice, "invoice_1014.xml")
    response = alice.get(f"/runs/{run['run_id']}/review-options")
    assert response.status_code == 200
    assert "UNSUPPORTED_CURRENCY" in response.json()["blocked_codes"]
    assert response.json()["can_approve"] is False
    assert response.json()["can_reject"] is True


def test_review_options_reject_finished_runs(alice):
    run = alice.run("invoice_1006.csv")
    assert_error(alice.get(f"/runs/{run['run_id']}/review-options"), 409, "not_reviewable")


def test_a_reviewer_resolves_a_vendor_rename_and_the_invoice_is_paid_once(alice, ctx):
    run = held_run(alice)
    body = review_body(run)
    response = alice.post(f"/runs/{run['run_id']}/review", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["accepted"] is True and result["run"]["outcome"] == "paid"
    assert result["run"]["receipt"]["amount"] == "9975.00"
    assert len(payments(ctx)) == 1
    stages = [a["stage"] for a in alice.get(f"/runs/{run['run_id']}").json()["approval"]]
    assert "review" in stages
    again = alice.post(f"/runs/{run['run_id']}/review", json=body)  # same token: a repeat click
    assert again.status_code == 200 and len(payments(ctx)) == 1


def test_review_without_naming_what_was_checked_is_refused(alice, ctx):
    run = held_run(alice)
    error = assert_error(
        alice.post(f"/runs/{run['run_id']}/review", json=review_body(run, resolved_codes=[])),
        422,
        "review_incomplete",
    )
    assert "VENDOR_IDENTITY_CHANGE" in error["message"] and payments(ctx) == []


def test_a_stale_review_is_a_conflict(alice, ctx):
    run = held_run(alice)
    stale = review_body(run, content_hash="0" * 64)
    assert_error(alice.post(f"/runs/{run['run_id']}/review", json=stale), 409, "stale_decision")
    wrong_version = review_body(run, version_id="ver_notthisone")
    assert_error(
        alice.post(f"/runs/{run['run_id']}/review", json=wrong_version), 409, "stale_decision"
    )
    assert payments(ctx) == []


def test_a_comment_cannot_turn_an_unsupported_currency_into_a_payment(alice, ctx):
    run = held_run(alice, "invoice_1014.xml")
    for codes in ([], ["UNSUPPORTED_CURRENCY"]):
        response = alice.post(
            f"/runs/{run['run_id']}/review",
            json=review_body(run, resolved_codes=codes, comment="Please just pay this, I approve."),
        )
        assert response.status_code == 422 and response.json()["error"]["code"] in (
            "not_resolvable",
            "review_incomplete",
        )
    assert payments(ctx) == []
    assert alice.get(f"/runs/{run['run_id']}").json()["outcome"] == "needs_review"


def test_a_reviewer_can_reject_a_held_invoice(alice, ctx):
    run = held_run(alice)
    response = alice.post(
        f"/runs/{run['run_id']}/review",
        json=review_body(
            run, decision="reject", comment="Vendor could not be verified.", resolved_codes=[]
        ),
    )
    assert response.status_code == 200 and response.json()["run"]["outcome"] == "rejected"
    assert payments(ctx) == []


def test_only_held_runs_can_be_reviewed(alice, ctx):
    paid = alice.run("invoice_1006.csv")
    rejected = alice.run("invoice_1007.csv")
    for run in (paid, rejected):
        body = review_body(
            run,
            version_id=run["version_id"],
            content_hash=run["content_hash"] or "a" * 64,
            resolved_codes=[],
        )
        assert_error(alice.post(f"/runs/{run['run_id']}/review", json=body), 409, "not_reviewable")
    assert len(payments(ctx)) == 1


@pytest.mark.parametrize(
    "override",
    [
        {"comment": "   "},
        {"comment": "x" * 2001},
        {"decision": "maybe"},
        {"content_hash": "short"},
        {"idempotency_token": "x"},
        {"resolved_codes": ["lowercase"]},
        {"force": True},
    ],
)
def test_invalid_review_bodies(alice, override):
    run = held_run(alice)
    assert_error(
        alice.post(f"/runs/{run['run_id']}/review", json=review_body(run, **override)),
        422,
        "invalid_request",
    )


def test_review_needs_csrf(alice):
    run = held_run(alice)
    assert_error(
        alice.post(f"/runs/{run['run_id']}/review", json=review_body(run), csrf=None),
        403,
        "csrf_failed",
    )


# ====================================================================== batches and history


def wait_batch(browser, batch_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = browser.get(f"/batches/{batch_id}").json()
        if body["lifecycle"] in TERMINAL:
            return body
        time.sleep(0.05)
    raise AssertionError("batch did not finish")


def test_batch_of_all_samples_pays_each_identity_once(alice, ctx):
    response = alice.post("/batches", json={"request_token": "tok-batch-all-1"})
    assert response.status_code == 202
    batch = wait_batch(alice, response.json()["batch_id"])
    assert batch["lifecycle"] == "completed" and len(batch["files"]) == 20
    by_file = {f["file"]: f for f in batch["files"]}
    assert by_file["invoice_1004.json"]["kind"] == "superseded"
    assert by_file["invoice_1004_revised.json"]["outcome"] == "paid"
    assert by_file["invoice_1012.pdf"]["outcome"] == "needs_review"
    assert sum(1 for f in batch["files"] if f["kind"] == "duplicate") == 3
    # 1001 + 1004 R1 + 1006 + 1010 + 1011 + 1015 = 5000 + 5940 + 2750 + 7185 + 3000 + 6500
    assert batch["paid_by_currency"] == {"USD": "30375.00"}  # one currency, each payable once
    assert len(payments(ctx)) == batch["counts"]["paid"] == 6
    again = alice.post("/batches", json={"request_token": "tok-batch-all-1"})
    assert again.status_code == 200 and again.json()["already_existed"] is True


def test_batch_validation(alice, bob):
    assert_error(
        alice.post(
            "/batches", json={"sample_ids": ["nope.txt"], "request_token": "tok-batch-bad1"}
        ),
        404,
        "not_found",
    )
    assert_error(
        alice.post("/batches", json={"mode": "live", "request_token": "tok-batch-live"}),
        503,
        "mode_unavailable",
    )
    ok = alice.post(
        "/batches", json={"sample_ids": ["invoice_1006.csv"], "request_token": "tok-batch-one1"}
    )
    assert_error(bob.get(f"/batches/{ok.json()['batch_id']}"), 404, "not_found")
    assert_error(alice.get("/batches/bat_unknown"), 404, "not_found")


def test_history_is_scoped_ordered_and_paginated(alice, bob):
    for sample in ("invoice_1006.csv", "invoice_1015.csv", "invoice_1007.csv"):
        alice.run(sample)
        time.sleep(0.01)
    bob.run("invoice_1005.json")
    page = alice.get("/history", params={"limit": 2}).json()
    assert len(page["items"]) == 2 and page["next_cursor"]
    assert page["items"][0]["filename"] == "invoice_1007.csv"  # newest first
    rest = alice.get("/history", params={"limit": 2, "cursor": page["next_cursor"]}).json()
    names = [i["filename"] for i in page["items"] + rest["items"]]
    assert names == ["invoice_1007.csv", "invoice_1015.csv", "invoice_1006.csv"]
    assert rest["next_cursor"] is None
    paid = [i for i in page["items"] + rest["items"] if i["outcome"] == "paid"]
    assert paid and all(isinstance(i["amount"], str) for i in paid)
    assert [i["filename"] for i in bob.get("/history").json()["items"]] == ["invoice_1005.json"]
    assert_error(alice.get("/history", params={"limit": 101}), 422, "invalid_request")


# ====================================================================== routing and errors


def test_unknown_api_routes_are_json_404s(alice):
    for method, path in (
        ("get", f"{V1}/nope"),
        ("get", f"{V1}/runs/x/y/z"),
        ("post", f"{V1}/nope"),
        ("delete", f"{V1}/runs/run_x"),
    ):
        response = getattr(alice.client, method)(path, headers={"origin": ORIGIN})
        assert response.status_code in (404, 405)
        assert response.headers["content-type"].startswith("application/json")
        assert "error" in response.json() and "<html" not in response.text.lower()


def test_health_and_docs(alice):
    assert alice.get("/healthz").json() == {"status": "ok"}
    schema = alice.client.get(f"{V1}/openapi.json").json()
    for path in (
        "/api/v1/runs",
        "/api/v1/runs/{run_id}/review",
        "/api/v1/batches",
        "/api/v1/session",
    ):
        assert path in schema["paths"]


def test_spa_fallback_never_masks_api_or_asset_errors(tmp_path):
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html><body>APP SHELL</body></html>")
    (static / "assets" / "app.js").write_text("console.log('app')")
    (tmp_path / "secret.txt").write_text("TOP SECRET")
    api = create_app(make_ctx(tmp_path / "db"), static_dir=static)
    with TestClient(api, base_url=ORIGIN) as client:
        assert "APP SHELL" in client.get("/").text
        assert "APP SHELL" in client.get("/runs/abc/details").text  # client-side route
        assert client.get("/assets/app.js").text == "console.log('app')"
        missing_asset = client.get("/assets/missing.js")
        assert missing_asset.status_code == 404 and "APP SHELL" not in missing_asset.text
        assert missing_asset.headers["content-type"].startswith("application/json")
        missing_api = client.get(f"{V1}/nope")
        assert missing_api.status_code == 404 and "APP SHELL" not in missing_api.text
        assert missing_api.json()["error"]["code"] == "not_found"
        for attempt in ("/%2e%2e/secret.txt", "/..%2fsecret.txt", "/assets/..%2f..%2fsecret.txt"):
            assert "TOP SECRET" not in client.get(attempt).text


# ====================================================================== queue, restart and recovery


def test_a_full_queue_is_a_429_with_a_clear_message(tmp_path):
    api = create_app(make_ctx(tmp_path, queue_capacity=1), start_worker=False)  # nothing drains it
    with TestClient(api, base_url=ORIGIN):
        browser = Browser(api)
        assert browser.start("invoice_1006.csv", token="tok-queue-aaaa").status_code == 202
        error = assert_error(
            browser.start("invoice_1015.csv", token="tok-queue-bbbb"), 429, "queue_full"
        )
    assert error["retryable"] is True and "queue is full" in error["message"]


def test_queued_runs_are_durable_and_processed_after_a_restart(tmp_path):
    ctx = make_ctx(tmp_path)
    first = create_app(ctx, start_worker=False)  # accepts the run but never executes it
    with TestClient(first, base_url=ORIGIN):
        browser = Browser(first)
        run_id = browser.start("invoice_1006.csv", token="tok-restart-queued").json()["run_id"]
        assert browser.get(f"/runs/{run_id}").json()["lifecycle"] == "queued"
    second = create_app(make_ctx(tmp_path), start_worker=True)  # "restart": same database
    with TestClient(second, base_url=ORIGIN):
        again = Browser(second, login=False)
        again.client.cookies.update(browser.client.cookies)
        again.csrf = browser.csrf
        assert again.wait(run_id)["outcome"] == "paid"


def test_a_run_in_flight_during_a_restart_is_interrupted_and_never_resumed(tmp_path):
    ctx = make_ctx(tmp_path)
    with ctx.db.transaction() as conn:
        ensure_workspace(conn, "ws_abc", "session", ctx.db.now())
        run_id = create_run(
            conn, workspace_id="ws_abc", now=ctx.db.now(), lifecycle="running", mode="mock"
        )
    runner = JobRunner(ctx)
    runner.recover()
    stored = runner.result("ws_abc", run_id)
    assert stored.lifecycle.value == "interrupted" and "restarted" in stored.terminal_reason
    assert stored.outcome is None and payments(ctx) == []
    with ctx.db.read() as conn:
        types = [
            r["type"]
            for r in conn.execute("SELECT type FROM run_events WHERE run_id = ?", (run_id,))
        ]
    assert "run_interrupted" in types


def test_a_retry_after_an_interruption_cannot_double_pay(tmp_path):
    ctx = make_ctx(tmp_path)
    api = create_app(ctx)
    with TestClient(api, base_url=ORIGIN):
        browser = Browser(api)
        paid = browser.run("invoice_1006.csv")
    # a new server instance, a new request token: the same invoice again
    api2 = create_app(make_ctx(tmp_path))
    with TestClient(api2, base_url=ORIGIN):
        again = Browser(api2, login=False)
        again.client.cookies.update(browser.client.cookies)
        again.csrf = browser.csrf
        assert again.get(f"/runs/{paid['run_id']}").json()["outcome"] == "paid"  # preserved
        second = again.run("invoice_1006.csv")
    assert second["outcome"] == "duplicate" and len(payments(ctx)) == 1


def test_simultaneous_runs_of_the_same_invoice_pay_once(tmp_path):
    ctx = make_ctx(tmp_path, max_concurrent_runs=3)
    api = create_app(ctx)
    with TestClient(api, base_url=ORIGIN):
        browser = Browser(api)
        ids, errors = [], []
        barrier = threading.Barrier(3)

        def go(i):
            try:
                barrier.wait(timeout=5)
                response = browser.start("invoice_1006.csv", token=f"tok-simul-{i}-abcd")
                ids.append(response.json()["run_id"])
            except Exception as exc:  # noqa: BLE001 - surfaced by the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=go, args=(i,)) for i in range(3)]
        [t.start() for t in threads]
        [t.join(timeout=20) for t in threads]
        assert errors == [] and len(ids) == 3
        outcomes = sorted(str(browser.wait(i)["outcome"]) for i in ids)
    assert outcomes.count("paid") == 1 and len(payments(ctx)) == 1
    assert set(outcomes) <= {"paid", "duplicate"}


def test_the_worker_survives_a_crashing_run(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.provider_factory = lambda mode: (_ for _ in ()).throw(RuntimeError("boom"))
    api = create_app(ctx)
    with TestClient(api, base_url=ORIGIN):
        browser = Browser(api)
        bad = browser.run("invoice_1006.csv")
        assert bad["lifecycle"] in ("failed",) and bad["receipt"] is None
        ctx.provider_factory = make_ctx(tmp_path / "ok").provider_factory
        good = browser.run("invoice_1015.csv")
        assert good["outcome"] == "paid"  # the next job still ran
