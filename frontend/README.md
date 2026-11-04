# Invoice Desk frontend

React 19, TypeScript and Vite provide the fictional Acme Corp accounts-payable workspace. For setup, requirements, architecture, limitations and evaluation, start with the [project README](../README.md).

## Development

Use Node 22 or later. From frontend/:

```bash
npm ci
npm run dev
```

Run the backend from the repository root in a separate terminal:

```bash
MODEL_MODE=mock uv run python -m invoice_flow.server
```

Vite proxies /api to localhost:8000 and preserves the browser host for same-origin checks. For the single-service local application, run npm run build in frontend/ and open the backend at http://127.0.0.1:8000.

## Interface conventions

- Invoices lists processing attempts. An invoice can have several attempts or versions; another attempt does not mean another payment.
- Handoffs lists review holds with internal department assignments. It does not notify a team or authorize payment.
- The overview has four fixed panels: invoice details, source document, validation and review decisions. Panels scroll independently and stack on narrow screens.
- Activity displays recorded events. Its readable PDF uses the browser print dialog. Developer mode reveals raw payloads and JSON export, shows its state even when the sidebar is collapsed, and remembers the choice in local browser storage. This toggle is not an authorization mechanism.
- Light is the default. An explicit theme choice persists. The guided walkthrough waits for user actions and never submits a review decision.
- System design shows the real processing flow and technology boundaries. Presentation has keyboard controls and a slide-only full-screen view. Neither starts invoice work.

## Checks

```bash
npm run lint
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Browser checks start an isolated local mock backend. They do not use the active workspace or model credit. Use UPDATE_SCREENSHOTS=1 with the browser command to refresh screenshots. Current measured results and scope are maintained only in [evaluation.md](../docs/evaluation.md).

## Assets

The Invoice Desk mark is an application asset. Technology marks in public/tech come from [Simple Icons](https://simpleicons.org/) and identify the technologies shown; they do not imply endorsement. Original supplier PDFs remain unchanged and are separate from branded application reports.
