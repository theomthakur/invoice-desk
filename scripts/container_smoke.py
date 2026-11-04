"""Smoke a running local container. Uses an isolated browser workspace and mock processing.

Set SMOKE_BASE_URL and, when the container has an access gate, SMOKE_DEMO_CODE. No key or
response body containing a session token is printed. This script does not start a container.
"""

from __future__ import annotations

import os
import sys
import time
from http.cookies import SimpleCookie

import httpx

BASE = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:18777").rstrip("/")
CODE = os.environ.get("SMOKE_DEMO_CODE", "")


def login(client: httpx.Client) -> dict[str, str]:
    headers = {"Origin": BASE, "X-Demo-Code": CODE}
    response = client.post(f"{BASE}/api/v1/session", headers=headers)
    response.raise_for_status()
    session = response.json()
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    value = cookie["invoice_flow_session"].value
    return {
        "Origin": BASE,
        "X-CSRF-Token": session["csrf_token"],
        "Cookie": f"invoice_flow_session={value}",
    }


def main() -> int:
    with httpx.Client(timeout=10, trust_env=False) as client:
        assert client.get(f"{BASE}/api/v1/healthz").json() == {"status": "ok"}
        assert '<div id="root"></div>' in client.get(f"{BASE}/").text
        assert client.get(f"{BASE}/api/v1/not-a-route").status_code == 404
        if CODE:
            assert (
                client.post(f"{BASE}/api/v1/session", headers={"Origin": BASE}).status_code == 401
            )
        first = login(client)
        session = client.get(f"{BASE}/api/v1/session", headers=first).json()
        assert session["persistence"] == "ephemeral" and session["modes_enabled"] == ["mock"]
        assert len(client.get(f"{BASE}/api/v1/samples", headers=first).json()) == 20
        body = {
            "sample_id": "invoice_1006.csv",
            "mode": "mock",
            "request_token": "container-smoke-123",
        }
        response = client.post(f"{BASE}/api/v1/runs", headers=first, json=body)
        assert response.status_code == 202, response.status_code
        run_id = response.json()["run_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            run = client.get(f"{BASE}/api/v1/runs/{run_id}", headers=first).json()
            if run["lifecycle"] in ("completed", "failed", "interrupted"):
                break
            time.sleep(0.2)
        else:
            raise AssertionError("Run did not reach a terminal state.")
        assert run["outcome"] == "paid" and run["receipt"]["simulated"] is True, run["outcome"]
        assert client.get(f"{BASE}/api/v1/runs/{run_id}/source", headers=first).status_code == 200
        assert client.get(f"{BASE}/api/v1/runs/{run_id}/export", headers=first).status_code == 200
        with httpx.Client(timeout=10, trust_env=False) as other:
            second = login(other)
            assert other.get(f"{BASE}/api/v1/runs/{run_id}", headers=second).status_code == 404
            assert (
                other.get(f"{BASE}/api/v1/runs/{run_id}/export", headers=second).status_code == 404
            )
        print(
            "Container smoke passed: static app, health, access gate, ephemeral storage, "
            "20 samples, mock receipt, source/export and workspace isolation."
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, httpx.HTTPError) as error:
        print(f"Container smoke failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from error
