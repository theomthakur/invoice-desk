"""Run each sample once in mock mode on a throwaway database and print the outcomes.

Usage: python scripts/smoke_workflow.py
Uses the labeled simulated reader (no key, no network) and a temporary directory.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_flow.config import load_policy  # noqa: E402
from invoice_flow.runtime import App, AppConfig  # noqa: E402
from invoice_flow.service import process_invoice  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        config = AppConfig(data_dir=Path(tmp))
        app = App.create(config, policy=load_policy())
        for path in sorted((ROOT / "data" / "invoices").glob("invoice_*")):
            result = process_invoice(path, app=app, mode="mock", today=date(2026, 11, 3))
            usage = result.usage
            print(
                f"{path.name:28} {result.lifecycle.value:9} "
                f"{str(result.outcome and result.outcome.value):13}"
                f" calls={usage.model_calls} tools={usage.tool_calls}"
                f" {(result.terminal_reason or '')[:90]}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
