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

A custom Invoice Desk lettermark is used in the app and browser icons. `public/` contains the SVG, matching PNG favicons, Apple touch icon and web manifest. The bundled DM Sans font and its license are in `public/fonts/`; no external font request is needed. The workspace uses forest-green navigation, warm light surfaces, a first-invoice illustration, and a consistent layout for intake, review, history and rules. The desktop sidebar can collapse to icons and remembers the choice in browser storage. On narrow screens the navigation is condensed, the document review stacks, and tables scroll inside their own container.

## Project resources and priority

System design and Presentation are available from the sidebar. The architecture view uses a single high-level diagram with local technology marks and a short explanation of the agent roles. The seven-part presentation has slide controls, keyboard navigation and a full-screen option; opening it does not start a processing or payment action.

Held invoices show review controls and findings before extracted details. Rejected invoices put findings first. The source remains alongside the decision content on desktop and below it on mobile. Refresh reloads history only; it does not reprocess documents or make payments.

## Verification on 4 November 2026

- Node 22; TypeScript and ESLint passed.
- Vitest: 54 tests passed. Coverage includes error bodies and status codes, CSRF and access-code headers, cancellation, exact decimal display, receipt gating, original PDF retrieval, text/preview switching, evidence location, explicit processing, duplicate clicks, queue search/sort and status grouping.
- Production build passed.
- Real Brave browser against the local backend in **mock mode**, using a separate temporary database: a TXT sample reached a stored mock-payment receipt; an invalid CSV sample was rejected with findings; an uploaded JSON file was processed; its JSON trace downloaded with stored events; history survived refresh; and the 20-file sample batch completed. These are workflow checks, not live-model quality measurements.
- Desktop at 1512×1050 and mobile at 390×844 were inspected. Queue search, the rules page, Escape to close intake and page-level horizontal overflow were checked. No browser JavaScript errors were observed.

Screenshots in `docs/screenshots/` show actual local simulated runs, not intercepted API fixtures:

- `inbox-desktop.png`, `inbox-mobile.png`, `inbox-empty.png`
- `new-invoice-upload.png`, `new-invoice.png`, `new-invoice-mobile.png`
- `invoice-record.png`, `invoice-exception.png`, `simulated-review.png`, `batch-results.png`, `processing-rules.png`

The current simulated review screen is shown in `docs/screenshots/simulated-review.png`; `pdf-review.png` shows the original PDF preview. `system-design.png` and `presentation.png` show the project resources. The screenshots use actual local mock runs. Seventeen Playwright tests exercise the integrated workflow, including sidebar persistence, resource navigation, review priority, controls and original PDF inspection.

## Remaining integration boundaries

- Live OpenRouter execution was not tested. No paid provider calls were made during this UI work.
- A simulated reviewer can approve a held invoice only after checking all server-designated resolvable findings, confirming the source and selecting a reason. Extra notes are optional. The backend revalidates the content hash, policy, workspace and payment gate. Unsupported currency cannot be cleared through this review.
- Original PDFs are retained for newly loaded documents and served by an owner-scoped download endpoint. The inline viewer uses bounded page images, keeping document scripts out of the application. Historical records without original bytes retain extracted text and need the original loaded again for PDF inspection.
- This is an invoice-processing prototype with mock payments, not a production bank or ERP integration. Formal accessibility auditing, deployment and broader device testing remain release work.

## Frontend checks

```bash
npm run typecheck
npm run lint
npm test
npm run build
```

The production build is served by the same backend origin. The review update adds owner-scoped internal handoff endpoints and storage. Payment rules and model behavior remain unchanged.

## Structured review

Choose a Review action, then a predefined Decision reason. Approval still requires every permitted finding to be confirmed and the source acknowledgment checked. A typed Decision note is optional. Rejection saves the chosen reason and any extra note as the rationale.

Assign to a department records a durable internal handoff to Accounts payable, Procurement, Vendor management or Finance operations. Payment stays on hold. The latest assignment and its reason appear when the invoice is reopened. No email or external notification is sent. See `docs/screenshots/department-handoff.png`.

## Guided walkthrough and appearance

