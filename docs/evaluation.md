# Evaluation

## What has been measured

| Check | Result | Scope |
| --- | --- | --- |
| Python tests | 730 passed, 4 live tests deselected | Deterministic rules, controlled providers, API, security, workflow, CLI, conflict persistence, legacy holds, grounding, cancellation, original PDF inspection and internal department routing. |
| Frontend unit and interaction tests | 56 passed | Controlled HTTP responses; no live model. |
| Browser tests | 18 passed | Real local backend in mock mode; valid invoice, rejection, workspace isolation, mobile intake, simulated review, optional notes, persistent department handoffs, rendered original PDF pages, manual guided onboarding and saved appearance in light/dark modes. |
| Mutation checks | Historical baseline: 52/52 caught | Measured before the corrections; not rerun for the current source. Current safeguard regressions are included in the Python suite. |
| Container smoke | Historical packaging check: passed on Linux amd64, 512 MB RAM / 0.1 CPU | Non-root image, custom port, access gate, browser production cookie on loopback, invoice outcomes, native PDF preview, review, export, isolation, full batch and CLI. Peak memory 213,676,032 bytes; no OOM. |
| Supplied-file regression | 20/20 expected outcomes matched | Each file on a fresh isolated ledger, simulated reader, fixed processing date 3 Nov 2026. Known examples, not a blind set or LLM accuracy estimate. |
| Supplied batch | Prior fingerprinted source: 20 files completed: 6 paid, 8 rejected, 2 held, 3 duplicate, 1 superseded | Fresh shared ledger; USD 30,375.00 in **mock** receipts. No real payments or savings. |
| Live OpenRouter | One clean-invoice Grok 4.7 acceptance test passed | One run called the inventory tool and produced a simulated receipt; no broader accuracy estimate. |

Per-file results, flags and elapsed times for the fresh-ledger regression are in [`sample-regression.json`](../evidence/offline/sample-regression.json); the batch output is [`batch-regression.json`](../evidence/offline/batch-regression.json). Both record a source-code/policy fingerprint without requiring Git history. The fresh-ledger regression was rerun after invoice lookup was added; the batch artifact remains a historical measurement of its recorded source. Do not sum the fresh-ledger payments: those separate runs intentionally include alternate copies and revisions. The shared-ledger batch groups them first.

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

Tests marked `live` make real network calls and cost money; they are deselected by default. They require `RUN_LIVE_TESTS=1`, `OPENROUTER_API_KEY`, a positive `MODEL_BUDGET_USD` and explicit `-m live`. Configure a provider-side limit too: missing provider cost is recorded as unknown and is not included in the app's known-cost counter. A single live clean-invoice Grok 4.7 test was performed; its trace was kept locally outside the repository.

The positive live acceptance test now requires successful completion of the clean invoice, an inventory tool-call event, approval/critique/final decisions, and one expected mock receipt. Offline contract tests verify that provider failure and omitted tool use make that acceptance test fail. These controlled tests do not count as a live-model demonstration. Additional authored fixtures in `tests/fixtures/independent_invoices/` check manually specified fields and outcomes; they are development examples, not a blind benchmark.

A meaningful next evaluation would use independent invoices with manually verified fields and expected findings, then record extraction accuracy, unsafe mock payments, unnecessary holds/rejections, retry/failure rates, latency and reported cost across repeated real-model runs. Negative examples and model/API failures should remain in the denominator. The present known examples were used during development and cannot answer that generalization question.

## Operator workflow increment

The invoice list labels a paid outcome as Payment reported because history does not contain a receipt. The record shows Paid (simulated) only with a stored receipt. Completed is a processing lifecycle, not a payment outcome. Rejected records provide a correction-request draft; no message is sent. Activity uses deterministic labels for known events and retains every event's type and payload under Technical details. Unknown events use a generic label and retain their original details. The print view summarizes the loaded record and can be saved as PDF through the browser; it is not the supplier's original document.

Frontend lint, type check and build passed with 46 unit/interaction tests. The 13-test integrated browser suite passed, followed by targeted browser checks for print layout and the rejected-invoice draft after the final layout adjustment. Backend behavior and historic container evidence were unchanged.

## Ledger lookup and CSV report

Invoice lookup matches an exact normalized invoice number in the signed-in browser workspace. It lists separate vendors, recorded versions, version flags and linked runs, and identifies a simulated payment only when a payment row exists. It returns at most 50 matching payables and 100 versions each, with truncation explicitly labeled. Records without a usable identity are not included. This is a read-only lookup, not model-generated question answering. Tests cover workspace isolation, a paid record, a hold and distinct vendors with the same number.

