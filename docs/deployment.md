# Deployment and operations

## Local container

The container builds the frontend and Python service from lockfiles and runs as a non-root user. The following uses a **temporary example secret**, mock mode and a local-only port; replace the value for any hosted service.

```bash
docker build -t invoice-desk:local .
docker run --rm -p 127.0.0.1:10000:10000 \
  -e SESSION_SECRET='local-example-only-change-this' \
  invoice-desk:local
```

Open http://127.0.0.1:10000. The process binds to `0.0.0.0` inside the container and respects `PORT` (default 10000). Health endpoint: `/api/v1/healthz`. The image runs exactly one application process. The default database is `/tmp/invoice-flow`, is ephemeral across container replacement, and is reported as ephemeral. `APP_ENV=production` sets Secure cookies; browsers normally treat loopback as a secure context, but other plain HTTP hosts will not send those cookies.

For durable storage, mount a writable directory and set `DATA_DIR` to that mount and `STORAGE_PERSISTENCE=persistent` only after verifying persistence and backup/restore. The non-root image runs as UID 10001, so the mount must be writable by that UID. A mounted volume does not by itself establish a backup procedure.

## Render blueprint

The [hosted demo](https://invoice-desk-823q.onrender.com/) is available, and the [recorded walkthrough](https://youtu.be/3wwiXjCHQoQ) shows the application. Local execution remains the primary review path. The Blueprint configures one free Docker service configured for OpenRouter live inference with temporary storage and a generated session secret.

Connect the public repository, select `deployment`, and use the root `render.yaml`. A demo access code is optional: leave it unset for open demo access, or set `DEMO_ACCESS_CODE` in Render to require a shared code. If enabled, check that missing and incorrect codes fail. This is a shared gate, not personal authentication.

`SESSION_SECRET` is required when binding publicly. The Blueprint generates one; for a manually created service, generate a random value locally with `python -c "import secrets; print(secrets.token_urlsafe(32))"` and enter it privately in Render. Keep an existing secret unless intentionally ending sessions. Never commit it or a model key.

Render prompts for `sync: false` values when initially creating a Blueprint; later changes must be made in the existing service's Environment settings. The committed configuration uses `LLM_PROVIDER=openrouter`, `MODEL_MODE=live` and `ALLOW_LIVE_IN_SESSIONS=1`. Supply `OPENROUTER_API_KEY` and a positive `MODEL_BUDGET_USD` privately. The browser selects the configured live model first and allows Grok 4.7, Claude Haiku 4.5 and GPT-4o mini through OpenRouter. The actual service environment may differ, so inspect its selected mode before a demonstration. A Git push does not deploy while automatic deployment is disabled in the Blueprint.

For the free service, local files and SQLite are lost when the instance is redeployed, restarted or spun down. According to [Render's free service documentation](https://render.com/docs/free), it spins down after 15 minutes without inbound traffic, and waking can take about a minute. The UI reports ephemeral storage. If persistence is required, use an appropriate paid disk or managed service, set the storage flag truthfully, test restart and backups, and keep one server process. Do not assume a free instance is durable. The configuration follows the [Render Blueprint reference](https://render.com/docs/blueprint-spec).

Free compute provides 0.1 CPU and 512 MB RAM, according to the [compute plan reference](https://render.com/docs/compute-plans). Use this as a controlled, single-operator demo and run one sample batch at a time. Individual runs use the bounded worker pool and batches use one bounded, persisted queue; this is not a concurrent load-tested service. The React app and API share the same origin, so a second frontend service and CORS configuration are unnecessary.

For a Docker build on an ARM development machine that targets Render, use `docker build --platform linux/amd64 -t invoice-desk:local .`. Building locally and checking a container does not create a hosted service or verify its eventual HTTPS domain. After deployment, the browser must confirm login, processing, source inspection and export over the actual HTTPS URL.

An earlier image passed a local sequential verification with a 512,000,000-byte memory limit, no extra swap and 0.1 CPU. It ran as UID 10001 on a custom port and exercised the browser access gate, invoice processing, original PDF/PNG preview, simulated review, exports, workspace isolation, the 20-file batch and CLI. Peak memory was 213,676,032 bytes with no OOM. This is historical evidence, committed in `b61dbf3` on 3 November 2026. The artifact identifies the tested image and source-tree fingerprint; it predates subsequent UI, batch-recovery and correction-identity changes. The current release has not had this constrained-container measurement repeated. It is not a production-load or hosted-availability result. See [container evidence](../evidence/offline/render-container.json).

## Live inference and limits

The Blueprint enables browser live inference (`ALLOW_LIVE_IN_SESSIONS=1`) when credentials and the spending cap are configured. Offline testing remains available in Developer mode; if live inference is unconfigured, the UI explicitly labels its offline responses. Four known live Grok 4.7 acceptance checks and 16 fresh runs are documented in evaluation; general reliability remains unverified. Optional Ollama inference is local-only and is not included in this free Render configuration. Enabling cloud inference needs a configured key, access gate, explicit numeric application cap and an enforceable provider-side key cap. Either OpenRouter or direct xAI can be selected; see [model setup](model-setup.md). Direct xAI has offline contract tests only. The app's known-cost counter does not count requests for which the provider omits cost; it is not a complete spending guarantee. The rate limiter is per process, and provider-backed inference requires network access.

## Restart and rollback

Queued individual runs may resume; in-progress runs become interrupted. Queued batches resume after restart. Running batches become interrupted with a visible reason and explicit rerun action. Shutdown is cooperative, with a five-second wait; it is not distributed recovery. A retry still reaches the same payment gate. Before a persistent deployment, take a SQLite-consistent backup, test a restore, and record the application commit and schema compatibility. Rolling back code does not roll back database changes. No persistent-host backup or restore has been verified for this project.


## Main and deployment branches

`main` remains the reviewed product and the repository default. `deployment` is the release branch selected by Render. Keep both branches on the same reviewed application code; pushing a branch alone does not create a service. Make shared fixes on main, verify them, then merge main into deployment. Keep hosting-only configuration changes on deployment. Do not force-push or copy a ZIP over either history. The branches need an explicit merge for each release and do not automatically stay in sync.

```bash
git fetch origin
git switch deployment
git merge origin/main
# Resolve any hosting configuration conflicts, then run the checks before pushing.
git push origin deployment
```
