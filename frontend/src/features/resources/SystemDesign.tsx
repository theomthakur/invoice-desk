import { ArrowDown, ArrowRight, Database } from 'lucide-react';
import './resources.css';

const lanes = [
  {title:'1. Accept and save the input',steps:[
    {title:'Choose an invoice',tech:'React',logo:'react',text:'Upload a PDF, TXT, CSV, JSON or XML file, choose a sample, or start a sample batch.'},
    {title:'Check the request',tech:'FastAPI · Python',logo:'fastapi',text:'Verify the workspace cookie, request origin, CSRF token, permitted mode and input limits.'},
    {title:'Parse and store the file',tech:'Python parsers · SQLite',logo:'python',text:'Check the content, extract PDF text or parse structured fields, hash the bytes and save the source in this workspace.'},
    {title:'Queue a run',tech:'SQLite · local worker',logo:'sqlite',text:'Save the run before work starts. A request token prevents a repeated submission from creating another job.'},
  ]},
  {title:'2. Read the invoice and check the evidence',steps:[
    {title:'Prepare the draft',tech:'Reader role · LangGraph',logo:'langgraph',text:'JSON, CSV and XML map directly to fields. TXT and PDF text use the configured reader. An identical stored reading can be reused.'},
    {title:'Check the reading',tech:'Source checks · bounded reread',logo:'python',text:'Compare extracted values and quoted repairs with the source. A reader can try once more when its first reading is inconsistent.'},
    {title:'Validate the invoice',tech:'Python rules · SQLite inventory',logo:'sqlite',text:'Check totals, dates, currency, item names and summed quantities. Record errors, review needs, warnings and information.'},
    {title:'Register the version',tech:'SQLite invoice records',logo:'sqlite',text:'Match vendor and invoice number, detect duplicate submissions and order explicit revisions. Conflicts stay on hold.'},
  ]},
  {title:'3. Reach a decision and enforce it',steps:[
    {title:'Collect stock evidence',tech:'Validator role · inventory tool',logo:'python',text:'For an eligible invoice, the validation role requests the inventory tool. Code checks its arguments and reads the stock snapshot.'},
    {title:'Approve and challenge',tech:'Approver + critic · LangGraph',logo:'langgraph',text:'The approver proposes an action; a separate critic checks it. One revision is allowed. Disagreement can leave the invoice on hold.'},
    {title:'Check payment again',tech:'Python · SQLite transaction',logo:'python',text:'Reload the invoice, policy and approval. Reject stale versions, unresolved holds, unsupported currency and an existing payment.'},
    {title:'Record the outcome',tech:'Simulated payment · SQLite receipt',logo:'sqlite',text:'Eligible invoices receive a simulated receipt. Others finish as rejected, needs review, duplicate or superseded, with their reasons preserved.'},
  ]},
  {title:'4. Return the result and handle follow-up',steps:[
    {title:'Show progress',tech:'React · FastAPI polling',logo:'react',text:'The browser requests ordered events and the latest result. A disconnected browser does not stop the background job or erase its record.'},
    {title:'Review or hand off',tech:'Server-checked review actions',logo:'fastapi',text:'A person can resolve only permitted findings, reject, or record a department handoff. Handoffs stay on hold and send no notification.'},
    {title:'Retry or correct',tech:'Linked runs · same checks',logo:'python',text:'Failed or interrupted runs can be retried. Rejected runs can receive a corrected file. The original stays available and every new attempt reaches the same payment gate.'},
    {title:'Inspect and share',tech:'React reports · SQLite analytics',logo:'react',text:'Open the original PDF, source text, findings and decisions. Export readable reports or technical JSON; charts query the workspace records.'},
  ]},
];

export function SystemDesign() {
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

  </section>;
}
