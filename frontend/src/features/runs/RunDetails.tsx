import { AlertCircle, ArrowDownToLine, ArrowRight, Check, CheckCircle2, Clock3, FileSearch, FileText, LoaderCircle, ShieldCheck } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { formatAmount, Status } from '../../components/Status';
import { ApiClient } from '../../api/client';
import { ReviewPanel } from '../review/ReviewPanel';
import type { Finding, RunEvent, RunResult } from '../../types/api';

const stages = [{ key: 'intake', title: 'Received' }, { key: 'extraction', title: 'Extracted' }, { key: 'validation', title: 'Checked' }, { key: 'approval', title: 'Reviewed' }, { key: 'payment', title: 'Payment' }];
function eventText(event: RunEvent) {
  const message = event.payload.message ?? event.payload.reason ?? event.payload.summary;
  return typeof message === 'string' ? message : event.type.replaceAll('_', ' ');
}
function Findings({ findings, onViewEvidence }: { findings: Finding[]; onViewEvidence: (value: string) => void }) {
  if (!findings.length) return <div className="quiet-empty"><ShieldCheck size={18} />No findings reported for this run.</div>;
  return <div className="finding-list">{findings.map((finding, index) => <div className={`finding finding-${finding.severity.toLowerCase()}`} key={`${finding.code}-${index}`}>
    <AlertCircle size={17} /><div><div className="finding-heading"><strong>{finding.code.replaceAll('_', ' ').toLowerCase()}</strong><span>{finding.severity}</span></div><p>{finding.message}</p>{(finding.field || finding.item_ref) && <small>{[finding.field, finding.item_ref].filter(Boolean).join(' · ')}</small>}
      {finding.observed && <div className="finding-values"><span>Found: <b>{finding.observed}</b></span>{finding.expected && <span>Expected: <b>{finding.expected}</b></span>}</div>}
      {finding.evidence && <><blockquote>{finding.evidence}</blockquote><button className="text-button evidence-link" onClick={() => onViewEvidence(finding.evidence!)}>Find in source<ArrowRight size={12} /></button></>}
    </div></div>)}</div>;
}

