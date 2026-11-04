# Invoice Desk frontend

A custom React and TypeScript workspace for processing the fictional Acme Corp invoices. The interface is organized around an accounts-payable workflow: receive an invoice, follow its checks, inspect exceptions, and retrieve its decision and mock receipt.

## Start locally

Use Node 22 or later:

```bash
npm ci
npm run dev
```

From the solution root, run the backend independently:

```bash
MODEL_MODE=mock uv run python -m invoice_flow.server
```

Vite proxies `/api` to `http://127.0.0.1:8000` and preserves the browser's Host header, so the backend's same-origin validation works. The frontend resumes its signed-cookie workspace using `POST /session`. An optional access code is sent in `X-Demo-Code`, and mutations use `X-CSRF-Token`. No processing starts automatically on page load.

## Product workflow

1. **Invoice inbox:** actual workspace records with status filters, search, received-time/vendor sorting and history pagination. Summary counts refer to loaded processing attempts, not all company invoices or unique payables. Status and vendor are taken from the backend.
2. **New invoice:** a focused side panel with file upload, supplied samples, explicit model mode and an explicit processing action. It reports file limits from the server. Closing with Escape returns focus to the previous control; focus stays within the open panel.
3. **Invoice record:** original PDF page previews or extracted text beside verified fields, line items, totals and findings. PDF pages are rendered as bounded images; the original bytes remain downloadable. Literal finding evidence opens the retained extracted text. Recorded review decisions and event history explain the result.
4. **Receipt and trace:** paid is shown on the record only when a stored receipt exists. Every payment is labeled simulated. Trace download uses the backend export. History uses a completed label until a receipt is checked in the record.
5. **Sample batch:** explicit processing of the supplied set, per-file results, related revisions/duplicates and per-currency mock-payment totals.
6. **Processing rules:** read-only server approval thresholds, supported payment currencies and document limits.

The UI does not edit source documents, determine payment eligibility, invent totals or bypass backend policy. It does not contain a provider key. Unknown fields and connection failures remain visible. The empty inbox invites a first upload or sample rather than inserting fake records.

## Branding and responsive layout

One invoice/check mark is used in the app and in browser icons. `public/` contains the SVG, PNG favicons, Apple touch icon and web manifest. Fonts and assets load locally. The light layout uses compact operational tables and a focused document review view. On narrow screens the navigation is condensed, the document review stacks, and tables scroll inside their own container.

## Verification on 3 November 2026

- Node 22.13.1; TypeScript and ESLint passed.
- Vitest: 36 tests passed. Coverage includes error bodies and status codes, CSRF and access-code headers, cancellation, exact decimal display, receipt gating, original PDF retrieval, text/preview switching, evidence location, explicit processing, duplicate clicks, queue search/sort and status grouping.
- Production build passed.
- Real Brave browser against the local backend in **mock mode**, using a separate temporary database: a TXT sample reached a stored mock-payment receipt; an invalid CSV sample was rejected with findings; an uploaded JSON file was processed; its JSON trace downloaded with stored events; history survived refresh; and the 20-file sample batch completed. These are workflow checks, not live-model quality measurements.
- Desktop at 1512×1050 and mobile at 390×844 were inspected. Queue search, the rules page, Escape to close intake and page-level horizontal overflow were checked. No browser JavaScript errors were observed.

Screenshots in `docs/screenshots/` show actual local simulated runs, not intercepted API fixtures:

- `inbox-desktop.png`, `inbox-mobile.png`, `inbox-empty.png`
- `new-invoice-upload.png`, `new-invoice.png`, `new-invoice-mobile.png`
- `invoice-record.png`, `invoice-exception.png`, `simulated-review.png`, `batch-results.png`, `processing-rules.png`

The current simulated review screen is shown in `docs/screenshots/simulated-review.png`. Six Playwright tests exercise the integrated mock workflow, including review controls and original PDF inspection. Screenshots predating PDF retention show the extracted-text view.

## Remaining integration boundaries

- Live OpenRouter execution was not tested. No paid provider calls were made during this UI work.
- A simulated reviewer can approve a held invoice only after checking all server-designated resolvable findings, confirming the source and providing a note. The backend revalidates the content hash, policy, workspace and payment gate. Unsupported currency cannot be cleared through this review.
- Original PDFs are retained for newly loaded documents and served by an owner-scoped download endpoint. The inline viewer uses bounded page images, keeping document scripts out of the application. Historical records without original bytes retain extracted text and need the original loaded again for PDF inspection.
- This is an invoice-processing prototype with mock payments, not a production bank or ERP integration. Formal accessibility auditing, deployment and broader device testing remain release work.

## Frontend checks

```bash
npm run typecheck
npm run lint
npm test
npm run build
```

The production build is served by the same backend origin. Frontend changes are confined to `frontend/`; no payment rules or model code were changed for this redesign.
