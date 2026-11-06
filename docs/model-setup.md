# Configure AI models

**[Live demo](https://invoice-desk-823q.onrender.com/) · [YouTube walkthrough](https://youtu.be/3wwiXjCHQoQ)**

Use OpenRouter for the hosted demo. Keys stay on the server. Inventory, VP authority and payments remain simulated even with real AI responses.

## OpenRouter locally

Complete the [local setup](../README.md#run-locally), then stop the app. Copy `.env.example` to ignored `.env` and set:

```dotenv
LLM_PROVIDER=openrouter
LLM_MODEL=x-ai/grok-4.7
LLM_BASE_URL=
MODEL_MODE=live
ALLOW_LIVE_IN_SESSIONS=1
OPENROUTER_API_KEY=your-private-key
MODEL_BUDGET_USD=your-positive-budget
```

Replace both placeholders. Set a provider-side spending limit, then start from the repository root (macOS/Linux):

```bash
set -a
. ./.env
set +a
uv run python main.py --serve
```

On Windows, set the same environment variables in PowerShell before running the Python command. The app does not automatically load `.env`.

Open **New invoice → AI model**. Choices are **Grok 4.7**, **Claude Haiku 4.5** and **GPT-4o mini** through OpenRouter; the configured model is selected first. Each job retains its selection across queue recovery. Only approved IDs can be requested. Grok has recorded live evaluation; alternatives have not passed equivalent checks.

For CLI inference, run `uv run python main.py --mode live --invoice_path=data/invoices/invoice_1001.txt`. `./scripts/run_local.sh` deliberately starts offline testing; use the command above for live mode.

## Other options

| Option | Configuration |
| --- | --- |
| Direct xAI | `LLM_PROVIDER=xai`, `LLM_MODEL=grok-4.7`, private `XAI_API_KEY`, positive budget; clear `LLM_BASE_URL`. Offline contract tests only; no recorded funded direct-xAI run. |
| Offline test | `MODEL_MODE=mock`; no model calls. Browser option appears in Developer mode, or when live AI is unconfigured. |
| Local Ollama | `LLM_PROVIDER=ollama`; installed local tool-capable model and daemon with `OLLAMA_NO_CLOUD=1`. Experimental: tested Qwen models did not pass clean-invoice acceptance. |

Changing a provider requires its matching model ID and key. Restart after environment changes. Live failures remain errors; there is no automatic provider or offline fallback.

## Render and spending

The [Blueprint](../render.yaml) enables OpenRouter/browser live mode. Enter keys and the budget privately in Render, keep the generated session secret, then deploy. Optional access code: `DEMO_ACCESS_CODE`. [Deployment steps](deployment.md)

The application cap checks recorded known costs between runs; it does not reserve all in-flight spending. Unknown costs are not counted. Use a provider-side key limit. OpenRouter reports USD cost; direct xAI's integer USD ticks are converted at 10,000,000,000 ticks per dollar.