export function RunDetails({ run, events, source, pdfUrl, pdfPages = 1, sourceError, onExport, exporting, api, onReviewed }: {
  run: RunResult; events: RunEvent[]; source: string | null; pdfUrl?: string | null; pdfPages?: number; sourceError: string | null;
  onExport: () => void; exporting: boolean;
  api?: ApiClient; onReviewed?: (run: RunResult) => void;
}) {
  const [tab, setTab] = useState<'details' | 'source' | 'activity'>('details');
  const [evidence, setEvidence] = useState<string | null>(null);
  const [documentView, setDocumentView] = useState<'original' | 'text'>('original');
  const [pdfPage, setPdfPage] = useState(1);
  const [previewFailed, setPreviewFailed] = useState(false);
  const mark = useRef<HTMLElement>(null);
  useEffect(() => { if (evidence) mark.current?.scrollIntoView?.({ block: 'center' }); }, [tab, evidence]);
  const active = run.lifecycle === 'queued' || run.lifecycle === 'running';
  const current = events.at(-1)?.stage;
  const seen = new Set(events.map((event) => event.stage));
  if (seen.has('critique')) seen.add('approval');
  const invoice = run.invoice;
  const needsAttention = run.outcome === 'needs_review' || run.outcome === 'rejected' || run.lifecycle === 'failed' || run.findings.some(f => f.severity === 'ERROR' || f.severity === 'REVIEW');
  const status = run.outcome === 'paid' && !run.receipt ? <span className="status status-warning"><span className="status-dot" />Receipt unavailable</span> : <Status status={run.outcome ?? run.lifecycle} />;
  const documentControls = pdfUrl && <div className="document-options"><button className="text-button" aria-pressed={documentView === 'original'} onClick={() => setDocumentView('original')}>Original PDF</button><button className="text-button" aria-pressed={documentView === 'text'} disabled={source === null} onClick={() => setDocumentView('text')}>Extracted text</button></div>;
  const sourceContent = sourceError ? <div className="source-message"><AlertCircle size={20} /><p>{sourceError}</p></div> : pdfUrl && documentView === 'original' ? <>{previewFailed ? <p role="alert">Preview unavailable. Download the original PDF or use extracted text.</p> : <img className="pdf-preview" alt={`Original invoice PDF page ${pdfPage}`} src={api?.previewUrl(run.run_id, pdfPage) ?? `/api/v1/runs/${encodeURIComponent(run.run_id)}/preview?page=${pdfPage}`} onError={() => setPreviewFailed(true)} />}{pdfPages > 1 && <div className="pdf-pages"><button className="text-button" disabled={pdfPage <= 1} onClick={() => { setPreviewFailed(false); setPdfPage((page) => page - 1); }}>Previous page</button><span>Page {pdfPage} of {pdfPages}</span><button className="text-button" disabled={pdfPage >= pdfPages} onClick={() => { setPreviewFailed(false); setPdfPage((page) => page + 1); }}>Next page</button></div>}<a className="text-button" href={pdfUrl} download={run.filename ?? 'invoice.pdf'}>Download original PDF</a></> : source === null ? <div className="source-message"><FileSearch size={25} /><p>{active ? 'Reading your document…' : 'Loading source text…'}</p></div> : <>{evidence && !source.includes(evidence) && <p className="help-copy">The quoted text could not be located exactly in this source.</p>}<pre>{evidence && source.includes(evidence) ? <>{source.slice(0, source.indexOf(evidence))}<mark ref={mark}>{evidence}</mark>{source.slice(source.indexOf(evidence) + evidence.length)}</> : source}</pre></>;
  const findingsCard = <div className="content-card findings-card"><div className="card-heading"><div><span className="mini-label">VALIDATION</span><h2>Checks & findings</h2></div><span className="count-pill">{run.findings.length}</span></div><Findings findings={run.findings} onViewEvidence={(value) => { setEvidence(value); setDocumentView('text'); setTab('source'); }} /></div>;
  return <section className="run-view" aria-label="Invoice details">
    <div className="detail-head"><div><div className="detail-kicker">INVOICE RECORD</div><h1>{invoice?.invoice_number ?? run.filename ?? 'Processing invoice'}{status}</h1><p>{invoice?.vendor_name ?? 'Vendor not extracted'}<span className="detail-separator">·</span>{run.filename ?? 'Uploaded document'}</p></div><button className="button button-outline" disabled={exporting || active} data-tour="export" onClick={onExport}><ArrowDownToLine size={16} />{exporting ? 'Preparing…' : 'Export trace'}</button></div>
    <div className="record-metadata"><span><span className="mode-dot" />{run.mode === 'mock' ? 'Simulated model' : run.mode === 'live' ? 'Live model' : 'Model mode unavailable'}</span>{run.model && <span>{run.model}</span>}<span>Mock payments only</span>{run.started_at && <span className="record-date">Received {new Date(run.started_at).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</span>}</div>
    <div className="stage-row">{stages.map((stage, index) => {
      const isCurrent = active && (current === stage.key || (stage.key === 'approval' && current === 'critique'));
      return <div className={`stage-item ${seen.has(stage.key) ? 'visited' : ''}`} key={stage.key}><div className={`stage-icon ${isCurrent ? 'current' : seen.has(stage.key) ? 'done' : ''}`}>{isCurrent ? <LoaderCircle size={14} className="spin" /> : seen.has(stage.key) ? <Check size={13} /> : <span>{index + 1}</span>}</div><span>{stage.title}</span>{index < stages.length - 1 && <div className="stage-connector" />}</div>;
    })}</div>
    <div className="tabs" role="tablist" aria-label="Invoice information"><button data-tour="overview" role="tab" aria-selected={tab === 'details'} onClick={() => setTab('details')}>Overview</button><button role="tab" aria-selected={tab === 'source'} onClick={() => setTab('source')}>Source document</button><button data-tour="activity" role="tab" aria-selected={tab === 'activity'} onClick={() => setTab('activity')}>Activity<span>{events.length}</span></button></div>
    <div role="tabpanel" className="tab-content">
      {tab === 'details' && <>
        {run.terminal_reason && !(run.receipt && run.outcome === 'paid') && <div className={`result-note ${run.lifecycle === 'failed' || run.outcome === 'rejected' ? 'bad' : ''}`}><AlertCircle size={19} /><div><strong>{run.outcome === 'needs_review' ? 'Review required before payment' : run.outcome === 'rejected' ? 'Payment blocked' : run.lifecycle === 'failed' ? 'Processing could not finish' : 'Processing summary'}</strong><p>{run.terminal_reason}</p></div></div>}
        {run.outcome === 'paid' && !run.receipt && <div className="result-note bad"><AlertCircle size={19} /><div><strong>Receipt unavailable</strong><p>A paid outcome was returned without a stored receipt. Verify the run before relying on this status.</p></div></div>}
        {run.receipt && <div className="receipt"><div className="receipt-icon"><CheckCircle2 size={22} /></div><div><strong>Mock payment recorded</strong><p>{formatAmount(run.receipt.amount, run.receipt.currency)} · {run.receipt.transaction_id}</p></div><span>SIMULATED</span></div>}
        <div className="review-layout">
          <div className="record-pane">{run.outcome === 'needs_review' && (api && onReviewed ? <ReviewPanel api={api} run={run} onReviewed={onReviewed} /> : <div className="review-hold-note"><LockIcon /><p>This invoice is on hold. A reviewer must verify the flagged details before it can proceed.</p></div>)}{needsAttention && findingsCard}<div className="content-card extracted-card"><div className="card-heading"><div><span className="mini-label">EXTRACTED INFORMATION</span><h2>Invoice details</h2></div><FileText size={18} /></div>
            {invoice ? <><dl className="field-grid"><div><dt>Vendor</dt><dd>{invoice.vendor_name}</dd></div><div><dt>Invoice number</dt><dd>{invoice.invoice_number}</dd></div><div><dt>Invoice date</dt><dd>{invoice.invoice_date ?? 'Not available'}</dd></div><div><dt>Due date</dt><dd>{invoice.due_date ?? 'Not available'}</dd></div></dl><div className="table-wrap"><table><thead><tr><th>ITEM</th><th>QTY</th><th>UNIT PRICE</th><th>AMOUNT</th></tr></thead><tbody>{invoice.lines.map((line, index) => <tr key={`${line.line_no}-${index}`}><td><strong>{line.description}</strong>{line.sku && line.sku !== line.description && <small>{line.sku}</small>}</td><td>{line.quantity}</td><td>{formatAmount(line.unit_price, invoice.currency)}</td><td>{formatAmount(line.amount, invoice.currency)}</td></tr>)}</tbody></table></div><div className="totals"><div><span>Subtotal</span><span>{formatAmount(invoice.subtotal, invoice.currency)}</span></div><div><span>Tax</span><span>{formatAmount(invoice.tax, invoice.currency)}</span></div><div><span>Shipping</span><span>{formatAmount(invoice.shipping, invoice.currency)}</span></div><div className="total-final"><strong>Total</strong><strong>{formatAmount(invoice.total, invoice.currency)}</strong></div></div></>
              : <div className="quiet-empty"><Clock3 size={18} />{active ? 'Fields will appear when processing completes.' : 'No verified invoice fields are available.'}</div>}
          </div>{!needsAttention && findingsCard}
            {run.approval.length > 0 && <div className="content-card"><div className="card-heading"><h2>Review decisions</h2><ShieldCheck size={18} /></div><div className="decision-list">{run.approval.map((approval, index) => <div className="decision" key={`${approval.stage}-${index}`}><span>{approval.stage} · {approval.action}</span><p>{approval.rationale}</p></div>)}</div></div>}
          </div><div className="document-pane"><div className="document-toolbar"><span><FileText size={16} />Source document</span>{documentControls}<span className="document-type">{run.filename?.split('.').pop()?.toUpperCase() ?? 'SOURCE'}</span></div><div className="document-sheet">{sourceContent}</div><div className="document-caption"><ShieldCheck size={13} />{pdfUrl && documentView === 'original' ? 'Preview of the original PDF · original available to download' : 'Extracted source text'}</div></div></div>
      </>}
      {tab === 'source' && <div className="document-pane full-source"><div className="document-toolbar"><span><FileText size={16} />Source document</span>{documentControls}<span className="document-type">{run.filename ?? 'Original input'}</span></div><div className="document-sheet">{sourceContent}</div></div>}
      {tab === 'activity' && <div className="content-card activity-card"><div className="card-heading"><div><span className="mini-label">AUDIT TRAIL</span><h2>Processing activity</h2><p>Events recorded by the server, in the order they occurred.</p></div><span className="count-pill">{events.length} events</span></div>{events.length ? <ol className="timeline">{events.map((event) => <li key={event.seq}><div className="timeline-point" /><div><strong>{event.stage}<span>#{event.seq}</span></strong><p>{eventText(event)}</p></div><time>{new Date(event.timestamp).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })}</time></li>)}</ol> : <div className="quiet-empty">No activity recorded yet.</div>}</div>}
    </div>
  </section>;
}
function LockIcon() { return <ShieldCheck size={17} />; }
