import { GitBranch } from 'lucide-react';
import type { ConfigInfo } from '../../types/api';
import './resources.css';

function ArchitectureDiagram() {
  return <figure className="architecture-figure"><div className="diagram-scroll" tabIndex={0} aria-label="Architecture diagram. Scroll horizontally on narrow screens.">
    <svg viewBox="0 0 1080 650" className="architecture-diagram" role="img" aria-labelledby="architecture-title architecture-description">
      <title id="architecture-title">Invoice Desk current system architecture</title>
      <desc id="architecture-description">The React browser sends requests to FastAPI, which stores workspace-scoped records in SQLite and queues work for a local worker. The worker runs shared service code and LangGraph. The workflow uses the simulated reader or an explicitly configured OpenRouter model. A code-enforced payment gate rechecks the invoice and records a simulated receipt in SQLite. The browser polls for results. The CLI calls the shared service directly.</desc>
      <defs><marker id="diagram-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 10 5 0 10Z" fill="#6c8959" /></marker></defs>
      <g className="diagram-edges" markerEnd="url(#diagram-arrow)">
        <path d="M270 94H330" markerStart="url(#diagram-arrow)" /><path d="M630 94H735" />
        <path d="M480 152V234" /><path d="M480 348V430" />
        <path d="M330 490H270" markerStart="url(#diagram-arrow)" /><path d="M630 490H735" />
        <path d="M885 430V176" /><path d="M630 290H690V143H735" />
      </g>
      <g className="diagram-node"><rect x="30" y="35" width="240" height="117" rx="14" /><text x="50" y="69" className="diagram-kicker">OPERATOR</text><text x="50" y="98" className="diagram-name">React web application</text><text x="50" y="123">Upload, inspect, review and export</text></g>
      <g className="diagram-node"><rect x="330" y="35" width="300" height="117" rx="14" /><text x="352" y="69" className="diagram-kicker">WEB API</text><text x="352" y="98" className="diagram-name">FastAPI + workspace access</text><text x="352" y="123">Session cookie, CSRF, limits and ownership</text></g>
      <g className="diagram-node diagram-storage"><rect x="735" y="35" width="300" height="141" rx="14" /><text x="757" y="69" className="diagram-kicker">STORED RECORDS</text><text x="757" y="98" className="diagram-name">SQLite</text><text x="757" y="124">Documents, original PDFs, runs and events</text><text x="757" y="148">Inventory, versions, decisions and receipts</text></g>
      <g className="diagram-node"><rect x="330" y="234" width="300" height="114" rx="14" /><text x="352" y="268" className="diagram-kicker">BACKGROUND WORK</text><text x="352" y="297" className="diagram-name">Local job worker</text><text x="352" y="322">Queued run in SQLite; local thread wakes it</text></g>
      <g className="diagram-note"><text x="30" y="278" className="diagram-name">The CLI shares the same service</text><text x="30" y="304">It does not need the browser or HTTP API.</text></g>
      <g className="diagram-node"><rect x="30" y="430" width="240" height="139" rx="14" /><text x="50" y="464" className="diagram-kicker">MODEL</text><text x="50" y="493" className="diagram-name">Mock reader or live model</text><text x="50" y="519">Live requests through OpenRouter</text><text x="50" y="543">Same model, separate role requests</text></g>
      <g className="diagram-node diagram-workflow"><rect x="330" y="430" width="300" height="139" rx="14" /><text x="352" y="464" className="diagram-kicker">SHARED PROCESSING</text><text x="352" y="493" className="diagram-name">service.py + LangGraph</text><text x="352" y="519">Read, validate, register and gather evidence</text><text x="352" y="543">Approve, critique, revise or hold</text></g>
      <g className="diagram-node"><rect x="735" y="430" width="300" height="139" rx="14" /><text x="757" y="464" className="diagram-kicker">PAYMENT CHECK</text><text x="757" y="493" className="diagram-name">Payment gate in code</text><text x="757" y="519">Reload version, policy, findings and approval</text><text x="757" y="543">One mock receipt per workspace / payable</text></g>
      <image href="/tech/react.svg" x="225" y="48" width="25" height="25" />
      <image href="/tech/fastapi.svg" x="585" y="48" width="25" height="25" />
      <image href="/tech/sqlite.svg" x="990" y="48" width="25" height="25" />
      <image href="/tech/python.svg" x="585" y="247" width="25" height="25" />
      <image href="/tech/openrouter.svg" x="225" y="443" width="25" height="25" />
      <image href="/tech/langgraph.svg" x="585" y="443" width="25" height="25" />
      <image href="/tech/python.svg" x="990" y="443" width="25" height="25" />
      <g className="diagram-labels"><text x="286" y="78">HTTP</text><text x="649" y="78">read / write</text><text x="494" y="199">queued work</text><text x="494" y="392">run workflow</text><text x="46" y="402">Typed model and tool responses</text><text x="655" y="474">decision</text><text x="900" y="321">recheck / receipt</text><text x="642" y="270">source + inventory</text></g>
      <text x="30" y="621" className="diagram-footnote">The browser polls the API for results and events. The default deployment is one process, not a distributed system.</text>
    </svg>
  </div><figcaption>React and FastAPI serve the workspace. A Python worker runs LangGraph. SQLite stores the records. OpenRouter is used only in live mode; payments are simulated.</figcaption></figure>;
}

export function SystemDesign({ config }: { config: ConfigInfo | null }) {
  return <section className="resources-page" aria-label="System design">
    <div className="page-heading"><div><span className="page-eyebrow">INSIDE INVOICE DESK</span><h1>System design</h1><p>Follow an invoice from the browser to a checked decision and a recorded outcome.</p></div><span className="version-tag">Current implementation</span></div>
    <section className="content-card architecture-card"><div className="card-heading"><div><span className="mini-label">HIGH-LEVEL ARCHITECTURE</span><h2>One document, one traceable workflow</h2></div><GitBranch size={20} /></div><ArchitectureDiagram /></section>
    <div className="resource-section-heading"><h2>Different roles. One controlled decision.</h2><p>The reader extracts the invoice. The validator checks stock and amounts. The approver suggests an action and a critic challenges it. Code checks the final decision again before recording a mock payment. These roles share one configured model in live mode.</p></div>
    <div className="tech-stack"><img src="/tech/docker.svg" alt="Docker"/><span>Docker packages the React app, FastAPI and Python worker in one service.</span></div>
    {config && <p className="resource-footnote">Policy {config.policy_version}. The browser keeps asking the API for progress and results. Original files, invoice versions and decisions stay in the same workspace.</p>}
  </section>;
}
