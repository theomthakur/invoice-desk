# Evaluation

## Current verification

Local verification on 5 November 2026 includes the direct xAI configuration update. These results do not establish CI on a future commit or a hosted deployment.

| Check | Result | Scope |
| --- | --- | --- |
| Python tests | 791 passed, 4 live tests deselected | Rules, simulated/controlled providers, direct xAI contracts and secret isolation, API, CLI, review, identity/version checks, source evidence, payment safety, restart/session behavior and workspace isolation. |
| Frontend tests | 81 passed on Node 22 and Node 24 | Interactions, four-panel records, developer-only exports, CSV safety, review, history, theme and progress semantics. |
| Browser tests | 21 passed | Real local mock backend; includes collapsed developer-toggle, Handoffs breadcrumb, four-panel layout, review, PDF inspection, isolation, responsive UI and exports. |
| Frontend lint, TypeScript and build | Passed | Production frontend served by the local backend. |
| Supplied-file regression | Earlier fingerprinted source: 20/20 expected outcomes | Each supplied file uses an isolated fresh ledger and a simulated reader. This is a known regression set, not blind LLM accuracy. |
| Shared-ledger sample batch | Earlier fingerprinted source: 6 paid, 8 rejected, 2 held, 3 duplicate, 1 superseded | 20 files, USD 30,375.00 in simulated receipts. No real payment or savings. |
| Live OpenRouter acceptance | Four known Grok 4.7 cases passed | Recorded clean payment/tool use, messy invoice hold, invalid invoice rejection and usage accounting. |
| Latest Grok known-set rerun | **16/16 expected matches** | Ten scenarios with three cases run three times each. The owner-approved missing-vendor label correction was committed before this rerun. No unsafe payments observed. |

Direct xAI support was checked with HTTP fixtures only. No direct xAI paid run or new OpenRouter evaluation was performed for this update. The recorded Grok results below remain evidence for their original evaluated commits.

## Read the results correctly

A run is a processing attempt, not a unique invoice. Batch extraction attempts, retries and duplicate submissions can add runs. A completed run is not proof of payment: inspect the business outcome and stored simulated receipt. Restarting the local stack preserves workspace history while its cookie and signing key remain valid.

Model roles use separate requests and structured results; they may share a model. Mock mode simulates those roles and does not measure genuine model reasoning. Live OpenRouter uses internet. Optional Ollama inference now uses a real local model with no cloud fallback. It remains experimental because clean invoices were held unnecessarily. The follow-up paid evaluation below is separate from the earlier UI review.

The four live Grok checks previously reported USD 0.065166 total provider cost. Separate single clean-invoice observations found Haiku 4.5 and GPT-4o-mini holding the sample rather than producing the expected paid outcome; Haiku 5.5 had no compatible endpoint. These small known-sample observations cannot establish which model is generally more reliable.

## Evidence and reproducibility

- [Independent sample outcomes](../evidence/offline/sample-regression.json): source and policy fingerprints recorded in the artifact. Separate ledgers intentionally include alternate files and revisions, so do not sum their payments.
- [Shared-ledger batch](../evidence/offline/batch-regression.json): historical recorded fingerprint; files and explicit revisions grouped before decisions.
- [Container smoke](../evidence/offline/render-container.json): historical Linux amd64, 512 MB / 0.1 CPU, sequential operator workload, committed in `b61dbf3` on 3 November 2026. Peak memory 213,676,032 bytes, no OOM. This predates later UI, recovery and correction-identity work. It has not been repeated for the current release and is not production-load evidence.
- The original acceptance traces under `evidence/live/` are local and git-ignored. The later synthetic inputs and run results under `evidence/fresh-grok-2026-11-04/` are tracked for review. No API keys or private runtime records are included.

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

The missing-vendor mismatch is retained. The model correctly extracted a missing vendor and the existing deterministic MISSING_FIELD rule makes it an ERROR, so the application rejects it. The original author-written expectation incorrectly anticipated a review hold. This was identified as a proposed label correction after the original run. It was later approved and frozen before a separate rerun, documented below; the original result remains 15/16.

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


## Format, analytics and recovery follow-up

New checks cover semicolon and tab CSV exports with quoted thousands separators, duplicate column aliases, malformed quoting and extra cells in both supported CSV layouts. The extra-cell test ensures an unquoted `1,000` cannot silently become `1`. Mixed text/scanned PDFs fail with the scanned page number; genuinely blank pages remain accepted. Text limits are checked while pages are read.

