# Rules and design decisions

The user is a fictional accounts-payable operator. The goal is to turn mixed-format invoices into a receipt, rejection or review hold with a visible reason. Scenario business figures are supplied context, not measured savings. Policy lives in `config/policy.yaml`; implementation and tests are mapped in [architecture](architecture.md).

## Main choices

| Choice | Reason |
| --- | --- |
| One Python service and SQLite | Simple local setup; shared rules for CLI and UI. Requires one process. |
| Separate reader, validator, approver and critic | Distinct responsibilities, checked exchanges and a bounded correction loop. |
| Code controls payment | Model approval cannot bypass arithmetic, identity, version or policy checks. |
| Real OpenRouter; offline test option | Demonstrate real reasoning while preserving the brief's local/offline execution path. |
| React operator workspace | Source inspection, review and reports in one interface. |

## Payment policy

| Rule | Behavior |
| --- | --- |
| Inventory | Read-only sample snapshot. Sum repeated item quantities before comparing stock. No authoritative price list was supplied. |
| Threshold | Strictly above USD 10,000 requires additional scrutiny; exactly 10,000 does not. |
| Currency | Automatic payment is USD-only. Missing currency defaults to USD with an INFO finding; other currencies remain blocked. No invented exchange rates. |
| Totals | Preserve stated amounts. Derive absent values only. Rounding tolerance is one minor currency unit. |
| Identity | Normalize vendor and invoice number conservatively; keep leading zeros and distinct legal suffixes. One payment per payable across revisions. |
| Revisions | Source-supported newer unpaid revisions supersede older ones. Revisions after payment require reconciliation. Unordered conflicts need explicit content-bound review. Never pay a difference automatically. |
| Corrections | An unrelated document cannot become payable by attaching it as a correction or clearing another vendor warning. |
| Dates | Overdue is informational. Unresolved dates remain unknown; ambiguous numeric dates are flagged. |
| Source evidence | Extracted values and repairs need support. Look-alike character repairs are distinguished from changes in amount or meaning. |
| Document instructions | Treat invoice text as untrusted data. Embedded instructions cannot change policy or authorize payment. |
| Vendor name change | Hold for review. Urgency wording alone is a warning, not a fraud conclusion. |
| Approval binding | Invoice content or policy changes invalidate approval. The gate reloads both before payment. |

Default input limits: 2 MB/file, 200,000 text characters, 20 PDF pages, 1,000 CSV rows and 200 line items. These are implementation choices, not externally mandated limits.

## Findings and review

- **ERROR:** reject; a reviewer cannot clear it.
- **REVIEW:** hold. Only server-designated findings can be resolved.
- **WARNING:** may proceed, with the concern addressed in the rationale.
- **INFO:** display without blocking.

The authoritative list is [`FINDING_SPECS`](../src/invoice_flow/findings.py). Currency, unrelated-correction and paid-revision restrictions cannot be cleared by resolving an ordinary vendor warning. Review requests bind to the stored version and content hash. Department assignment alone grants no approval.

## What the supplied data revealed

- 20 files represent 16 invoice numbers: revision 1004 R1 and alternate-format pairs for 1011, 1012 and 1013.
- 1007's stated total disagrees with subtotal plus tax. 1013 is overstated by $50 and exceeds stock when repeated items are combined.
- 1011.pdf omits subtotal/tax lines; 1012 includes look-alike character errors. Preserve uncertainty and source evidence.
- Three CSV samples omit currency; 1014 uses EUR. 1002's dates conflict with Net 30 terms.
- 1010 has different prices across lines, but no supplied catalog establishes that either price is invalid.

File names and expected labels are evaluation inputs; runtime rules do not branch on them. [Sample provenance](../data/invoices/SOURCE.md) · [Evaluation](evaluation.md)

## Boundaries

Payments, inventory, VP authority and review identities are simulated. No real bank, vendor master, notification or ERP is connected. Source matching and instruction detection are safeguards, not complete semantic/security proofs. No OCR, distributed recovery, production load guarantee, enterprise authorization or verified backup/restore. Hosted storage is temporary. Spending controls use known provider-reported costs; enforce a provider-side key limit too.

Unknown legacy identities can hold new payment until sources are resubmitted and reconciled. Old browser sessions whose process-only signing key was lost cannot be recovered; current local signing keys persist beside the database. Local Ollama is experimental because tested Qwen models held clean invoices unnecessarily.
