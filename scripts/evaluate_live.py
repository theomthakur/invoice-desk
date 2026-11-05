"""Run fresh, fingerprinted cases with paid inference and an aggregate spending guard.

Default pytest never runs this script. Provider-side limits remain the hard cost boundary.
The guard stops before new requests once its reservation would exhaust the local budget.
"""

import argparse
import hashlib
import json
import os
import tempfile
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx

from invoice_flow.providers import OpenRouterProvider, ProviderError
from invoice_flow.runtime import DEFAULT_MODEL, App, AppConfig
from invoice_flow.service import process_invoice

parser = argparse.ArgumentParser(
    description="Paid live evaluation against a frozen synthetic manifest."
)
parser.add_argument("--manifest", type=Path, required=True)
parser.add_argument("--output-dir", type=Path, required=True)
parser.add_argument("--budget-usd", type=Decimal, required=True)
parser.add_argument("--run-paid", action="store_true")
args = parser.parse_args()
if not args.run_paid or not Decimal("0.10") <= args.budget_usd <= Decimal("1.00"):
    parser.error("Explicit --run-paid and a budget between 0.10 and 1.00 are required.")
key = os.environ.get("OPENROUTER_API_KEY")
if not key:
    parser.error("OPENROUTER_API_KEY is required in the environment.")
manifest = json.loads(args.manifest.read_text())
if manifest.get("model") != DEFAULT_MODEL:
    parser.error("The frozen manifest model must match the configured Grok default.")
cases = manifest["cases"]
for case in cases:
    if hashlib.sha256(case["text"].encode()).hexdigest() != case["sha256"]:
        parser.error("Manifest fingerprint mismatch. Freeze expectations before running.")
out = args.output_dir
out.mkdir(parents=True, exist_ok=False)
(out / "manifest.json").write_text(json.dumps(manifest, indent=2))


def credit_usage():
    response = httpx.get(
        "https://openrouter.ai/api/v1/auth/key",
        headers={"Authorization": "Bearer " + key},
        timeout=20,
    )
    response.raise_for_status()
    return Decimal(str(response.json()["data"]["usage"]))


start = credit_usage()
total = Decimal(0)
usage_unknown = False
rows = []


class CappedProvider(OpenRouterProvider):
    def complete(self, request):
        global total, usage_unknown
        # Conservative reservation per request. Stop on incomplete usage rather than assuming zero.
        if usage_unknown:
            raise ProviderError(
                "evaluation_usage_unknown", "Evaluation stopped after missing cost."
            )
        if total + Decimal("0.10") > args.budget_usd - Decimal("0.10"):
            raise ProviderError("evaluation_cap", "Evaluation spending limit reached.")
        response = super().complete(request)
        if response.cost_usd is None:
            usage_unknown = True
            raise ProviderError(
                "evaluation_usage_unknown", "Provider cost missing; stopping evaluation."
            )
        total += Decimal(str(response.cost_usd))
        return response


sequence = cases + [c for c in cases if c["name"] in manifest["repeats"]] * 2
for i, c in enumerate(sequence):
    actual_spend = credit_usage() - start
    if usage_unknown:
        print("Stopped after missing provider cost.", flush=True)
        break
    if max(actual_spend, total) > args.budget_usd - Decimal("0.20"):
        print("Budget stop", flush=True)
        break
    with tempfile.TemporaryDirectory(prefix="invoice-fresh-") as td:
        path = Path(td) / "invoice.txt"
        path.write_text(c["text"])
        cfg = AppConfig(
            data_dir=Path(td) / "runtime",
            openrouter_api_key=key,
            model_budget_usd=float(args.budget_usd),
        )
        app = App.create(cfg, provider_factory=lambda mode: CappedProvider(key, DEFAULT_MODEL))
        app.mock_payment = lambda vendor, amount: {"status": "success"}
        begin = time.monotonic()
        result = process_invoice(path, app=app, mode="live", today=date(2026, 11, 4))
        payload = result.model_dump(mode="json")
        (out / f"{i + 1:02d}-{c['name']}.json").write_text(json.dumps(payload, indent=2))
        observed = result.outcome.value if result.outcome else None
        row = {
            "case": c["name"],
            "expected": c["expected"],
            "observed": observed,
            "lifecycle": result.lifecycle.value,
            "pass": observed == c["expected"] and result.lifecycle.value == "completed",
            "unsafe_payment": observed == "paid" and c["expected"] != "paid",
            "seconds": round(time.monotonic() - begin, 2),
            "usage": payload["usage"],
            "reason": result.terminal_reason,
        }
        rows.append(row)
        print(
            json.dumps({k: v for k, v in row.items() if k not in {"usage", "reason"}}), flush=True
        )
        (out / "summary.json").write_text(
            json.dumps(
                {
                    "model": DEFAULT_MODEL,
                    "results": rows,
                    "reported_cost_usd": str(total),
                    "account_usage_delta_usd": str(credit_usage() - start),
                },
                indent=2,
            )
        )
print(
    "Completed",
    len(rows),
    "Reported cost",
    str(total),
    "Account delta",
    str(credit_usage() - start),
    flush=True,
)