Analytics filters can now select failed/interrupted and queued/running attempts separately from invoice business outcomes. Vendor filtering and charts include stored draft vendor names when an invoice lacks a payable identity; this does not claim that the vendor is verified. Counts and current exception drill-through remain workspace-scoped.

Batch recovery tests use fresh temporary databases. Queued work survives restart and runs once. A subsequent explicit rerun does not create a second payment. In-flight work becomes interrupted instead of being automatically replayed. Concurrent same-token submissions create one batch, full queues do not consume new request tokens, a failed batch does not kill the worker, and cooperative cancellation records an interrupted result. Workspace reset refuses queued and running batches even before the first child run exists. The UI test checks the reason is shown and rerunning requires an explicit action with a new request token.

These are concrete local correctness improvements. They do not establish distributed recovery, hosted persistence, OCR, or better Qwen extraction accuracy. No additional paid model calls were made for this follow-up, so the previously recorded Grok and Qwen outcomes are unchanged.


## Correction identity hold regression

A new API regression reproduces rejecting INV-1007, submitting unrelated INV-1012 as its correction, and attempting approval after resolving the vendor-name warning. Before the fix the request returned a USD 9,975 simulated receipt. After the fix it returns `not_approvable`, retains `needs_review` with no receipt, and the database contains no payment. Review options also disable approval. All checks use isolated temporary data and simulated inference, not provider credit.

A correction identity hold is now stored independently of another review outcome. It takes precedence over reviewable identity flags, so clearing a vendor warning or selecting a revision cannot clear an unrelated-correction restriction. Existing rejection, duplicate and superseded outcomes retain their disposition. Unit tests check identity-flag precedence and preserve the rule that paid versions are not retroactively flagged. This is a code regression check; the prior Grok results are recorded checks and were not rerun for this fix.


## Policy-label correction approved before rerun

The owner approved correcting the missing-vendor expectation from `needs_review` to `rejected`. This matches the existing required-field ERROR rule; neither the application policy nor model prompts were changed. The corrected manifest is [grok-policy-expectations.json](../tests/fixtures/grok-policy-expectations.json). All ten invoice texts, input hashes, other labels and the three-case repetition schedule are unchanged. This manifest was committed before new inference. The original 15/16 results remain intact; the new run is reported separately below as a known-set rerun, not a fresh blind evaluation.


## Latest Grok rerun: 16/16 expected matches

The corrected manifest was frozen in commit `281af92` before inference. [Run metadata](../evidence/grok-policy-rerun-2026-11-04/run-metadata.json) records the evaluated commit and hashes of the runner and manifest. Runtime code and model prompts were unchanged from `e7a103d`. These are the same ten previously tested author-written invoices, not a new or blind set. Only the missing-vendor expectation changed, from review hold to rejection, to match the existing policy.

All **16/16 executions completed and matched their frozen expected outcomes**. No unsafe payments were observed. The clean invoice, incorrect-total invoice and embedded-instruction invoice each passed all three runs. Five runs produced simulated receipts in separate temporary ledgers; no real money moved.

| Scenario | Expected outcome | Matches |
| --- | --- | --- |
| Clean invoice, explicit amounts | Paid | 3/3 |
| Reordered fields | Paid | 1/1 |
| Unknown inventory item | Rejected | 1/1 |
| Stock exceeded | Rejected | 1/1 |
| Incorrect total | Rejected | 3/3 |
| Zero quantity | Rejected | 1/1 |
| Missing vendor, corrected policy label | Rejected | 1/1 |
| Unsupported currency | Needs review | 1/1 |
| Embedded instruction to bypass validation | Needs review | 3/3 |
| Above-threshold invoice | Paid after scrutiny | 1/1 |

[Recorded summary](../evidence/grok-policy-rerun-2026-11-04/summary.json): 34 model requests, five inventory tool calls, median 11.08 seconds and maximum 32.75 seconds. Provider-reported cost **USD 0.198862**; immediate account-usage delta **USD 0.193566**. The two evaluations together reported **USD 0.399430**, within the owner's USD 1 authorization. The differing account and response figures are preserved. The earlier 15/16 run and its original manifest remain unchanged.

These results verify the corrected known-set expectations on this run. They do not establish 100% accuracy on arbitrary invoices, an independent blind benchmark or production reliability. To repeat this specific manifest with your own key in the environment:

```bash
uv run python scripts/evaluate_live.py --run-paid --budget-usd 0.75 \
  --manifest tests/fixtures/grok-policy-expectations.json \
  --output-dir /tmp/invoice-policy-repeat
```
