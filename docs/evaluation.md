# Evaluation

## What has been measured

| Check | Result | Scope |
| --- | --- | --- |
| Python tests | 717 passed, 4 live tests deselected | Deterministic rules, controlled providers, API, security, workflow, CLI, conflict persistence, legacy holds, grounding, cancellation, original PDF inspection and internal department routing. |
| Frontend unit and interaction tests | 46 passed | Controlled HTTP responses; no live model. |
| Browser tests | 13 passed | Real local backend in mock mode; valid invoice, rejection, workspace isolation, mobile intake, simulated review, optional notes, persistent department handoffs, rendered original PDF pages, manual guided onboarding and saved appearance in light/dark modes. |
| Mutation checks | Historical baseline: 52/52 caught | Measured before the corrections; not rerun for the current source. Current safeguard regressions are included in the Python suite. |
| Container smoke | Historical packaging check: passed on Linux amd64, 512 MB RAM / 0.1 CPU | Non-root image, custom port, access gate, browser production cookie on loopback, invoice outcomes, native PDF preview, review, export, isolation, full batch and CLI. Peak memory 213,676,032 bytes; no OOM. |
| Supplied-file regression | 20/20 expected outcomes matched | Each file on a fresh isolated ledger, simulated reader, fixed processing date 3 Nov 2026. Known examples, not a blind set or LLM accuracy estimate. |
| Supplied batch | Prior fingerprinted source: 20 files completed: 6 paid, 8 rejected, 2 held, 3 duplicate, 1 superseded | Fresh shared ledger; USD 30,375.00 in **mock** receipts. No real payments or savings. |
| Live OpenRouter | Not run | No measured real-model accuracy, tool calling, latency or cost. |

Per-file results, flags and elapsed times for the fresh-ledger regression are in [`sample-regression.json`](../evidence/offline/sample-regression.json); the batch output is [`batch-regression.json`](../evidence/offline/batch-regression.json). Both record a source-code/policy fingerprint without requiring Git history. The fresh-ledger regression was rerun after department routing was added; the batch artifact remains a historical measurement of its recorded source. Do not sum the fresh-ledger payments: those separate runs intentionally include alternate copies and revisions. The shared-ledger batch groups them first.

The local Docker verification is recorded in [`render-container.json`](../evidence/offline/render-container.json). It predates the department-routing update and used its recorded source fingerprint, a Linux amd64 image, a 512,000,000-byte memory limit without extra swap, and 0.1 CPU. The workload was sequential with one operator. The actual hosted Render HTTPS URL, sleep/wake behavior and concurrent load remain unverified; no cloud deployment is claimed.

Re-run from the repository root:

```bash
MODEL_MODE=mock uv run python scripts/evaluate_samples.py
MODEL_MODE=mock uv run python main.py --batch=data/invoices
```

The second command uses `DATA_DIR` or `data/runtime`. Point it at a fresh directory if you need the clean-ledger batch results. Expected outcomes are stored in `tests/fixtures/sample_expectations.json` as test labels, not runtime rules.

## Selected checks

- `invoice_1007.csv`: declared USD 15,525 differs from USD 15,635 computed from subtotal and tax. The case also has stock violations.
- `invoice_1013.json`: declared USD 22,562.80 differs from USD 22,512.80 computed from subtotal and tax; summed quantities also exceed stock.
- `invoice_1012.pdf`: review is required for an unverified vendor-name change; extraction corrections are recorded separately.
- `invoice_1014.xml`: EUR is held because no FX or non-USD payment policy was supplied.
- `invoice_1004`: a batch chooses the explicit newer revision before payment; if the original was already paid, the later revision is held for reconciliation.

These labels are judgments for the supplied case, informed by the current policy. The checks do not prove a vendor committed fraud. No monetary value above is a measured business saving.

## Live evaluation and gaps

Tests marked `live` make real network calls and cost money; they are deselected by default. They require `RUN_LIVE_TESTS=1`, `OPENROUTER_API_KEY`, a positive `MODEL_BUDGET_USD` and explicit `-m live`. Configure a provider-side limit too: missing provider cost is recorded as unknown and is not included in the app's known-cost counter. No live traces are present because no live run was performed.

The positive live acceptance test now requires successful completion of the clean invoice, an inventory tool-call event, approval/critique/final decisions, and one expected mock receipt. Offline contract tests verify that provider failure and omitted tool use make that acceptance test fail. These controlled tests do not count as a live-model demonstration. Additional authored fixtures in `tests/fixtures/independent_invoices/` check manually specified fields and outcomes; they are development examples, not a blind benchmark.

A meaningful next evaluation would use independent invoices with manually verified fields and expected findings, then record extraction accuracy, unsafe mock payments, unnecessary holds/rejections, retry/failure rates, latency and reported cost across repeated real-model runs. Negative examples and model/API failures should remain in the denominator. The present known examples were used during development and cannot answer that generalization question.

## Operator workflow increment

The invoice list labels a paid outcome as Payment reported because history does not contain a receipt. The record shows Paid (simulated) only with a stored receipt. Completed is a processing lifecycle, not a payment outcome. Rejected records provide a correction-request draft; no message is sent. Activity uses deterministic labels for known events and retains every event's type and payload under Technical details. Unknown events use a generic label and retain their original details. The print view summarizes the loaded record and can be saved as PDF through the browser; it is not the supplier's original document.

Frontend lint, type check and build passed with 46 unit/interaction tests. The 13-test integrated browser suite passed, followed by targeted browser checks for print layout and the rejected-invoice draft after the final layout adjustment. Backend behavior and historic container evidence were unchanged.
