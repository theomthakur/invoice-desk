"""Write the frozen OpenAPI contract to docs/openapi.json (Part 03 builds against this file).

    python scripts/export_openapi.py          # rewrite docs/openapi.json
    python scripts/export_openapi.py --check  # exit 1 if the file is out of date

A test runs the same comparison, so an API change cannot land without the contract changing too.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
TARGET = ROOT / "docs" / "openapi.json"


def current_schema() -> str:
    from invoice_flow.api import create_app
    from invoice_flow.runtime import App, AppConfig

    with tempfile.TemporaryDirectory() as tmp:
        config = AppConfig(data_dir=Path(tmp), session_secret="openapi-export-secret-0123456789")
        app = create_app(App.create(config), start_worker=False)
        return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    text = current_schema()
    if "--check" in sys.argv:
        if not TARGET.exists() or TARGET.read_text() != text:
            print("docs/openapi.json is out of date. Run: python scripts/export_openapi.py")
            return 1
        print("docs/openapi.json is current.")
        return 0
    TARGET.write_text(text)
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
