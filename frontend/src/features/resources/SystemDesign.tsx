import { ArrowRight, Database, FileCheck2, GitBranch, LockKeyhole, ShieldCheck } from 'lucide-react';
import type { ConfigInfo } from '../../types/api';
import './resources.css';

const roles = [
  ['01', 'Read the document', 'Structured files are parsed directly. TXT and PDF text use the extraction role, with a bounded reread when its checks find inconsistencies.'],
  ['02', 'Gather inventory evidence', 'The validation role requests the inventory lookup tool. Code validates the arguments and queries the supplied mock inventory.'],
  ['03', 'Propose a decision', 'The approver considers the invoice, findings, approval policy, inventory evidence and previous invoice history.'],
  ['04', 'Challenge the proposal', 'A separate critic checks the recommendation. One revision is allowed; unresolved disagreement goes to human review.'],
];

function ArchitectureDiagram() {
  return <figure className="architecture-figure"><div className="diagram-scroll" tabIndex={0} aria-label="Architecture diagram. Scroll horizontally on narrow screens.">
    <svg viewBox="0 0 1080 650" className="architecture-diagram" role="img" aria-labelledby="architecture-title architecture-description">
      <title id="architecture-title">Invoice Desk current system architecture</title>
      <desc id="architecture-description">The React browser sends requests to FastAPI, which stores workspace-scoped records in SQLite and queues work for a local worker. The worker runs shared service code and LangGraph. The workflow uses the simulated reader or an explicitly configured OpenRouter model. A code-enforced payment gate rechecks the invoice and records a simulated receipt in SQLite. The browser polls for results. The CLI calls the shared service directly.</desc>
      <defs><marker id="diagram-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 10 5 0 10Z" fill="#6c8959" /></marker></defs>
      <g className="diagram-edges" markerEnd="url(#diagram-arrow)">
        <path d="M270 94H330" /><path d="M630 94H735" />
        <path d="M480 152V234" /><path d="M480 348V430" />
        <path d="M330 490H270" /><path d="M630 490H735" />
        <path d="M885 430V176" /><path d="M630 290H690V143H735" />
      </g>
      <g className="diagram-node"><rect x="30" y="35" width="240" height="117" rx="14" /><text x="50" y="69" className="diagram-kicker">OPERATOR</text><text x="50" y="98" className="diagram-name">React web application</text><text x="50" y="123">Upload, inspect, review and export</text></g>
      <g className="diagram-node"><rect x="330" y="35" width="300" height="117" rx="14" /><text x="352" y="69" className="diagram-kicker">REQUEST LAYER</text><text x="352" y="98" className="diagram-name">FastAPI + workspace access</text><text x="352" y="123">Session cookie, CSRF, limits and ownership</text></g>
      <g className="diagram-node diagram-storage"><rect x="735" y="35" width="300" height="141" rx="14" /><text x="757" y="69" className="diagram-kicker">STORED RECORDS</text><text x="757" y="98" className="diagram-name">SQLite</text><text x="757" y="124">Documents, original PDFs, runs and events</text><text x="757" y="148">Inventory, versions, decisions and receipts</text></g>
      <g className="diagram-node"><rect x="330" y="234" width="300" height="114" rx="14" /><text x="352" y="268" className="diagram-kicker">BACKGROUND WORK</text><text x="352" y="297" className="diagram-name">Local job worker</text><text x="352" y="322">Queued run in SQLite; local thread wakes it</text></g>
      <g className="diagram-note"><text x="30" y="278" className="diagram-name">The CLI shares the same service</text><text x="30" y="304">It does not need the browser or HTTP API.</text></g>
      <g className="diagram-node"><rect x="30" y="430" width="240" height="139" rx="14" /><text x="50" y="464" className="diagram-kicker">MODEL BOUNDARY</text><text x="50" y="493" className="diagram-name">Simulated reader by default</text><text x="50" y="519">Optional configured OpenRouter model</text><text x="50" y="543">Separate role requests; live is unverified</text></g>
      <g className="diagram-node diagram-workflow"><rect x="330" y="430" width="300" height="139" rx="14" /><text x="352" y="464" className="diagram-kicker">SHARED PROCESSING</text><text x="352" y="493" className="diagram-name">service.py + LangGraph</text><text x="352" y="519">Read, validate, register and gather evidence</text><text x="352" y="543">Approve, critique, revise or hold</text></g>
      <g className="diagram-node"><rect x="735" y="430" width="300" height="139" rx="14" /><text x="757" y="464" className="diagram-kicker">FINAL AUTHORITY</text><text x="757" y="493" className="diagram-name">Payment gate in code</text><text x="757" y="519">Reload version, policy, findings and approval</text><text x="757" y="543">One mock receipt per workspace / payable</text></g>
      <g className="diagram-labels"><text x="286" y="78">HTTP</text><text x="649" y="78">read / write</text><text x="494" y="199">queued work</text><text x="494" y="392">run workflow</text><text x="46" y="402">Typed model and tool responses</text><text x="655" y="474">decision</text><text x="900" y="321">recheck / receipt</text><text x="642" y="270">source + inventory</text></g>
      <text x="30" y="621" className="diagram-footnote">The browser polls the API for results and events. The default deployment is one process, not a distributed system.</text>
    </svg>
  </div><figcaption>Current implementation. Kubernetes, Redis, a vector database and real banking are not part of this architecture.</figcaption></figure>;
}

