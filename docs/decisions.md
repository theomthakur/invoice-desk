# Decisions and discovery record

Status: current prototype policy. Updated 3 November 2026.
Source of truth: the [Galatiq case brief](https://github.com/galatiq-ai/galatiq-case-invoices), commit `2f150152b962ccd24b35e515e56b04f673d410bc`.
This file records our assumptions. None of it comes from client interviews, and no business
figure here is measured.

## 1. The problem, in operator terms

| Question | Answer used for this build |
| --- | --- |
| Who is the user? | A fictional accounts-payable operator at Acme Corp. |
| What happens today? | Invoices arrive by email in mixed formats. Staff retype them, check them against a legacy inventory database, chase a VP by email, then pay through a bank API. The brief quotes a 30% error rate and 5-day delays. Those are scenario context. This prototype does not measure them. |
| Minimum useful outcome | Every invoice ends as exactly one of: paid, rejected with reasons, held for a person, duplicate, or superseded. The reason is always visible. |
| Who reviews exceptions? | A simulated human reviewer (labelled as such). Real authentication is out of scope. |
| Which inputs are trusted? | Only the inventory database and `config/policy.yaml`. Invoice text is untrusted data. Wording inside an invoice can never approve, pay, or change a rule. |
| What is mocked? | The bank (`mock_payment`), the VP, and the inventory database. |
| What stops payment when a fact is uncertain? | Unknown stays unknown. A missing or unreadable value is a finding, never a guess. Uncertain identity or currency holds the invoice for review. |
| Which policy choices are ours? | Listed in section 3. The brief supplies the stock numbers and gives the USD 10,000 threshold as an example. |
| Revisions and duplicates | One payment per payable, ever. Revisions are stored as separate versions under that payable (section 3, rows 9-11). |
| Local vs hosted | Local CLI and SQLite is the deliverable. A hosted demo is an optional supplement (Part 04). |

## 2. Requirement matrix

The test and evidence details are in [evaluation.md](evaluation.md). A check marked implemented still has the limitations listed below.

| Case requirement | Implementation | Verification or limitation |
| --- | --- | --- |
| Read PDF/TXT, CSV, JSON and XML | `parsers/`, ingestion agent | All 20 supplied files run in mock mode; image-only PDFs need OCR and are rejected. |
| SQLite inventory and stock checks | `storage.py`, `rules/inventory.py`, catalog tool | Unit tests and mock regression run. Stock is a fixed snapshot. |
| Simulated VP decision and critique | `agents/review.py`, LangGraph in `workflow.py` | Controlled provider, mock workflow and browser tests. Live model behavior unverified. |
| Mock payment or rejection with reasons | `ledger.py`, `tools/payment.py`, event log | Payment gate and duplicate/revision tests. No real bank. |
| LLM / Grok | `providers/openrouter.py` | Adapter implemented, live requests unverified. Mock reader is deterministic and is not an LLM. |
| Local operation without external APIs | mock provider, SQLite, CLI, API | End-to-end runs offline with no LLM. A local LLM is not configured; OpenRouter requires internet. |
| Multiple agents, tool calls, structured outputs and correction | `agents/`, `workflow.py`, `llm.py` | Controlled tests and simulated tool calls; real-provider tool and schema behavior unverified. |
| CLI, structured logs and result | `main.py`, `cli.py`, `storage.py` | CLI and event tests; commands in the root README. |
| UI and human review | `frontend/`, workspace API | Browser tests cover actual mock workflows, including review and blocked currency. |
| Public repository | User-controlled GitHub publication | Not yet published. |

## 3. Policy decisions

| # | Decision | Reason |
| --- | --- | --- |
| 1 | **Stock is a read-only snapshot.** Paying an invoice does not reduce it. | Keeps each run reproducible. A real system would reserve stock against receiving. |
| 2 | **Quantities are summed per item** before comparing with stock. Each priced line is kept. | Splitting an order across lines must not hide an over-order. Sample 1013 relies on this. |
| 3 | **Approval threshold: strictly above USD 10,000** needs completed extra scrutiny. Exactly 10,000 does not. | The brief gives "over $10K". The boundary is tested. |
| 4 | **USD only for automatic payment.** Other currencies are held as `UNSUPPORTED_CURRENCY`. A reviewer cannot clear it. | No exchange rate was supplied. Converting with an invented rate would be fabricating a payment amount. |
| 5 | **A missing currency defaults to USD, with an INFO finding.** The three supplied CSV files state no currency. | Otherwise three valid samples could never be paid. This is configurable (`assume_currency_when_missing`). |
| 6 | **A former vendor name ("formerly X") holds the invoice for review.** | This is a common payment-redirection pattern. It is a signal, not proof. Switch: `review_on_vendor_identity_change`. |
| 7 | **Urgency wording is a WARNING only.** It never blocks and is never called fraud. | Urgent wording appears on legitimate invoices too. |
| 8 | **Document text is data.** Text that tries to instruct the system is a REVIEW finding. | Prompt-injection defense. The rules run in code, so the text cannot switch them off. |
| 9 | **Payable identity = normalized vendor + normalized invoice number**, stable across revisions. | One payment per payable, enforced by a database UNIQUE constraint. |
| 10 | **Identity normalization is conservative.** Case, spacing and punctuation are ignored. `Widgets Inc.` and `Widgets LLC` stay different. `INV-1002` and `1002` stay different. Leading zeros are kept. | Wrongly merging two vendors is worse than holding a duplicate. |
| 11 | **Revisions.** An explicit newer revision of an unpaid invoice supersedes the older one. A revision after payment is held for reconciliation. Two versions with no usable order are all held. We never pay a difference automatically. | Credits, tax and partial payments are not specified by the brief. |
| 12 | **Batch preflight decides versions before any payment.** Filename order alone never selects what is paid. | Sorting by name could pay the wrong version. |
| 13 | **Overdue is INFO.** The clock is injected. | Every sample due date is in the past today. Rejecting them all would hide real problems. |
| 14 | **Unresolved dates stay unknown.** Evidence-backed extraction corrections are recorded; unsupported guesses such as `yesterday` are not accepted. Numeric `NN/NN/YYYY` dates get a WARNING when the order is ambiguous. | Wrong dates are worse than missing dates. |
| 15 | **Stated totals are never rewritten.** A wrong total is a finding. Only absent values are derived. | We report what the invoice says, not what we wish it said. |
| 16 | **Tolerance: 1 minor unit** (one cent for USD). | Absorbs rounding only. Tested at the boundary. |
| 17 | **Model repairs need evidence.** A correction must quote the source. It is accepted as a plain repair only if it differs by look-alike characters (`O`/`0`). A change of magnitude (`3,500.O0` to `350,000`) is held. Every extracted number must appear in the source. | A model repeating itself is not proof. |
| 18 | **Approvals bind to invoice content and policy.** Changing either voids the approval. | Prevents approving one thing and paying another. |
| 19 | **The payment gate reloads and re-validates everything inside one transaction.** The caller passes identifiers only. | A UI boolean or a model's say-so is never authorization. |
| 20 | **Demo limits are ours:** 2 MB per file, 200,000 characters of text, 20 PDF pages, 1,000 CSV rows, 200 lines. | Not requirements of the brief. They are policy values and can be changed. |

## 4. Findings, severity and route

`ERROR` rejects. `REVIEW` holds for a person. `WARNING` allows payment but the rationale must address it. `INFO` is shown only.
A reviewer can clear a REVIEW finding only when the code is marked *reviewer-resolvable* (`findings.py`). ERRORs are never clearable.

| Code | Severity | Reviewer can clear | Needs evidence |
| --- | --- | --- | --- |
| MISSING_FIELD (identity, due date, items, total) | ERROR | no | no |
| MISSING_FIELD (invoice date only) | WARNING | no | no |
| SOURCE_FIELD_INVALID, INVALID_QUANTITY, INVALID_AMOUNT, NEGATIVE_AMOUNT | ERROR | no | some |
| UNKNOWN_ITEM, AMBIGUOUS_ITEM, ZERO_STOCK_ITEM, INSUFFICIENT_STOCK | ERROR | no | no |
| LINE_TOTAL_MISMATCH, SUBTOTAL_MISMATCH, TOTAL_MISMATCH | ERROR | no | no |
| UNRESOLVED_DATE, DUE_BEFORE_INVOICE | ERROR | no | UNRESOLVED_DATE |
| TAX_MISMATCH, TERMS_DATE_INCONSISTENT, AMBIGUOUS_DATE | WARNING | no | AMBIGUOUS_DATE |
| PAYMENT_PRESSURE_LANGUAGE | WARNING | no | yes |
| OVERDUE, CURRENCY_ASSUMED, EXTRACTION_CORRECTION, DUPLICATE_SUBMISSION, SUPERSEDED_VERSION | INFO | n/a | EXTRACTION_CORRECTION |
| UNSUPPORTED_CURRENCY | REVIEW | **no** | no |
| REVISION_AFTER_PAYMENT, REVISION_CONFLICT | REVIEW | no | no |
| VENDOR_IDENTITY_CHANGE | REVIEW | yes | yes |
| PAYMENT_INSTRUCTION_IN_DOCUMENT | REVIEW | yes | yes |
| EMBEDDED_INSTRUCTIONS | REVIEW | yes | yes |
| CORRECTION_NOT_IN_SOURCE, SUBSTANTIVE_CORRECTION, VALUE_NOT_IN_SOURCE | REVIEW | yes | yes |

The authoritative table is `FINDING_SPECS` in `src/invoice_flow/findings.py`, and a test checks it is complete.

## 5. Observations about the supplied data

Verified by running the code, not only by reading:

- **20 files, 16 invoice numbers.** One explicit revision (1004 R1) and three alternate-format pairs (1011, 1012, 1013).
- **1007** states a total of 15,525, but 14,750 plus 885 tax is 15,635.
- **1013** states a total of 22,562.80, but 21,040 plus 1,472.80 tax is 22,512.80 (a USD 50 difference). Each line fits stock alone, but summed per item the quantities are 22, 18 and 9 against stock of 15, 10 and 5.
- **1011.pdf** has no subtotal or tax lines, and **1012.pdf** has the same `2O26` and `3,500.O0` typos as its text twin. So duplicate detection cannot depend on those lines, and OCR repair cannot be assumed already done in the PDF.
- **1006, 1007, 1015** state no currency. **1014** is in EUR.
- **1002** has a due date equal to its invoice date but "Net 30" terms. We report a WARNING and do not invent a rule that this is illegal.
- **1010** charges 300 for one rush WidgetA line against 250 elsewhere. We have no authoritative price list, so this is not a finding.

## 6. Current limitations

- The no-key, no-network mode uses a deterministic simulated reader. It demonstrates the control flow but is not an LLM. The OpenRouter adapter is implemented and unit-tested with mock transport, but live model access, accuracy, cost and latency were not measured. It needs internet, which is at odds with the case's no-internet constraint. A real local model is future work.
- Tests that use hand-written TXT/PDF drafts prove deterministic checks, not real model extraction accuracy. The 20 supplied examples are a known regression set, not a blind test.
- The evidence checks look for source quotes and values, but do not prove every field's semantic meaning. Prompt-injection detection uses patterns and is not a complete security boundary.
- No OCR. A scanned PDF without a text layer is refused. The web API serves extracted text, not original PDF bytes.
- No vendor master, authoritative price list or FX source was supplied, so none is checked. A reviewer can only clear a finding if its policy marks it resolvable.
- Payments, inventory and review identities are simulated. A mock receipt is not proof of bank settlement. Workspaces are sandboxed by a signed session cookie, not enterprise authentication.
- Run exactly one server process. A running job is marked interrupted after a restart. Batches are not automatically recovered after restart. Free hosted storage is ephemeral.
- The application spending cap counts only costs reported by the provider; an unknown provider cost is not counted. Public live mode is disabled by default and needs a provider-side cap before enabling.
- Coverage is risk-based, not exhaustive. No cross-process load, production backup/restore or accessibility conformance audit has been performed.
