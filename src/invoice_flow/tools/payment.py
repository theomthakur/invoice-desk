"""The case brief's mock payment function. It has no real side effect.

The payment *gate* is ``ledger.execute_mock_payment``; this is only the simulated bank call
that the gate makes after every check has passed.
"""

from __future__ import annotations

import sys
from decimal import Decimal


def mock_payment(vendor: str, amount: Decimal) -> dict[str, str]:
    # stderr keeps stdout clean for `--json` output
    print(f"Paid {amount} to {vendor}", file=sys.stderr)
    return {"status": "success"}
