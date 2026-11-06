# Invoice Desk

**[Open the live demo](https://invoice-desk-823q.onrender.com/) · [Watch the YouTube walkthrough](https://youtu.be/3wwiXjCHQoQ)**

Read an invoice, check it against inventory, review exceptions and record a simulated payment. Separate reader, validator, approver and critic agents work through LangGraph; Python enforces the payment rules.

![Invoice Desk workspace](frontend/docs/screenshots/dark-workspace.png)

## Review it in three minutes

1. Open the live demo. Allow about a minute if the service is waking up.
2. Select **New invoice → Sample invoices**. Check the **AI model** selection; OpenRouter calls use the server's API credit.
3. Process `invoice_1001.txt`, inspect the source, decision and activity, then save a decision or activity PDF.
4. Try `invoice_1003.txt` for rejection and `invoice_1012.txt` for human review. Existing records can change the outcome to duplicate.

Use **Guided walkthrough** for help, **System design** for the architecture, and **Presentation** for the full-screen explanation. Payments and inventory are simulated. Hosted records use temporary storage.

## Run locally

Prerequisites: [uv](https://docs.astral.sh/uv/getting-started/installation/), Node 22+ and npm. On macOS or Linux:

```bash
git clone https://github.com/theomthakur/invoice-desk.git
cd invoice-desk
./scripts/run_local.sh
```

Open **http://127.0.0.1:8000**. First setup needs internet. The script installs locked dependencies, builds the UI and starts offline test mode: no model key or API charge. Press **Ctrl+C** to stop. Local history stays in `data/runtime`.

Manual setup, including Windows:

```bash
uv sync --locked --python 3.12
npm --prefix frontend ci
npm --prefix frontend run build
uv run python main.py --serve
```

CLI, from the repository root:

```bash
uv run python main.py --invoice_path=data/invoices/invoice_1001.txt
uv run python main.py --mode mock --batch=data/invoices
```

The CLI defaults to offline testing unless `MODEL_MODE` is set. A rejection is a successful business result; processing the same invoice again does not create another payment.

## Use real AI

Follow [model setup](docs/model-setup.md) to configure a server-side key and spending limit. A configured browser defaults to live inference. **New invoice → AI model** offers Grok 4.7, Claude Haiku 4.5 and GPT-4o mini through OpenRouter. Offline testing is available in Developer mode, or when live AI is unconfigured. A live error stays visible; it does not switch to mock responses.

Grok has the recorded live evaluation below. The alternative models have not passed an equivalent evaluation. Payments remain simulated with every model.

## Requirements and implementation

| Requirement | Implementation |
| --- | --- |
| Ingest invoices | PDF/TXT extraction; CSV, JSON and XML parsers. |
| Check inventory | SQLite stock lookup plus independent field, quantity and arithmetic checks. |
| Multi-agent reasoning | Reader extracts; validator requests inventory; approver proposes; critic challenges. One revision, then human review if unresolved. |
| Tool use and structured outputs | Typed responses and strict tool arguments; invalid data cannot authorize payment. |
| Approval or rejection | Findings explain each result. Eligible invoices reach a transaction that rechecks identity, version, policy and approvals before a mock receipt. |
| Local execution | Shared Python service for the CLI and browser; no key needed for offline testing. |

Roles may share a model. Structured inputs can skip model extraction; blocked invoices can stop early. Activity shows which stages actually ran.

Additional features: department handoffs, linked corrections/retries, duplicate protection, analytics charts, source inspection, PDF/CSV reports, multi-select, dark mode and a collapsible sidebar. Handoffs record an assignment; they do not notify a department or authorize payment.

**Stack:** Python, FastAPI, LangGraph, SQLite, React, TypeScript and Docker. [Architecture and code map](docs/architecture.md) · [Policy decisions](docs/decisions.md)

## Verification

| Check | Recorded result |
| --- | --- |
| Backend | 798 tests passed; four paid tests excluded. |
| Frontend / browser | 83 frontend tests and 21 browser tests passed. |
| Supplied samples | 20/20 expected outcomes with offline test responses. |
| Live Grok rerun | 16/16 expected matches across ten known scenarios; $0.198862 provider-reported cost. |

The Grok rerun used a corrected missing-vendor expectation frozen before inference. The original 15/16 result is retained. These are known-case checks, not general accuracy claims. See [evaluation and reproduction commands](docs/evaluation.md).

## Limits and deployment

No real bank, ERP, vendor master, notification service or enterprise sign-in is connected. OCR is excluded. Run one server process; distributed recovery and production load are unverified. Render records may reset after restart or redeployment. [Render instructions](docs/deployment.md) · [Model configuration](docs/model-setup.md)

`main` is the product branch; `deployment` is the hosting release branch. Both carry the same reviewed application code.

Sample inputs originate from the [Galatiq case repository](https://github.com/galatiq-ai/galatiq-case-invoices); see [provenance](data/invoices/SOURCE.md). Built with AI coding assistance.
