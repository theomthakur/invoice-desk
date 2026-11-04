import { ArrowDown, ArrowRight, CheckCheck, CircleAlert, FileClock, Files, FileText, Inbox, Plus, Search, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { formatAmount, Status } from '../../components/Status';
import type { HistoryItem } from '../../types/api';
import { InvoiceIllustration } from '../../components/InvoiceIllustration';
import { tableCsv } from '../runs/reportCsv';

export type QueueFilter = 'all' | 'attention' | 'processing' | 'completed' | 'blocked' | 'duplicate';
const active = (item: HistoryItem) => item.lifecycle === 'queued' || item.lifecycle === 'running';
export function inQueue(item: HistoryItem, filter: QueueFilter) {
  if (filter === 'attention') return item.outcome === 'needs_review';
  if (filter === 'processing') return active(item);
  if (filter === 'completed') return item.lifecycle === 'completed';
  if (filter === 'blocked') return item.outcome === 'rejected' || item.lifecycle === 'failed' || item.lifecycle === 'interrupted';
  if (filter === 'duplicate') return item.outcome === 'duplicate';
  return true;
}
const names: Record<QueueFilter, string> = { all: 'All runs', attention: 'Needs review', processing: 'Processing', completed: 'Processing finished', blocked: 'Blocked', duplicate: 'Duplicates' };

export function InvoiceQueue({ items, filter, onFilter, openRun, newInvoice, trySamples, connected, cursor, loadMore, loading }: {
  items: HistoryItem[]; filter: QueueFilter; onFilter: (filter: QueueFilter) => void;
  openRun: (id: string) => void; newInvoice: () => void; trySamples: () => void;
  connected: boolean; cursor: string | null; loadMore: () => void; loading: boolean;
}) {
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('newest');
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const selectAll = useRef<HTMLInputElement>(null);
  const shown = useMemo(() => items.filter((item) => inQueue(item, filter))
    .filter((item) => [item.invoice_number, item.filename, item.vendor].filter(Boolean).join(' ').toLowerCase().includes(query.trim().toLowerCase()))
    .sort((a, b) => sort === 'vendor' ? (a.vendor ?? '').localeCompare(b.vendor ?? '') : (sort === 'newest' ? -1 : 1) * a.started_at.localeCompare(b.started_at)), [items, filter, query, sort]);
  const selectedItems = items.filter(item => selected.has(item.run_id));
  const visibleSelected = shown.filter(item => selected.has(item.run_id)).length;
  const allVisible = shown.length > 0 && visibleSelected === shown.length;
  useEffect(() => { if (selectAll.current) selectAll.current.indeterminate = visibleSelected > 0 && !allVisible; }, [visibleSelected, allVisible]);
  useEffect(() => {
    const present = new Set(items.map(item => item.run_id));
    setSelected(previous => {
      const next = new Set([...previous].filter(id => present.has(id)));
      return next.size === previous.size ? previous : next;
    });
  }, [items]);
  function toggleVisible(checked: boolean) {
    setSelected(previous => { const next = new Set(previous); shown.forEach(item => checked ? next.add(item.run_id) : next.delete(item.run_id)); return next; });
  }
  function downloadSelected() {
    const rows:unknown[][] = [['Run ID','Invoice','Vendor','Filename','Amount','Currency','Outcome','Processing status','Received']];
    selectedItems.forEach(item => rows.push([item.run_id,item.invoice_number,item.vendor,item.filename,item.amount,item.currency,item.outcome === 'paid' ? 'Payment reported' : item.outcome,item.lifecycle,item.started_at]));
    const url = URL.createObjectURL(new Blob([tableCsv(rows)],{type:'text/csv;charset=utf-8'}));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'selected-invoice-runs.csv'; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const cards = [
    { name: 'All runs', icon: Files, filter: 'all' as const },
    { name: 'Needs review', icon: CircleAlert, filter: 'attention' as const },
    { name: 'In progress', icon: FileClock, filter: 'processing' as const },
    { name: 'Processing finished', icon: CheckCheck, filter: 'completed' as const },
  ];
  return <section className="inbox" aria-label="Invoice inbox">
    <div className="queue-stats">{cards.map((card) => <button key={card.filter} onClick={() => onFilter(card.filter)} className={`stat-card ${filter === card.filter ? 'selected' : ''}`}>
      <div><span>{card.name}</span><card.icon size={17} /></div><strong>{items.filter((item) => inQueue(item, card.filter)).length}<span>runs</span></strong>
      <small>{card.filter === 'attention' ? 'Waiting for a decision' : card.filter === 'processing' ? 'Queued or processing' : card.filter === 'completed' ? 'Finished with a recorded outcome' : 'In your loaded history'}</small>
    </button>)}</div>
    <div className="queue-card">
      <div className="queue-tabs" role="group" aria-label="Filter invoices">{(Object.keys(names) as QueueFilter[]).map((key) => <button aria-pressed={filter === key} className={filter === key ? 'active' : ''} key={key} onClick={() => onFilter(key)}>{names[key]}<span>{items.filter((item) => inQueue(item, key)).length}</span></button>)}</div>
      {filter === 'duplicate' && <p className="queue-footnote">These runs were identified as duplicate submissions. Open a run to inspect its findings and related invoice records. Counts cover loaded runs only.</p>}
      <div className="queue-toolbar"><div className="search-field"><Search size={17} /><input aria-label="Search invoices" placeholder="Search invoice, vendor or filename…" value={query} onChange={(event) => setQuery(event.target.value)} />{query && <button aria-label="Clear search" onClick={() => setQuery('')}><X size={14} /></button>}</div>
        <label className="sort-control"><ArrowDown size={14} /><select aria-label="Sort invoices" value={sort} onChange={(event) => setSort(event.target.value)}><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="vendor">Vendor A to Z</option></select></label>
      </div>
      {selectedItems.length > 0 && <div className="selection-bar" role="status"><span><strong>{selectedItems.length} selected</strong>{selectedItems.length > visibleSelected && ` · ${selectedItems.length - visibleSelected} outside this view`}</span><div><button className="text-button" onClick={() => setSelected(new Set())}>Clear selection</button><button className="button button-outline" onClick={downloadSelected}>Export selected CSV</button></div></div>}
      {shown.length ? <div className="table-wrap queue-table"><table><thead><tr><th className="selection-cell"><input ref={selectAll} type="checkbox" aria-label="Select all visible loaded runs" checked={allVisible} onChange={event => toggleVisible(event.target.checked)} /></th><th>INVOICE</th><th>VENDOR</th><th>AMOUNT</th><th>STATUS</th><th>RECEIVED</th><th aria-label="Action" /></tr></thead><tbody>{shown.map((item) => <tr key={item.run_id}>
        <td className="selection-cell"><input type="checkbox" aria-label={`Select ${item.invoice_number ?? item.filename ?? 'run'} (${item.run_id})`} checked={selected.has(item.run_id)} onChange={event => setSelected(previous => {const next = new Set(previous); if(event.target.checked) next.add(item.run_id); else next.delete(item.run_id); return next;})} /></td><td><div className="invoice-cell"><span className="file-symbol"><FileText size={18} /></span><div><button onClick={() => openRun(item.run_id)}>{item.invoice_number ?? item.filename ?? 'Invoice processing run'}</button><small>{item.filename ?? item.run_id}</small></div></div></td>
        <td>{item.vendor ?? <span className="muted">Not extracted</span>}</td><td className="money-cell">{item.amount != null ? formatAmount(item.amount, item.currency) : <span className="muted">Not available</span>}</td>
        <td><Status status={item.outcome === 'paid' ? 'reported_paid' : item.outcome ?? item.lifecycle} /></td>
        <td className="date-cell">{new Date(item.started_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}<small>{new Date(item.started_at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}</small></td>
        <td><button className="row-open" onClick={() => openRun(item.run_id)} aria-label={`Open ${item.invoice_number ?? item.filename ?? item.run_id}`}><ArrowRight size={17} /></button></td>
      </tr>)}</tbody></table></div>
        : <div className={`queue-empty ${!items.length && !query ? 'first-invoice' : ''}`}>
          {!items.length && !query && <InvoiceIllustration />}
          <div className="empty-copy">{!items.length && !query ? <span className="empty-eyebrow"><span />YOUR INBOX STARTS HERE</span> : <div className="empty-document"><Inbox size={29} strokeWidth={1.5} /></div>}
          <h2>{query ? 'No matching invoices' : items.length ? 'Nothing in this view' : 'A clear view of every invoice'}</h2><p>{query ? 'Try another invoice number, filename or vendor.' : items.length ? 'Choose another status to see your invoices.' : 'Bring in an invoice. We’ll read the details, check the numbers, and show you what needs a decision.'}</p>
          {!items.length && <><div className="empty-actions"><button className="button button-primary" disabled={!connected} onClick={newInvoice}><Plus size={16} />Add your first invoice</button><button className="button button-outline" disabled={!connected} onClick={trySamples}>Try a sample<ArrowRight size={15} /></button></div><div className="empty-formats">PDF, TXT, JSON, CSV and XML supported</div></>}
          </div>
        </div>}
      <div className="queue-bottom"><span>{shown.length} of {items.length} loaded runs{cursor ? ' · more available' : ''}</span>{cursor ? <button className="text-button" onClick={loadMore} disabled={loading}>{loading ? 'Loading…' : 'Load more'}</button> : <span>Each processing attempt is recorded separately</span>}</div>
    </div>
    {!items.length && !query ? <div className="workflow-guide"><div><span>01</span><strong>Bring in the document</strong><p>Upload a file or explore a sample.</p></div><ArrowRight size={17} /><div><span>02</span><strong>Let the checks run</strong><p>Totals, stock and approval rules.</p></div><ArrowRight size={17} /><div><span>03</span><strong>See the next step</strong><p>A decision, a hold or a mock receipt.</p></div></div> : <div className="queue-footnote"><FileText size={14} /><span>Original documents, checks and decisions stay together in each invoice record.</span></div>}
  </section>;
}
