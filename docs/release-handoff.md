# Integration handoff

The application can be run locally in mock mode. The API, frontend, reviewer decisions, simulated payment ledger and container have been exercised together. See [evaluation.md](evaluation.md) for results and their scope.

Checks completed on 3 November 2026:

- Python: Ruff passed; 596 tests passed and 4 live tests were deselected.
- Frontend: TypeScript, ESLint and build passed; 31 tests passed.
- Browser: 5 Playwright tests passed against an isolated local backend in mock mode.
- Supplied data: 20 of 20 predefined outcomes matched on fresh independent ledgers. A separate batch used a shared ledger and exercised duplicates and revisions.
- Safeguards: 52 of 52 selected deliberate mutations were caught by the test suite.
- Docker: the image built and passed the isolated container smoke test, including custom port, access gate, simulated processing, export and workspace isolation.

These results do not measure live-model performance or general accuracy. The app's payments are simulations. The case data and expected outcomes were available during development.

Still to do before claiming a live demonstration or submission:

1. Configure an authorized OpenRouter key and a spending limit, then run and record live-model checks. No live calls were made for this handoff.
2. Resolve the brief's offline requirement: the deterministic mock runs without a provider, but it is not a local AI model. OpenRouter requires network access.
3. Create or connect the intended repository and confirm hosted CI on its actual commit. A workflow file alone is not a CI result.
4. If a hosted supplement is wanted, deploy the prepared Render blueprint and verify the hosted flow. Free-tier local storage is ephemeral. A shared passcode is a demo gate, not enterprise authentication.
5. Review the README and any AI-assistance disclosure, then send only the requested repository link when ready. No submission has been sent.

The prototype is not a production payment system. It has no real bank integration, enterprise identity, distributed workers, durable hosted storage on the free tier, or independent security and accessibility audit. Scanned PDFs without extractable text require OCR, which is not included.
