"""Original PDF evidence is retained separately from its extracted text."""

from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from invoice_flow.api import create_app
from tests.integration.test_api import Browser, make_ctx

PDF = Path(__file__).resolve().parents[2] / "data/invoices/invoice_1011.pdf"


def test_sample_original_pdf_and_extracted_text_are_both_available(tmp_path):
    api = create_app(make_ctx(tmp_path))
    with TestClient(api):
        browser = Browser(api)
        run = browser.run("invoice_1011.pdf")
        original = browser.get(f"/runs/{run['run_id']}/original")
        text = browser.get(f"/runs/{run['run_id']}/source")

    assert original.status_code == 200
    assert original.content == PDF.read_bytes()
    assert original.headers["content-type"] == "application/pdf"
    assert original.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in original.headers["content-disposition"]
    assert text.headers["content-type"].startswith("text/plain")
    assert text.headers["x-original-available"] == "pdf"
    assert "INV-1011" in text.text


def test_uploaded_pdf_original_survives_server_recreation(tmp_path):
    api = create_app(make_ctx(tmp_path))
    with TestClient(api):
        first = Browser(api)
        uploaded = first.post(
            "/documents", files={"file": ("invoice.pdf", PDF.read_bytes(), "application/pdf")}
        )
        assert uploaded.status_code == 201
        accepted = first.post(
            "/runs",
            json={
                "document_id": uploaded.json()["document_id"],
                "request_token": "original-pdf-test",
            },
        )
        run_id = accepted.json()["run_id"]
        first.wait(run_id)
        cookies = first.client.cookies

    restarted = create_app(make_ctx(tmp_path))
    with TestClient(restarted):
        second = Browser(restarted, login=False)
        second.client.cookies.update(cookies)
        response = second.get(f"/runs/{run_id}/original")

    assert response.status_code == 200
    assert response.content == PDF.read_bytes()


def test_original_pdf_is_scoped_to_the_owner(tmp_path):
    api = create_app(make_ctx(tmp_path))
    with TestClient(api):
        alice, bob = Browser(api), Browser(api)
        run = alice.run("invoice_1011.pdf")
        assert bob.get(f"/runs/{run['run_id']}/original").status_code == 404
        assert bob.get(f"/runs/{run['run_id']}/preview").status_code == 404
        assert alice.get(f"/runs/{run['run_id']}/original").status_code == 200


def test_text_invoice_has_no_original_pdf_endpoint(tmp_path):
    api = create_app(make_ctx(tmp_path))
    with TestClient(api):
        browser = Browser(api)
        run = browser.run("invoice_1001.txt")
        assert browser.get(f"/runs/{run['run_id']}/original").status_code == 404
        response = browser.get(f"/runs/{run['run_id']}/source")
        assert response.status_code == 200
        assert "x-original-available" not in response.headers


def test_pdf_preview_is_a_bounded_image_of_the_original(tmp_path):
    api = create_app(make_ctx(tmp_path))
    with TestClient(api):
        browser = Browser(api)
        run = browser.run("invoice_1011.pdf")
        preview = browser.get(f"/runs/{run['run_id']}/preview")
        invalid = browser.get(f"/runs/{run['run_id']}/preview", params={"page": 20})

    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(preview.content)) as image:
        assert 0 < image.width <= 1441
        assert 0 < image.height <= 1921
    assert invalid.status_code == 404
