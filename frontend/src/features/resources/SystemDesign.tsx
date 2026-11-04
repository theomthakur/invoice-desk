import { ArrowDown, ArrowRight, Database, ShieldCheck } from 'lucide-react';
import type { ConfigInfo } from '../../types/api';
import './resources.css';

const lanes = [
  {title:'1. Accept and save the input',steps:[
    {title:'Choose an invoice',tech:'React',logo:'react',text:'Upload a PDF, TXT, CSV, JSON or XML file, choose a sample, or start a sample batch.'},
    {title:'Check the request',tech:'FastAPI · Python',logo:'fastapi',text:'Verify the workspace cookie, request origin, CSRF token, permitted mode and input limits.'},
    {title:'Parse and store the file',tech:'Python parsers · SQLite',logo:'python',text:'Check the content, extract PDF text or parse structured fields, hash the bytes and save the source in this workspace.'},
    {title:'Queue a run',tech:'SQLite · local worker',logo:'sqlite',text:'Save the run before work starts. A request token prevents a repeated submission from creating another job.'},
  ]},
  {title:'2. Read the invoice and check the evidence',steps:[
    {title:'Prepare the draft',tech:'LangGraph · Python',logo:'langgraph',text:'JSON, CSV and XML map directly to fields. TXT and PDF text use the configured reader. An identical stored reading can be reused.'},
    {title:'Check the reading',tech:'Source checks · bounded reread',logo:'python',text:'Compare extracted values and quoted repairs with the source. A reader can try once more when its first reading is inconsistent.'},
    {title:'Validate the invoice',tech:'Python rules · SQLite inventory',logo:'sqlite',text:'Check totals, dates, currency, item names and summed quantities. Record errors, review needs, warnings and information.'},
    {title:'Register the version',tech:'SQLite invoice records',logo:'sqlite',text:'Match vendor and invoice number, detect duplicate submissions and order explicit revisions. Conflicts stay on hold.'},
  ]},
  {title:'3. Reach a decision and enforce it',steps:[
    {title:'Collect stock evidence',tech:'Validated inventory tool',logo:'python',text:'For an eligible invoice, the validation role requests the inventory tool. Code checks its arguments and reads the stock snapshot.'},
    {title:'Approve and challenge',tech:'LangGraph · model roles',logo:'langgraph',text:'The approver proposes an action; a separate critic checks it. One revision is allowed. Disagreement can leave the invoice on hold.'},
    {title:'Check payment again',tech:'Python · SQLite transaction',logo:'python',text:'Reload the invoice, policy and approval. Reject stale versions, unresolved holds, unsupported currency and an existing payment.'},
    {title:'Record the outcome',tech:'Mock payment · SQLite receipt',logo:'sqlite',text:'Eligible invoices receive a simulated receipt. Others finish as rejected, needs review, duplicate or superseded, with their reasons preserved.'},
  ]},
  {title:'4. Return the result and handle follow-up',steps:[
    {title:'Show progress',tech:'React · FastAPI polling',logo:'react',text:'The browser requests ordered events and the latest result. A disconnected browser does not stop the background job or erase its record.'},
    {title:'Review or hand off',tech:'Server-checked review actions',logo:'fastapi',text:'A person can resolve only permitted findings, reject, or record a department handoff. Handoffs stay on hold and send no notification.'},
    {title:'Retry or correct',tech:'Linked runs · same checks',logo:'python',text:'Failed or interrupted runs can be retried. Rejected runs can receive a corrected file. The original stays available and every new attempt reaches the same payment gate.'},
    {title:'Inspect and share',tech:'React reports · SQLite analytics',logo:'react',text:'Open the original PDF, source text, findings and decisions. Export readable reports or technical JSON; charts query the workspace records.'},
  ]},
];

