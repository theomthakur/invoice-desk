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

`render.yaml` is a prepared **free, ephemeral, mock-only** web service, with a generated session secret and a required manually entered demo access code. It uses the root Dockerfile and health endpoint. No service is claimed as deployed. Connect the public repository, select the intended branch and root blueprint, enter the access code in Render, then deploy. The access code is a shared demo gate, not personal authentication. Do not put it or a model key in the repository or client bundle.

Render prompts for `sync: false` secrets only when initially creating a Blueprint. Existing services must have `DEMO_ACCESS_CODE` configured manually; an unset code permits open demo access. Confirm that missing and incorrect codes fail before sharing the URL. A generated `SESSION_SECRET` is created only when absent, so retain an existing valid secret unless intentionally ending sessions. Keep `MODEL_MODE=mock`, `ALLOW_LIVE_IN_SESSIONS=0`, and no provider key for the free demonstration.

The case instructions require local operation and say no cloud deployment. The hosted app is an optional supplement. The requested submission remains the GitHub repository URL.

For the free service, local files and SQLite are lost when the instance is redeployed, restarted or spun down. According to [Render's free service documentation](https://render.com/docs/free), it spins down after 15 minutes without inbound traffic, and waking can take about a minute. The UI reports ephemeral storage. If persistence is required, use an appropriate paid disk or managed service, set the storage flag truthfully, test restart and backups, and keep one server process. Do not assume a free instance is durable. The configuration follows the [Render Blueprint reference](https://render.com/docs/blueprint-spec).

Free compute provides 0.1 CPU and 512 MB RAM, according to the [compute plan reference](https://render.com/docs/compute-plans). Use this as a controlled, single-operator demo and run one sample batch at a time. The individual-job worker is bounded, but sample batches use separate threads; this is not a concurrent load-tested service. The React app and API share the same origin, so a second frontend service and CORS configuration are unnecessary.

For a Docker build on an ARM development machine that targets Render, use `docker build --platform linux/amd64 -t invoice-desk:local .`. Building locally and checking a container does not create a hosted service or verify its eventual HTTPS domain. After deployment, the browser must confirm login, processing, source inspection and export over the actual HTTPS URL.

The current image passed a local sequential verification with a 512,000,000-byte memory limit, no extra swap and 0.1 CPU. It ran as UID 10001 on a custom port and exercised the browser access gate, invoice processing, original PDF/PNG preview, simulated review, exports, workspace isolation, the 20-file batch and CLI. Peak memory was 213,676,032 bytes with no OOM. This is a controlled demo check, not a production load or hosted availability claim. See [container evidence](../evidence/offline/render-container.json).

## Live inference and limits

Public live inference is off (`ALLOW_LIVE_IN_SESSIONS=0`). Four live Grok 4.7 acceptance tests passed on known samples on 4 November 2026; broader live reliability remains unverified. Enabling it needs a configured key, access gate, explicit numeric application cap and an enforceable provider-side key cap. The app's known-cost counter does not count requests for which the provider omits cost; it is not a complete spending guarantee. The rate limiter is per process, and provider-backed inference requires network access.

## Restart and rollback

Queued individual runs may resume; in-progress runs become interrupted. Batches are not automatically recovered after restart. A retry still reaches the same payment gate. Before a persistent deployment, take a SQLite-consistent backup, test a restore, and record the application commit and schema compatibility. Rolling back code does not roll back database changes. No persistent-host backup or restore has been verified for this project.