Open Guided walkthrough near the bottom of the sidebar. On small screens the guide and appearance controls sit at the bottom right. The 11-step guide points to the actual controls and covers intake, document selection, explicit processing, recorded stages, outcomes, source inspection, review, Activity, export and project resources. Back, Next and Close are available. The guide waits for intake and processing actions instead of clicking them. It never submits an approval, rejection or department handoff. It uses the current workspace without clearing history; previous runs can affect duplicate outcomes. Closing ends the guide; reopening starts it again.

Light is always the first-visit default, including on a computer set to dark mode. Use the sidebar appearance button to choose dark or light. The browser remembers that explicit choice. The choice is applied before the first React paint. If browser storage is blocked, the toggle still works for the current page. Dark tokens cover intake, review, history, rules, system design and presentation; original PDF page images keep their document colors.

New screenshots: `dark-workspace.png`, `guided-walkthrough.png` and `guided-mobile.png`. Browser checks cover the manual processing gate, no automatic review mutation, persisted appearance, dark resources, guide cleanup and mobile control placement.

## Sidebar navigation and account

System design and Presentation now sit in the main sidebar with an active-page indicator. The top bar shows the page location and demo status. The bottom account card uses a user icon and identifies Acme workspace as a Demo session. This represents the existing browser workspace, not a personal sign-in. The card remains visible as an icon when the rail is collapsed and as a bottom-left card on phones. Guide and appearance controls remain at the bottom right on phones.

The sidebar and account were checked at 320, 390, 768 and 1440 pixels. The 43 frontend tests and 13 browser checks passed, along with TypeScript, ESLint and the production build. `sidebar-navigation.png` shows the updated light layout.

## Outcome, activity and report

History shows Payment reported for a paid outcome without claiming that the list contains a receipt. Open the record to verify the simulated receipt. Rejected invoices show a selectable correction-request draft, but no message is sent. Activity displays concise labels from stored events with expandable technical details. Print decision report opens the browser print dialog, where it can be saved as PDF. The report uses the currently loaded record and is separate from the original PDF and JSON trace.

## Finding invoice versions and exporting CSV

Find invoice searches all identified payables in the current browser workspace by exact invoice number, independent of the loaded history page. It distinguishes different vendors with the same number, shows recorded versions and flags, and opens a related run. A simulated payment is indicated only if a payment row exists. The endpoint displays truncation at 50 matching payables or 100 versions per payable. Invalid or missing identities do not appear. There is no LLM or arbitrary SQL involved.

Download report CSV exports the selected run's details, findings, decisions and simulated receipt when available. Formula-like values are prefixed before opening in spreadsheet software. Print decision report remains available for a PDF through the browser. The original PDF remains separately downloadable. See `docs/screenshots/invoice-lookup.png`.

## Retry and corrected file

Failed and interrupted records offer an explicit Retry this invoice action. Rejected records allow a user to select a corrected file and explicitly process it. Each new attempt links to the original run and keeps its source available. All processing and payment checks still apply. The interface warns before a live-mode run because provider charges may apply. A correction with an unmatched identity is held and cannot be approved through this demo. The app never sends the correction request.

## Ledger overview

Ledger overview shows workspace-wide identified payables, run counts, failures, review outcomes, flagged active versions and simulated receipts grouped by currency. It queries the database, not just the history page. Counts of runs include retries, duplicates and batch extraction. Review runs are historical outcomes, not an outstanding balance. Values can be downloaded as CSV. Filter by UTC run start date, vendor or run outcome. With filters applied, identified invoices and active flags are linked to matching runs, and receipts require a matching paid run for the same version. Vendor filters exclude runs without an identifiable vendor. Reconciled drill-through and latency percentiles are not implemented. `ledger-overview.png` shows the local mock view.

## Select multiple entries

The invoice list supports selecting individual loaded runs and selecting all visible loaded runs. Search and status filters retain the selection and show how many selected runs are outside the current view. The selection is limited to loaded history, clears when leaving the list, and is pruned if records disappear from the loaded page. Export selected CSV includes all selected loaded runs, including those hidden by a filter. The export uses spreadsheet formula escaping. Selection and export do not approve, reject, reprocess or pay invoices. `multi-select.png` shows the workflow.

Technology marks in `public/tech/` come from Simple Icons (https://simpleicons.org), downloaded for local use. Brand marks identify the technologies in the architecture diagram; they do not imply endorsement.
