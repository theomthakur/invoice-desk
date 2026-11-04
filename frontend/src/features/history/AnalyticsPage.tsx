import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiClient } from '../../api/client';
import { formatAmount } from '../../components/Status';
import { rowsCsv } from '../runs/reportCsv';
import type { AnalyticsFilters, AnalyticsOverview, Outcome } from '../../types/api';

const empty:AnalyticsFilters={date_from:'',date_to:'',vendor:'',outcome:''};
export function AnalyticsPage({api,openHistory}:{api:ApiClient;openHistory:()=>void}) {
  const [data,setData]=useState<AnalyticsOverview|null>(null); const [error,setError]=useState('');
  const [draft,setDraft]=useState<AnalyticsFilters>(empty); const [applied,setApplied]=useState<AnalyticsFilters>(empty);
  const [loading,setLoading]=useState(false); const sequence=useRef(0);
  const load=useCallback(async(filters:AnalyticsFilters)=>{
    const id=++sequence.current; setError(''); setLoading(true);
    try {const next=await api.analyticsOverview(filters); if(id===sequence.current){setData(next);setApplied(filters);}}
    catch(err){if(id===sequence.current) setError(err instanceof Error?err.message:'Overview unavailable.');}
    finally {if(id===sequence.current) setLoading(false);}
  },[api]);
  useEffect(()=>{const current=sequence;void load(empty);return()=>{current.current++;};},[load]);
  const filtered=Boolean(applied.date_from || applied.date_to || applied.vendor.trim() || applied.outcome);
  function download() {
    if (!data) return;
    const rows:[unknown,unknown][]=[['Scope','Current workspace; filters apply to recorded run start dates (UTC)'],['Date from',applied.date_from],['Date to',applied.date_to],['Vendor contains',applied.vendor.trim()],['Run outcome',applied.outcome],['Identified invoices',data.identified_invoices],['Recorded runs',data.total_runs],['Active runs',data.active_runs],['Failed or interrupted runs',data.failed_runs],['Runs marked for review',data.review_runs],['Flagged active versions',data.flagged_active_versions],['Simulated receipts',data.simulated_receipts]];
    data.paid_by_currency.forEach(row=>rows.push([`Simulated amount (${row.currency})`,row.amount],[`Receipts (${row.currency})`,row.receipts]));
    rows.push(['Note','Runs include retries, duplicates and batch extraction. Filtered receipts require a matching paid run; review counts are not outstanding balances.']);
    const url=URL.createObjectURL(new Blob([rowsCsv(rows)],{type:'text/csv;charset=utf-8'}));
    const anchor=document.createElement('a'); anchor.href=url; anchor.download='ledger-overview.csv'; anchor.click(); window.setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return <section><div className="page-heading"><div><span className="page-eyebrow">INVOICE ANALYTICS</span><h1>Ledger overview</h1><p>Explore invoice activity and simulated payments across this workspace, including records not currently loaded in the inbox.</p></div><div className="report-actions"><button className="button button-outline" onClick={download} disabled={!data || loading}>Download CSV</button><button className="button button-outline" onClick={()=>void load(applied)} disabled={loading}>Refresh overview</button></div></div>
    <form className="content-card analytics-filters" onSubmit={event=>{event.preventDefault();void load({...draft,vendor:draft.vendor.trim()});}}><div className="analytics-filter-grid">
      <label>From date (UTC)<input type="date" value={draft.date_from} onChange={e=>setDraft({...draft,date_from:e.target.value})}/></label><label>To date (UTC)<input type="date" value={draft.date_to} onChange={e=>setDraft({...draft,date_to:e.target.value})}/></label><label>Vendor contains<input maxLength={128} value={draft.vendor} onChange={e=>setDraft({...draft,vendor:e.target.value})} placeholder="Vendor name"/></label><label>Run outcome<select value={draft.outcome} onChange={e=>setDraft({...draft,outcome:e.target.value as Outcome | ''})}><option value="">All outcomes</option><option value="paid">Paid (simulated)</option><option value="needs_review">Needs review</option><option value="rejected">Rejected</option><option value="duplicate">Duplicate</option><option value="superseded">Superseded</option></select></label>
      </div><div className="analytics-filter-actions"><button className="button button-primary" type="submit" disabled={loading}>Apply filters</button><button className="button button-outline" type="button" disabled={loading} onClick={()=>{setDraft(empty);void load(empty);}}>Clear filters</button></div><p>Dates use the run start date in UTC. Filtered receipts require a matching paid run; vendor filters exclude runs without a usable vendor identity.</p></form>
    {loading && <div role="status" className="help-copy">Loading overview…</div>}{error && <div className="inline-error" role="alert">{error}{data && ' The previous results remain visible.'}</div>}
    {!data ? <div className="loading-card">{error?'The overview could not be loaded.':'Loading ledger overview…'}</div> : <><p className="analytics-scope">{filtered ? `Filtered results: ${[applied.date_from && `from ${applied.date_from}`,applied.date_to && `to ${applied.date_to}`,applied.vendor && `vendor contains ${applied.vendor}`,applied.outcome && `outcome ${applied.outcome.replaceAll('_',' ')}`].filter(Boolean).join(' · ')}` : 'Showing all workspace records'}</p><div className="analytics-grid">{[
      ['Identified invoices',data.identified_invoices,filtered?'Distinct payables linked to matching runs':'Distinct vendor and invoice-number pairs'],
      ['Recorded runs',data.total_runs,'Includes retries, duplicates and batch extraction'],
      ['Active runs',data.active_runs,'Queued or processing'],
      ['Failed or interrupted runs',data.failed_runs,'Recorded attempts'],
      ['Runs marked for review',data.review_runs,'Recorded run outcomes, not an outstanding balance'],
      ['Flagged active versions',data.flagged_active_versions,filtered?'Current flags on versions linked to matching runs':'Identity or version flags'],
    ].map(([title,value,note])=><div className="content-card analytics-card" key={title}><span>{title}</span><strong>{value}</strong><small>{note}</small></div>)}</div>
      <div className="content-card analytics-visual"><h2>Run activity</h2><p>Counts of processing attempts, not unique invoices. Categories may overlap.</p>{[
        ['Recorded runs',data.total_runs],['Active',data.active_runs],['Failed or interrupted',data.failed_runs],['Marked for review',data.review_runs],
      ].map(([label,value]) => <div className="analytics-bar-row" key={label}><span>{label}</span><div className="analytics-bar-track"><div className="analytics-bar-fill" style={{width:`${data.total_runs ? Math.min(100,Number(value)/data.total_runs*100) : 0}%`}} /></div><strong>{value}</strong></div>)}</div>
      <div className="content-card analytics-payments"><h2>Simulated receipts</h2><p>{data.simulated_receipts} recorded receipt(s). Amounts are kept separate by currency; no real money moved.</p>{data.paid_by_currency.length ? <ul>{data.paid_by_currency.map(row=><li key={row.currency}><span>{row.currency} · {row.receipts} receipt(s)</span><strong>{formatAmount(row.amount,row.currency)}</strong></li>)}</ul> : <p>No simulated receipts recorded.</p>}</div>
      <div className="content-card analytics-note"><p>These are ledger counts, not savings, proven fraud or a list of currently payable invoices. Records without a usable identity are not counted as identified invoices. History below is not filtered to these totals.</p><button className="text-button" onClick={openHistory}>Open full run history</button></div></>}
  </section>;
}
