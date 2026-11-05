# Choose a model provider

The default is simulation: no model key or API charge. For real model responses,
choose **OpenRouter** or **direct xAI** on the server. Reviewers can use their own key.
Never put a key in the browser, source code, screenshots or a committed file.

## Local setup

Complete the [local quick start](../README.md#run-locally) once to install dependencies and build the interface, then stop the server before configuring live mode.

1. Copy `.env.example` to `.env` and edit it locally. `.env` is git-ignored.
2. Set one of the configurations below. Enter the matching key in that private file.
3. Set a positive `MODEL_BUDGET_USD` and a provider-side spending limit.
4. Load the file into the shell before starting the app:

```bash
set -a
. ./.env
set +a
uv run python main.py --serve
```

For the browser, set `ALLOW_LIVE_IN_SESSIONS=1` before starting and select **Live model**
under Processing mode. For the CLI, pass `--mode live`. Setting a key alone does not
switch simulation to live mode. Restart the server after changing configuration.

### OpenRouter

```dotenv
LLM_PROVIDER=openrouter
LLM_MODEL=x-ai/grok-4.7
LLM_BASE_URL=
MODEL_MODE=live
```

Set `OPENROUTER_API_KEY` privately. OpenRouter uses its own model identifier and endpoint.
The recorded Grok evaluation used this path.

### Direct xAI

```dotenv
LLM_PROVIDER=xai
LLM_MODEL=grok-4.7
LLM_BASE_URL=
MODEL_MODE=live
```

Set `XAI_API_KEY` privately. This calls `https://api.x.ai/v1` directly, without OpenRouter.
Leave `LLM_MODEL` and `LLM_BASE_URL` blank to use defaults for the selected provider.
When switching providers, clear old overrides or replace them with the matching values.
The app does not try another provider or simulation if the selected provider fails.

Direct xAI request, tool, schema, credential and cost handling have offline contract tests.
A funded direct xAI run has not been recorded. The existing OpenRouter results are not
claimed as validation of the direct path.

## Payments and costs

Both providers use real model inference in live mode. **Payments, approvals and inventory
remain simulated**, as requested by the case. Eligible invoices receive a stored simulated
receipt after the code-controlled gate checks the current invoice, approvals and holds.
No real money moves. Missing keys do not change how payment safety is checked.

OpenRouter reports cost in USD. Direct xAI reports integer USD ticks, converted at
10,000,000,000 ticks per dollar. Missing or invalid cost remains unknown, never zero.
The application cap uses recorded known costs between runs; it is not a hard reservation
for every request or a substitute for a provider-side limit.

## Render

The committed Blueprint stays in simulation. To deliberately enable live inference,
configure the selected provider, its private key, model and budget in Render, with an
access code and `ALLOW_LIVE_IN_SESSIONS=1`. Update the corresponding Blueprint values
on the deployment branch too, so a later sync does not restore the mock settings.
The key must stay a private environment secret. No hosting or paid inference is enabled
by this document.

Protocol references: [xAI structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs),
[xAI cost reporting](https://docs.x.ai/developers/cost-tracking).
