"""Run the HTTP API: ``python -m invoice_flow.server`` (or ``python main.py --serve``).

One process, one background worker, SQLite on local disk. Bind to localhost by default; a public
host needs an explicit ``--host``, a ``SESSION_SECRET`` and, for live inference, a spending cap.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .runtime import App, AppConfig

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def build_app(config: AppConfig | None = None):
    """Create the ASGI app. Serves ``frontend/dist`` when it exists (Part 03), else API only."""
    from .api import create_app

    cfg = config or AppConfig.from_env()
    ctx = App.create(cfg, samples_dir=PACKAGE_ROOT / "data" / "invoices")
    dist = PACKAGE_ROOT / "frontend" / "dist"
    return create_app(ctx, static_dir=dist if dist.is_dir() else None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="invoice_flow.server", description="Run the invoice API.")
    parser.add_argument(
        "--host", default="127.0.0.1", help="Default 127.0.0.1 (this machine only)."
    )
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    try:
        config = AppConfig.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    public = args.host not in ("127.0.0.1", "localhost", "::1")
    if public and not config.session_secret:
        print("Refusing to listen on a public address without SESSION_SECRET.", file=sys.stderr)
        return 2

    import uvicorn

    ready, why = config.live_ready()
    print(
        f"invoice-flow API on http://{args.host}:{args.port}  "
        f"(modes: {', '.join(config.modes_enabled(for_session=True))}; "
        f"live {'ready' if ready else 'off: ' + why})",
        file=sys.stderr,
    )
    uvicorn.run(build_app(config), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
