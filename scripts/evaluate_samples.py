"""Evaluate known case files in explicitly simulated mode, each on a fresh ledger.

This is a known regression set, not a blind model evaluation. No network, provider key or
runtime filename rules are involved. Exit nonzero if an outcome differs or a run fails.
"""

from __future__ import annotations

import json
import platform
import sys
import tempfile
import time
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_flow.runtime import App, AppConfig  # noqa: E402
from invoice_flow.service import process_invoice  # noqa: E402


def main() -> int:
    expectations = json.loads((ROOT / "tests/fixtures/sample_expectations.json").read_text())
    paths = sorted((ROOT / "data/invoices").glob("invoice_*"))
    if set(expectations) != {path.name for path in paths}:
        print("Expected case list does not match the supplied files.", file=sys.stderr)
        return 1
    rows = []
    for path in paths:
        with tempfile.TemporaryDirectory(prefix="invoice-eval-") as directory:
            app = App.create(AppConfig(data_dir=Path(directory)))
            started = time.monotonic()
            result = process_invoice(path, app=app, mode="mock", today=date(2026, 11, 3))
            actual = result.outcome.value if result.outcome else None
            codes = [finding.code.value for finding in result.findings]
            rows.append(
                {
                    "file": path.name,
                    "expected": expectations[path.name],
                    "actual": actual,
                    "lifecycle": result.lifecycle.value,
                    "match": actual == expectations[path.name],
                    "findings": codes,
                    "amount": str(result.invoice.total) if result.invoice else None,
                    "currency": result.invoice.currency if result.invoice else None,
                    "mock_payment": result.receipt is not None,
                    "model_requests": result.usage.model_calls,
                    "tool_calls": result.usage.tool_calls,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }
            )
    out = {
        "description": "Known supplied cases, simulated reader, fresh ledger for each file",
        "provider": "simulated; not an LLM",
        "python": platform.python_version(),
        "evaluation_date": "2026-11-03",
        "calendar_assumption": "2026-11-03",
        "commit": None,
        "commit_note": "Uncommitted working tree at evaluation time; see release handoff.",
        "cases": len(rows),
        "matched": sum(row["match"] for row in rows),
        "outcome_counts": dict(Counter(str(row["actual"]) for row in rows)),
        "cost_usd": None,
        "cases_detail": rows,
    }
    output = ROOT / "evidence/offline/sample-regression.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(out, indent=2) + "\n")
    for row in rows:
        print(
            f"{row['file']:28} expected={row['expected']:12} actual={str(row['actual']):12} "
            f"{'PASS' if row['match'] else 'FAIL'}"
        )
    print(f"{out['matched']}/{out['cases']} expected outcomes matched; {output.relative_to(ROOT)}")
    return 0 if all(row["match"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