The CSV report is built from the loaded authorized run. It quotes cells and prefixes formula-like input, and marks payments as simulated. It is a decision report, not the original PDF. The lookup increment passed 719 Python tests with four live tests deselected, and the known sample regression matched 20/20 in mock mode. At that increment frontend checks passed 48 tests and 14 browser tests, including ledger lookup, CSV download and print layout. The historical container and shared-ledger batch evidence were not rerun for this source.

## Linked retries and corrections

Only failed or interrupted runs can be retried. Only rejected runs can receive a corrected file. New attempts and their parent link are saved atomically with an idempotency token before the worker starts, and queued links survive worker recovery. The original source and run remain accessible. Every attempt uses the same payment gate. A correction that cannot be matched to the original payable is held with an identity flag that the demo cannot clear. Identical source bytes are rejected as unchanged. Selecting a different file does not establish that it is a valid correction.

The final local checks passed 726 Python tests (four live tests deselected), 50 frontend tests and 15 browser tests; linked navigation was rerun after the final UI adjustment. These include concurrent retry submission, cross-workspace and CSRF denial, repeated requests, recovery, preservation of the original, a correction with an unrelated identity and the one-payment rule. The 20 known samples matched expected mock outcomes. No live model was tested.

## Initial ledger overview

The overview reads all identified payables, runs, active flagged versions and simulated receipts from one workspace-scoped database snapshot. Runs are attempts and include batch extraction, retries and duplicates; they are not a count of distinct invoices. Review-run counts reflect recorded run outcomes, not currently outstanding invoices. Receipt totals use integer minor units and remain separate by currency. Unknown identities are excluded from identified-invoice counts. The CSV has the same labels and values.

This initial overview did not implement the full planned analytics. See the ledger filters increment below for subsequent work; reconciliation drill-through, processing percentiles and comprehensive version-aware business metrics remain open. Tests cover empty workspaces, workspace isolation, duplicate attempts and separate currencies. Current checks: 729 Python tests with four live tests deselected, 51 frontend tests and 16 browser tests. The overview CSV and compact sidebar were also checked in a targeted browser run after the full suite. Known mock regression: 20/20. Historical container and shared-ledger batch artifacts were not rerun.

## Multiple selection

Selection applies to loaded runs. Select all affects only the loaded rows on the current page; selected rows hidden by a filter remain selected and are counted. CSV export includes every selected loaded run and escapes spreadsheet formula-like input. Selection is in browser memory and clears when the list unmounts; it does not execute invoice actions. Tests cover filtering, clearing, removal from loaded history and formula escaping. The integrated browser check verifies the selected CSV contains both visible and hidden selected run IDs.

## Ledger filters

The overview can filter by UTC run start date, vendor substring and outcome. Filtered invoice and flag counts are distinct records linked to matching runs. A receipt is included only when its own version has a matching paid run, so a duplicate or rejection is not presented as a payment. Vendor filters exclude runs without an identifiable vendor. CSV includes the applied filters. Automated tests cover workspace isolation, date boundaries, duplicate and rejected outcomes, and invalid dates. The 20 supplied samples matched expected outcomes in mock mode; live model and hosted deployment were not tested for this increment.

## Duplicate inbox and current batch check

The inbox labels the backend duplicate outcome and has a Duplicates filter for loaded runs. It does not guess duplicates from filenames or invoice numbers. In a fresh shared ledger on 4 November 2026, all 20 supplied files completed: 6 paid, 8 rejected, 2 held, 3 duplicates and 1 superseded. The USD 30,375 total is simulated. The browser check processes the same invoice twice and checks the duplicate filter. These known samples and offline tests do not establish real-model accuracy. The four live-provider tests remain unrun.

## Initial live model comparison

On 4 November 2026, the same clean-invoice acceptance test was run once per model using OpenRouter. Grok 4.7 passed with four model calls, one inventory tool call and a mock receipt; provider-reported cost was USD 0.032896 and the test took about 46 seconds. Claude Haiku 4.5 completed and called the inventory tool but held the sample for review because its due date was past, so the test expecting a paid outcome failed; reported cost was USD 0.010708. GPT-4o-mini held it for review after source-grounding checks, before tool use; reported cost was USD 0.000477. Haiku 5.5 returned no compatible endpoint for the request. The application did not fall back silently. These are single observations on a known sample, not a reliability comparison or guarantee. Live mode requires internet and payments are simulated.