export function SystemDesign({config}:{config:ConfigInfo|null}) {
  return <section className="resources-page" aria-label="System design">
    <div className="page-heading"><div><span className="page-eyebrow">INSIDE INVOICE DESK</span><h1>System design</h1><p>The complete path from an incoming file to a recorded decision, with the planned Render deployment.</p></div><span className="version-tag">Current code · Render prepared</span></div>
    <figure className="architecture-board" aria-label="Invoice Desk current system architecture">
      <div className="architecture-entry"><strong>Operator in a browser</strong><span>Upload, sample invoice or sample batch</span><ArrowDown size={19}/></div>
      <div className="hosting-boundary"><header className="hosting-title"><img src="/tech/render.svg" alt="Render"/><img src="/tech/docker.svg" alt="Docker"/><div><strong>Render web service · one Docker container</strong><p>Prepared deployment: React static files + FastAPI + Python worker. One process owns the local SQLite database.</p></div><span>render.yaml + Dockerfile</span></header>
        {lanes.map((lane,index)=><div className="architecture-lane" key={lane.title}><h2>{lane.title}</h2><ol className="architecture-steps">{lane.steps.map((step,i)=><li key={step.title}><span className="flow-number">{index*4+i+1}</span><img src={`/tech/${step.logo}.svg`} alt=""/><h3>{step.title}</h3><strong>{step.tech}</strong><p>{step.text}</p>{i<lane.steps.length-1 && <ArrowRight className="flow-arrow" size={19}/>}</li>)}</ol>{index<lanes.length-1 && <div className="lane-continue"><ArrowDown size={19}/><span>Continue to the next stage</span></div>}</div>)}
        <div className="architecture-shared"><Database size={22}/><div><h3>Shared stored records · SQLite in DATA_DIR</h3><p>Documents and original PDF bytes, extracted text, invoice versions, inventory, jobs, ordered events, findings, approvals, handoffs and mock receipts. Every read and action is scoped to the workspace. The CLI calls the same processing service and payment gate.</p></div></div>
      </div>
      <div className="model-connection"><ArrowDown size={18}/><span>Reading and decision roles call this only in live mode</span></div>
      <div className="external-model"><img src="/tech/openrouter.svg" alt="OpenRouter"/><div><h3>OpenRouter · external model service</h3><p>The server sends invoice text and role instructions to the configured model, currently Grok 4.7 for live tests. The API key stays on the server. Mock mode runs locally with a simulated reader. A live failure stays visible; it does not switch silently to mock.</p></div></div>
      <figcaption>Read each row from left to right, then move down. Arrows describe the normal path; validation errors, duplicates and holds can finish earlier without entering approval or payment.</figcaption>
    </figure>
    <div className="resource-section-heading"><h2>Different roles. One controlled decision.</h2><p>LangGraph coordinates preparation and decision graphs. The reader, validator, approver and critic have separate jobs; they can share the same model through separate requests. Stock checks, version rules and payment permission stay in code.</p></div>
    <div className="resource-two-column">
      <section className="content-card"><h2>What happens during ingestion?</h2><ul className="resource-list"><li>The API reads only up to the file limit, then checks extension and actual content.</li><li>Text-based PDFs are read page by page. Image-only PDFs are rejected because OCR is not included.</li><li>Structured files are parsed directly. A SHA-256 byte hash identifies identical content; this is separate from invoice identity and revision checks.</li><li>The source is saved before a run is queued. The browser then follows the run ID.</li><li>Sample batches prepare files and register their versions before making decisions, so file order cannot choose which revision gets paid.</li></ul></section>
      <section className="content-card"><h2>What happens when something fails?</h2><ul className="resource-list"><li>A bad upload is rejected with a reason before processing starts.</li><li>Default limits are eight model requests, six tool calls and 90 seconds per run, with at most 1,500 output tokens per response. They are configurable; invalid model output must be repaired or the run stops.</li><li>A restart restores queued individual jobs. Running jobs become interrupted; batches are not automatically recovered.</li><li>A retry is a new linked attempt. It cannot bypass the existing-payment or current-version checks.</li><li>Live mode requires a server-side key and application spending cap. Provider-side key limits are still needed because unknown reported cost is not a reliable spending boundary.</li><li>A successful model response alone cannot release payment. A stored approval and the final code checks are required.</li></ul></section>
      <section className="content-card"><h2>How Render hosts it</h2><ul className="resource-list"><li>Render builds the root Dockerfile. Node 22 builds React; Python 3.12 runs the application as a non-root user.</li><li>One web service serves the UI and API from the same address. It listens on Render’s PORT and exposes /api/v1/healthz.</li><li>The current Blueprint starts in mock mode with public live inference disabled. It creates a session secret and asks for a demo access code.</li><li>DATA_DIR is /tmp/invoice-flow and STORAGE_PERSISTENCE is ephemeral. Files and SQLite records can disappear when the instance is replaced.</li><li>Deployment is prepared, not yet verified at a hosted URL. Live hosting will need server-side model credentials and spending controls if enabled.</li></ul></section>
      <section className="content-card"><h2>What would change for production?</h2><ul className="resource-list"><li>Use a shared durable database and job queue with worker ownership and recovery.</li><li>Store original documents in object storage, with tested backups and deletion rules.</li><li>Add company sign-in, reviewer permissions and actual department notifications.</li><li>Use global rate and spending controls. A real payment provider needs its own idempotency and reconciliation design.</li><li>Test larger and unseen invoice sets, concurrent traffic and live-model failures before broader use.</li></ul></section>
    </div>
    {config && <div className="resource-config"><ShieldCheck size={16}/><span>Policy {config.policy_version}</span><span>{config.limits.max_pdf_pages} PDF pages</span><span>{(config.limits.max_file_bytes/1000000).toFixed(0)} MB per file</span><span>Simulated payments only</span></div>}
  </section>;
}
