# Deploy on Render

**[Live demo](https://invoice-desk-823q.onrender.com/) · [YouTube walkthrough](https://youtu.be/3wwiXjCHQoQ)**

## Setup

1. Connect `theomthakur/invoice-desk` to a Render Blueprint.
2. Select branch **deployment**, use root **render.yaml**, and leave Root Directory empty.
3. Supply `OPENROUTER_API_KEY` and a positive `MODEL_BUDGET_USD` privately. Set a provider-side key limit too.
4. Keep `LLM_PROVIDER=openrouter`, `MODEL_MODE=live` and `ALLOW_LIVE_IN_SESSIONS=1`. The Blueprint supplies these settings and generates `SESSION_SECRET`.
5. Leave `DEMO_ACCESS_CODE` unset for open access, or set a shared code. No separate frontend service is needed.
6. Deploy. For an existing service, check Environment settings directly; later Blueprint changes may need syncing.

Automatic deployment is off in the Blueprint. After a release, choose **Manual Deploy → Deploy latest commit**. Pushing Git commits alone does not update the hosted service.

## Verify the release

- Open `/api/v1/healthz`; expect HTTP 200.
- Open **New invoice → AI model**; verify the configured model and OpenRouter choices.
- Process an invoice; inspect its recorded provider/model, source, decision and activity.
- Test a review hold and report export. Confirm duplicate processing cannot create a second receipt.

Missing `SESSION_SECRET` prevents public startup. Generate one privately with `python -c "import secrets; print(secrets.token_urlsafe(32))"` if the service was created manually. Preserve an existing secret to retain sessions. Missing credentials or budget disable live AI; live provider failures do not switch to mock responses. [Model setup](model-setup.md)

## Storage, cost and recovery

The free demo uses temporary SQLite/files; records may disappear after restart or redeployment. Cold starts can delay the first page. Treat it as a single-operator demo with one process. Provider charges apply in live mode; missing reported cost does not count as zero or establish a hard application spending guarantee.

Queued jobs resume; in-progress jobs become interrupted and need an explicit retry. A retry still passes through payment checks. Durable hosting requires writable persistent storage, tested backups and a restore procedure. Rolling back code does not roll back the database.

An older container passed a constrained local check: peak memory 213,676,032 bytes with no OOM, at a 512,000,000-byte limit and 0.1 CPU. That evidence was committed in `b61dbf3` on 3 November 2026 and predates later changes. It is not a current-release or production-load measurement. [Recorded evidence](../evidence/offline/render-container.json)

## Local Docker check

```bash
docker build -t invoice-desk:local .
docker run --rm -p 127.0.0.1:10000:10000 \
  -e SESSION_SECRET='local-example-only-change-this' invoice-desk:local
```

Open http://127.0.0.1:10000. This example uses offline responses. The image runs as UID 10001; mounted storage must be writable by that user. For a Render-targeted build on an ARM machine, add `--platform linux/amd64` to the build command.

## Release branches

Make and check shared changes on `main`, merge into `deployment`, then push both. Both branches should contain the same reviewed application. Deploy `deployment` manually and verify the actual HTTPS service.

References: [Render Blueprints](https://render.com/docs/blueprint-spec) · [Free services](https://render.com/docs/free)
