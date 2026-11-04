# Verification status

The local application has been checked with Python 3.12.2 and Node 22.13.1. Current results:

- Python: 729 passed; four paid-provider tests deselected.
- Frontend: TypeScript, ESLint and production build passed; 51 tests passed.
- Browser: sixteen integrated Brave tests passed against an isolated local backend in mock mode, including actual PDF page rendering.
- Supplied data: all 20 original files retained unchanged; 20 predefined independent-ledger outcomes matched.
- Previously measured shared-ledger batch: six paid, eight rejected, two held, three duplicate, one superseded; USD 30,375.00 in simulated receipts.
- Source regression evidence records a source-code and policy fingerprint. No genuine model trace has been produced.
- Earlier source Render container: Linux amd64 image passed a sequential API/browser/CLI workflow with 512 MB RAM and 0.1 CPU; peak memory 213,676,032 bytes, no OOM. PDF original/preview and full sample batch were included. Evidence is in `evidence/offline/render-container.json`.

The public repository's successful CI run on commit `e6cc9d83f668991edc755162c3f3f1ea0a3762e9` predates these corrections. Local verification does not establish hosted CI for a later commit. Earlier mutation results belong to the baseline and were not rerun for this source. Container verification is local; the eventual hosted HTTPS domain and Render sleep/wake behavior still require checking after deployment.

Current safeguards include persistent conflict holds and explicit version selection, source-supported dates/revisions, nonnegative tax and derived charges, cancellation checks inside the payment transaction, local session-key persistence, stable history pagination, and original PDF byte retention with bounded page previews. Legacy unknown holds remain conservative until their sources are accounted for.

This remains a local prototype with simulated payments. Actual model accuracy, provider availability, real banking, production identity, distributed workers, hosted durability, OCR, formal accessibility conformance, and production load behavior are unverified or outside its scope.

## Structured review and department handoffs

The current local branch supports action and reason dropdowns with an optional extra note. Source confirmation, finding resolution and the server payment gate still apply to approval. Department handoffs are durable, owner-scoped internal assignments with an audit event and idempotent submission. They keep payment on hold and send no external notification. Tests cover stale hashes, invalid options, CSRF, workspace isolation, reassignment history and assignment closure after approval.

The latest checks and fresh-ledger regression include this update. Container and shared-ledger batch artifacts were not rerun for the department-routing source; their earlier fingerprints remain preserved. No hosted deployment or live-model result is claimed.

## Guided onboarding and appearance

The sidebar now has Guided walkthrough and an appearance toggle. The 11-step tour highlights real controls, waits for document choice and an explicit processing click, explains the returned result and never submits a review. It preserves existing records and can be closed or restarted. Its mobile layout leaves intake actions above the guide. This is guided onboarding for one user-selected invoice, not an automatic multi-scenario batch demonstration.

Light is the initial look regardless of the operating system. An explicit light/dark choice persists in browser storage and applies before React renders. System design and Presentation use the selected palette; original PDF pages remain unmodified. These frontend changes were checked with 43 unit/interaction tests and 13 real-browser tests, including the existing invoice, review and PDF workflows. No backend source changed in this increment.

## Sidebar resources and account visibility

System design and Presentation moved from the top bar into the sidebar. A user icon and the current demo-workspace label replace the AP badges. The account card remains visible in collapsed and mobile layouts. No identity or authentication flow changed. Local checks passed: 43 frontend tests, 13 browser tests, TypeScript, ESLint and the production build. Navigation, active state, account visibility and overflow were checked across desktop and mobile widths.

## Operator workflow increment

Added a copyable correction draft, receipt-aware outcome wording, deterministic activity labels and a browser-printable decision report. The report does not replace the original source PDF or JSON trace. There is no automatic email. Frontend lint, build and 46 tests passed; the integrated suite passed 13 tests and the two affected browser workflows were rerun after the final layout adjustment. At that stage linked retries, correction uploads, broader event descriptions, CSV and ledger lookup had not yet been implemented.

## Invoice lookup and CSV

A workspace-scoped exact invoice-number lookup lists vendor matches, versions, holds, linked runs and whether a simulated payment is recorded. There is no model involved. Results have explicit 50-payable and 100-version limits and omit records without usable identity. The CSV decision report escapes formula-like values and remains separate from the original PDF and JSON trace. Current checks: 719 Python tests with four live tests deselected, 48 frontend tests and 14 browser tests. The 20 supplied cases matched expected outcomes in mock mode.

At that stage linked corrections, retries, broader event summaries and the initial overview had not yet been implemented. No live-model or hosted deployment was verified in this increment.

## Linked retries and corrections

The API stores the parent link and request token atomically before enqueueing an explicit retry or correction. The local worker restores the link when recovering a queued run. Failed/interrupted runs can be retried; rejected runs can receive a different uploaded file. The original remains available and the linked record can be opened. An unmatched correction identity is held and cannot be approved through the demo. The normal payment gate still enforces one simulated payment per payable.

Final checks: 726 Python tests, four live tests deselected, 50 frontend tests and 15 browser checks. A concurrent same-token retry creates one attempt; an already-paid interrupted run cannot pay again. A fresh mock regression matched 20/20 known cases. Live inference and hosted deployment remain unverified; the overview described below is a limited first analytics increment.

## Initial ledger overview

A workspace-scoped read-only overview reports distinct identified payables, run lifecycle counts, historical review-run outcomes, active identity flags and simulated receipts, with amounts separated by currency. Run counts include batch extraction, retries and duplicates. The CSV preserves those labels. This is a first increment: filters, per-metric drill-through and latency/version-aware business measures remain open. Current checks: 729 Python tests (four live tests deselected), 51 frontend tests and 16 browser tests, plus a targeted overview CSV/sidebar check. The known mock sample regression matched 20/20. No live model, paid hosting or external payment was used.