export function SystemDesign({ config }: { config: ConfigInfo | null }) {
  return <section className="resources-page" aria-label="System design">
    <div className="page-heading"><div><span className="page-eyebrow">INSIDE INVOICE DESK</span><h1>System design</h1><p>Follow an invoice from the browser to a checked decision and a recorded outcome.</p></div><span className="version-tag">Current implementation</span></div>
    <div className="resource-notice"><ShieldCheck size={18} /><p>The current demo uses simulated model responses and simulated payments. The live provider path is implemented but has not been verified with a real model.</p></div>
    <section className="content-card architecture-card"><div className="card-heading"><div><span className="mini-label">HIGH-LEVEL ARCHITECTURE</span><h2>One document, one traceable workflow</h2></div><GitBranch size={20} /></div><ArchitectureDiagram /></section>
    <div className="resource-section-heading"><span className="mini-label">THE AGENTIC PART</span><h2>Different roles. One controlled decision.</h2><p>LangGraph coordinates these roles. They can use the same configured model in separate requests; they are not four independent model services.</p></div>
    <div className="role-grid">{roles.map(([n,title,description]) => <article className="content-card role-card" key={n}><span>{n}</span><h3>{title}</h3><p>{description}</p></article>)}</div>
    <div className="resource-two-column">
      <section className="content-card"><div className="card-heading"><h2>What code always checks</h2><FileCheck2 size={20} /></div><ul className="resource-list"><li>Arithmetic, source evidence, dates, currency and available stock.</li><li>Duplicate invoices, revision conflicts and existing payments.</li><li>The displayed version and content hash when a reviewer submits a decision.</li><li>Cancellation and time limits before the mock payment call.</li></ul><p className="resource-footnote">A model recommendation cannot override these checks. No real bank is connected.</p></section>
      <section className="content-card"><div className="card-heading"><h2>Where the state lives</h2><Database size={20} /></div><ul className="resource-list"><li>SQLite is the authority for documents, versions, events and receipts.</li><li>Queued individual runs can recover after restart. In-progress runs become interrupted.</li><li>Batches are not automatically recovered after restart.</li><li>The prepared free Render service uses temporary local storage.</li></ul></section>
      <section className="content-card"><div className="card-heading"><h2>Access and boundaries</h2><LockKeyhole size={20} /></div><p className="resource-body">A signed HttpOnly cookie identifies the workspace. Reads are scoped to that workspace; mutations require the correct origin and a CSRF token. The optional shared passcode is a demo access gate, not enterprise sign-in.</p><p className="resource-footnote">Model requests, tools, retries and time are bounded. Live use requires a key, a configured application budget and provider-side spending controls.</p></section>
      <section className="content-card"><div className="card-heading"><h2>What happens at the end</h2><ArrowRight size={20} /></div><ul className="resource-list"><li>Eligible invoice: approval, critic and code gate can produce a mock receipt.</li><li>Resolvable hold: a person verifies the source and records a permitted decision.</li><li>Blocking error: payment stays blocked; the findings explain the issue.</li><li>Duplicates and superseded versions do not create another payment.</li></ul></section>
    </div>
    {config && <div className="resource-config"><span>Current policy: {config.policy_version}</span><span>{config.limits.max_pdf_pages} PDF pages maximum</span><span>{(config.limits.max_file_bytes/1024/1024).toFixed(0)} MB per file</span><span>{config.enabled_modes.includes('live') ? 'Live mode available when selected' : 'Session mode: simulated'}</span></div>}
    <section className="content-card resource-future"><h2>What changes at production scale?</h2><p>Move from SQLite and local threads to a shared database and durable queue, store original files in object storage, add enterprise identity and reviewer roles, and enforce global rate and cost limits. Those are future changes, not capabilities of this prototype.</p></section>
  </section>;
}
