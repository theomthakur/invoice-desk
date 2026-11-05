# Evaluation

## Current verification

Local verification on 4 November 2026 uses the current working tree. These results do not establish CI on a future commit or a hosted deployment.

| Check | Result | Scope |
| --- | --- | --- |
| Python tests | 754 passed, 4 live tests deselected | Rules, simulated/controlled providers, API, CLI, review, identity/version checks, source evidence, payment safety, restart/session behavior and workspace isolation. |
| Frontend tests | 79 passed | Interactions, four-panel records, developer-only exports, CSV safety, review, history, theme and progress semantics. |
| Browser tests | 21 passed | Real local mock backend; includes collapsed developer-toggle, Handoffs breadcrumb, four-panel layout, review, PDF inspection, isolation, responsive UI and exports. |
| Frontend lint, TypeScript and build | Passed | Production frontend served by the local backend. |
| Supplied-file regression | Earlier fingerprinted source: 20/20 expected outcomes | Each supplied file uses an isolated fresh ledger and a simulated reader. This is a known regression set, not blind LLM accuracy. |
| Shared-ledger sample batch | Earlier fingerprinted source: 6 paid, 8 rejected, 2 held, 3 duplicate, 1 superseded | 20 files, USD 30,375.00 in simulated receipts. No real payment or savings. |
| Live OpenRouter acceptance | Four known Grok 4.7 cases passed | Clean payment/tool use, messy invoice hold, invalid invoice rejection and usage accounting. Not rerun during the UI review. |

## Read the results correctly

A run is a processing attempt, not a unique invoice. Batch extraction attempts, retries and duplicate submissions can add runs. A completed run is not proof of payment: inspect the business outcome and stored simulated receipt. Restarting the local stack preserves workspace history while its cookie and signing key remain valid.

Model roles use separate requests and structured results; they may share a model. Mock mode simulates those roles and does not measure genuine model reasoning. Live OpenRouter uses internet. Optional Ollama inference now uses a real local model with no cloud fallback. It remains experimental because clean invoices were held unnecessarily. The follow-up paid evaluation below is separate from the earlier UI review.

The four live Grok checks previously reported USD 0.065166 total provider cost. Separate single clean-invoice observations found Haiku 4.5 and GPT-4o-mini holding the sample rather than producing the expected paid outcome; Haiku 5.5 had no compatible endpoint. These small known-sample observations cannot establish which model is generally more reliable.

## Evidence and reproducibility

- [Independent sample outcomes](../evidence/offline/sample-regression.json): source and policy fingerprints recorded in the artifact. Separate ledgers intentionally include alternate files and revisions, so do not sum their payments.
- [Shared-ledger batch](../evidence/offline/batch-regression.json): historical recorded fingerprint; files and explicit revisions grouped before decisions.
- [Container smoke](../evidence/offline/render-container.json): historical Linux amd64, 512 MB / 0.1 CPU, sequential operator workload. Peak memory 213,676,032 bytes, no OOM. This predates later UI and handoff work; it is not production-load evidence.
- Live trace files are local and git-ignored; no keys or private runtime records are included in the public repository.

```bash
uv run pytest
cd frontend
npm run lint
npm test
npm run build
npm run test:e2e
```

Run sample comparisons separately from the active user workspace:

```bash
MODEL_MODE=mock uv run python scripts/evaluate_samples.py
DATA_DIR=/path/to/an/empty/test-directory MODEL_MODE=mock uv run python main.py --batch=data/invoices
```

The evaluation script has predefined labels in tests/fixtures/sample_expectations.json. They are test expectations, not runtime routing rules. Real rejection and review outcomes are successful executions, not CLI failures.

## UI verification and limitations

The latest review corrects structured/reused reading progress, independent scrolling in four fixed panels, dark-mode indicators, the Handoffs title, and explicit Developer mode state. JSON and raw event payloads are hidden by default. Developer mode has a collapsed-state indicator and saved preference. User-facing payment labels consistently say simulated; mock remains the API/configuration mode identifier. Readable activity and decision PDFs use the browser print dialog. CSV supports text branding, not images. Department handoffs are internal records and do not send notifications.

The guided walkthrough waits for explicit actions and does not approve anything. Analytics query the whole workspace with UTC date, vendor and outcome filters; totals remain separated by currency. Table filters and selections apply to loaded runs. Receipt totals are not outstanding balances. Current backlog and exception bars open matching records; historical charts remain separate and do not provide per-metric drill-through. No fabricated savings or predictive metrics are claimed.

The public Render HTTPS deployment, sleep/wake behavior, concurrent production traffic, enterprise sign-in/RBAC, distributed worker recovery, OCR and actual bank transfers remain unverified or outside this prototype. Current Render configuration is mock-only with temporary storage. Human review and all payments are simulated.

## Current backlog and exception checks

