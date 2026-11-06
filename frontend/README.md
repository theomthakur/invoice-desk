# Invoice Desk frontend

**[Live demo](https://invoice-desk-823q.onrender.com/) · [YouTube walkthrough](https://youtu.be/3wwiXjCHQoQ) · [Project setup](../README.md#run-locally)**

React 19, TypeScript and Vite. The production build is served by FastAPI.

## Develop

From the repository root, start the backend:

```bash
uv sync --locked --python 3.12
uv run python main.py --serve
```

In another terminal, use Node 22+:

```bash
cd frontend
npm ci
npm run dev
```

Open the Vite URL printed in the terminal. `/api` proxies to `127.0.0.1:8000`, preserving the browser host for origin checks. For a single service, build with `npm --prefix frontend run build` and open the backend on port 8000.

## Interface rules

- **Invoices:** processing attempts; multiple attempts do not imply multiple payments.
- **Handoffs:** internal review assignments; no external notifications or payment authority.
- **Review:** four independently scrolling panels; approval is rechecked by the server.
- **AI model:** configured live model by default. Developer mode reveals offline testing and technical JSON; it is not an access-control feature.
- **Reports:** decision/activity PDFs use the browser print dialog. CSV is text-only.
- **Guided walkthrough:** waits for explicit actions; it does not submit decisions.
- Theme/sidebar preferences persist. Presentation supports full screen and keyboard navigation.

## Check

From `frontend`, run `npm run lint`, `npm test` and `npm run build`. Install Chromium once with `npx playwright install chromium`, then run `npm run test:e2e`. Browser tests start an isolated offline backend and use no model credit. `UPDATE_SCREENSHOTS=1` refreshes screenshots. [Recorded results](../docs/evaluation.md)

Technology marks in `public/tech` come from [Simple Icons](https://simpleicons.org/). Original invoice PDFs remain separate from branded application reports.
