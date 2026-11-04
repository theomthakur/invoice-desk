"""Start an isolated local image with temporary test credentials and run its HTTP/CLI smoke.

Usage: python scripts/run_container_smoke.py [image-tag]
The script owns and removes only its uniquely named test container and temporary environment file.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
IMAGE = sys.argv[1] if len(sys.argv) > 1 else "invoice-desk-part04:local"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True)


def main() -> int:
    name = "invoice-desk-smoke-" + secrets.token_hex(5)
    secret = secrets.token_urlsafe(32)
    code = secrets.token_urlsafe(16)
    with tempfile.TemporaryDirectory(prefix="invoice-container-") as directory:
        env_file = Path(directory) / "container.env"
        env_file.write_text(f"SESSION_SECRET={secret}\nDEMO_ACCESS_CODE={code}\nPORT=10042\n")
        env_file.chmod(0o600)
        started = False
        try:
            run(
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--env-file",
                str(env_file),
                "-p",
                "127.0.0.1:18777:10042",
                IMAGE,
            )
            started = True
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    if (
                        httpx.get(
                            "http://127.0.0.1:18777/api/v1/healthz", timeout=1, trust_env=False
                        ).status_code
                        == 200
                    ):
                        break
                except httpx.HTTPError:
                    time.sleep(0.25)
            else:
                raise RuntimeError("container did not become healthy")
            env = {
                **os.environ,
                "SMOKE_DEMO_CODE": code,
                "SMOKE_BASE_URL": "http://127.0.0.1:18777",
            }
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/container_smoke.py")],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            print(result.stdout.strip())
            cli = run(
                "docker",
                "exec",
                name,
                "python",
                "main.py",
                "--invoice_path=data/invoices/invoice_1001.txt",
                "--mode",
                "mock",
                "--json",
            )
            body = json.loads(cli.stdout)
            assert body["lifecycle"] == "completed" and body["outcome"] == "paid"
            assert body["receipt"]["simulated"] is True
            print(
                "Container CLI passed: sample invoice produced a simulated receipt "
                "in a separate local workspace."
            )
            print("Bound custom PORT 10042; no live provider was configured.")
        finally:
            if started:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        subprocess.CalledProcessError,
        RuntimeError,
        AssertionError,
        json.JSONDecodeError,
    ) as error:
        print(f"Container verification failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from error