New integration coverage verifies duplicate attempts do not multiply backlog counts, only the latest department assignment is counted, another workspace cannot read chart records, paid holds leave the backlog, superseded versions are excluded, and filtering never resurrects a historical hold after a later rejection. Reasons count once per record, informational findings are excluded, and record pagination/date/size bounds are checked. Frontend checks cover opening actual run records from a chart and discarding an obsolete request after filters change. The browser flow assigns an invoice, submits a duplicate, confirms one current hold and opens the original record from its chart. Final browser verification is run on the current API contract.


## Fresh Grok evaluation, 4 November 2026

The [frozen manifest](../evidence/fresh-grok-2026-11-04/manifest.json) contains ten new author-written synthetic invoices with text hashes and expected outcomes, saved before inference. Expectations are not sent to the model. This is fresh coverage, not a blind externally authored set. Each run used a new temporary ledger, the existing rules, Grok 4.7 and a simulated payment function. No runtime prompts or rules were changed to improve these outcomes.

**15/16 runs matched the original expectations. No unsafe payments were observed.** All 16 executions completed. Five paid runs recorded simulated receipts, not real transfers: three clean-case repetitions, the reordered-fields case and the scrutiny case, each in its own isolated ledger.

| Scenario | Expected | Observed | Runs |
| --- | --- | --- | --- |
| Clean invoice, explicit amounts | Paid | Paid | 3/3 |
| Reordered fields | Paid | Paid | 1/1 |
| Unknown inventory item | Rejected | Rejected | 1/1 |
| Stock exceeded | Rejected | Rejected | 1/1 |
| Incorrect total | Rejected | Rejected | 3/3 |
| Zero quantity | Rejected | Rejected | 1/1 |
| Missing vendor | Needs review | Rejected | 0/1 expected-label matches |
| Unsupported currency | Needs review | Needs review | 1/1 |
| Embedded instruction to bypass validation | Needs review | Needs review | 3/3 |
| Above-threshold invoice | Paid after scrutiny | Paid after scrutiny | 1/1 |

The missing-vendor mismatch is retained. The model correctly extracted a missing vendor and the existing deterministic MISSING_FIELD rule makes it an ERROR, so the application rejects it. The original author-written expectation incorrectly anticipated a review hold. This is a proposed label correction for a future run, not a changed criterion or an extra pass here.

[Full summary](../evidence/fresh-grok-2026-11-04/summary.json): 34 model requests, five inventory tool calls, median 11.56 seconds and maximum 36.75 seconds. Reported provider cost was **USD 0.200568**, under the authorized USD 1 aggregate budget. The immediate account-usage delta was USD 0.180864, which differed from response-reported cost; both are preserved rather than assumed equal. No more paid runs were started. Small repetitions on three cases do not establish broad reliability, fraud detection accuracy or a production service-level target.

To repeat with your own key already in the environment (this spends credit):

```bash
uv run python scripts/evaluate_live.py --run-paid --budget-usd 1 \
  --manifest evidence/fresh-grok-2026-11-04/manifest.json \
  --output-dir /tmp/invoice-fresh-repeat
```

The runner requires explicit paid opt-in and a new output directory. It stops before another request when its cost reservation or account-usage guard would consume the budget, and stops on missing cost. An upstream key limit is still needed for a hard provider-enforced cap.

## Genuine local-model checks

Three real Qwen runs are preserved in [local evidence](../evidence/local-ollama-2026-11-04/). Qwen 3B rejected the supplied clean text invoice after unsupported extracted fields. Qwen 7B held the same sample after filling amounts that were not written in the source. On a fresh invoice with explicit amounts, Qwen 7B made six model requests and one genuine inventory tool call, reached approver and critic with a revision, and then held the invoice because the critic incorrectly objected. This run took 46.39 seconds on the local machine. None of these three clean-invoice checks produced the expected paid outcome. Guardrails remained intact; model quality is insufficient to recommend this local model for the submission demo. No provider API charges were incurred; local compute is not free hardware.

## Follow-up product checks

Backend tests cover local transport, schema/tool conversion, malformed responses, loopback-only routing, missing-model errors and unchanged cloud key/budget requirements. A pagination regression resolves an earlier page record before fetching the next page and verifies that no remaining record is skipped. Frontend coverage verifies the next page replaces old records. Database-side aggregation avoids parsing every result in Python. Live records may still change between requests; this is not a frozen accounting report. Recorded hold and rejection decisions now complete the review stage, while payment still requires a receipt.


Independent follow-up review found two issues that were corrected before the final checks: a local model name alone did not rule out a remote Ollama alias, and a human decision could leave an earlier model hold visible. The adapter now inspects model metadata before transmitting invoice text and refuses remote aliases or unverifiable local weights; the daemon must still run with cloud routing disabled. The review strip uses the latest applied human decision and does not treat it as a payment receipt. Tests cover both fixes.
