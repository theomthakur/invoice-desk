# Tests and recorded evidence

[Live demo](https://invoice-desk-823q.onrender.com/) · [YouTube walkthrough](https://youtu.be/3wwiXjCHQoQ) · [Setup](../README.md#run-locally)

## Latest local checks: 6 November 2026

| Check | Result | Scope |
| --- | --- | --- |
| Backend | 798 passed; four paid tests excluded | Rules, provider contracts, identity/payment safety, queues, API/CLI and workspace isolation. |
| Frontend | 83 passed on Node 24 | Navigation, review, reports, analytics, live-model selection and explicit offline testing. |
| Browser | 21 passed | Real isolated backend with offline responses; invoice, review, handoff, duplicate, analytics and report flows. |
| Quality | Passed | Python lint/format, frozen OpenAPI, frontend lint, TypeScript and production build. |

Model-selection checks cover approved IDs, per-job isolation, queue recovery, batch persistence and conflicting request-token reuse. These tests use stubs, not paid inference. GitHub CI must be checked on the pushed commit separately.

A fresh-clone startup was checked on 5 November: the script installed dependencies, built the UI and served the UI, health endpoint and API docs; the default CLI produced a mock receipt. That earlier startup check was not repeated for the latest docs edit.

## Recorded sample and model results

| Check | Recorded outcome | Meaning |
| --- | --- | --- |
| [Supplied samples](../evidence/offline/sample-regression.json) | 20/20 expected outcomes | Offline regression, fresh ledger per file. |
| [Shared-ledger batch](../evidence/offline/batch-regression.json) | 6 paid, 8 rejected, 2 held, 3 duplicate, 1 superseded | Historical 20-file check; USD 30,375.00 in mock receipts. |
| Original live Grok acceptance | Four known cases passed; USD 0.065166 provider cost | Tool use, payment, rejection, hold and usage accounting. Original traces are local/ignored. |
| [First synthetic Grok run](../evidence/fresh-grok-2026-11-04/summary.json) | 15/16 expected matches; USD 0.200568 | Missing-vendor expectation was incorrectly labeled as review rather than rejection. |
| [Corrected Grok rerun](../evidence/grok-policy-rerun-2026-11-04/summary.json) | 16/16 expected matches; USD 0.198862 | Corrected expectation committed before inference. Same ten known scenarios. |
| [Local Qwen checks](../evidence/local-ollama-2026-11-04/) | No clean-invoice check produced the expected paid outcome | Genuine local inference; unnecessary rejection/holds. Experimental only. |

A run is an attempt, not a unique invoice or real transfer. Offline responses do not test model reasoning. Live Grok results are small known-case checks, not a blind benchmark or general reliability claim. Single-case Haiku 4.5 and GPT-4o mini observations held a clean sample; neither alternative has passed equivalent evaluation. Direct xAI has offline contract tests only.

## Why the Grok results changed

The original manifest expected `needs_review` for missing vendor identity. Existing policy treats it as a required-field ERROR and rejects it. The owner approved fixing that label; [the corrected manifest](../tests/fixtures/grok-policy-expectations.json) was frozen in `281af92` before new inference. Invoice texts, remaining labels and prompts/rules were unchanged. The original 15/16 record remains available. [Rerun metadata](../evidence/grok-policy-rerun-2026-11-04/run-metadata.json)

| Scenario | Corrected expected outcome | Matches |
| --- | --- | --- |
| Clean invoice | Paid | 3/3 |
| Reordered fields | Paid | 1/1 |
| Unknown inventory item | Rejected | 1/1 |
| Stock exceeded | Rejected | 1/1 |
| Incorrect total | Rejected | 3/3 |
| Zero quantity | Rejected | 1/1 |
| Missing vendor | Rejected | 1/1 |
| Unsupported currency | Needs review | 1/1 |
| Embedded bypass instruction | Needs review | 3/3 |
| Above threshold | Paid after scrutiny | 1/1 |

The rerun made 34 model requests and five inventory tool calls: median 11.08 seconds, maximum 32.75 seconds. Five runs recorded mock receipts in separate ledgers. No unsafe payments were observed. Immediate account-usage delta was USD 0.193566 versus USD 0.198862 response-reported cost; both are retained. The two synthetic evaluations reported USD 0.399430 total, within the authorized USD 1 budget.

## Reproduce

From the repository root:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python scripts/export_openapi.py --check
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
cd frontend
npx playwright install chromium
npm run test:e2e
```

For sample comparisons, use `MODEL_MODE=mock uv run python scripts/evaluate_samples.py` from the repository root. For a batch, use an empty `DATA_DIR` to avoid changing an active workspace.

Paid repetition requires your own configured key and explicit opt-in:

```bash
uv run python scripts/evaluate_live.py --run-paid --budget-usd 0.75 \
  --manifest tests/fixtures/grok-policy-expectations.json \
  --output-dir /tmp/invoice-policy-repeat
```

Use a new output directory. The runner stops on missing cost or exhausted guards; an upstream key limit is still needed.

## Important regression coverage

- **Correction identity:** reject 1007, attach unrelated 1012, resolve its vendor warning. Approval stays blocked and no receipt is created.
- **Payment:** duplicates, revisions, content/policy changes, unsupported currency, cancellation and stale human decisions cannot bypass the gate.
- **Formats:** strict CSV quoting/delimiters/aliases; mixed scanned/text PDFs report the scanned page; blank pages remain valid. No OCR.
- **Recovery:** queued jobs survive restart; in-progress jobs become interrupted; repeated tokens share work; failed batches do not stop the worker; reset refuses active work.
- **Analytics:** current holds do not multiply with retries; paid/superseded records leave backlog; workspace isolation, filters, cursors and stale-response handling are checked.

## Evidence limits

[Container evidence](../evidence/offline/render-container.json) is historical: committed in `b61dbf3`, 3 November, before later fixes. Peak memory was 213,676,032 bytes under a 512,000,000-byte limit and 0.1 CPU, without OOM. It was not repeated for the current release. Historical HTTPS availability checks do not establish production reliability.

No production load, distributed recovery, durable hosted storage, enterprise authorization, real bank settlement or accessibility conformance has been verified. No paid model evaluations were repeated for the model-selector or documentation changes.
