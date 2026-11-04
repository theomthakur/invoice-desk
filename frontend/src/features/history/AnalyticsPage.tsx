import { useCallback, useEffect, useState } from 'react';
import { ApiClient } from '../../api/client';
import { formatAmount } from '../../components/Status';
import { rowsCsv } from '../runs/reportCsv';
import type { AnalyticsOverview } from '../../types/api';

export function AnalyticsPage({api,openHistory}:{api:ApiClient;openHistory:()=>void}) {
  const [data,setData]=useState<AnalyticsOverview|null>(null); const [error,setError]=useState('');
  const load=useCallback(async()=>{setError('');try {setData(await api.analyticsOverview());} catch(err){setError(err instanceof Error?err.message:'Overview unavailable.');}},[api]);
  useEffect(()=>{void load();},[load]);
  function download() {
    if (!data) return;
    const rows:[unknown,unknown][]=[['Scope','Current workspace; all recorded runs'],['Identified invoices',data.identified_invoices],['Recorded runs',data.total_runs],['Active runs',data.active_runs],['Failed or interrupted runs',data.failed_runs],['Runs marked for review',data.review_runs],['Flagged active versions',data.flagged_active_versions],['Simulated receipts',data.simulated_receipts]];
    data.paid_by_currency.forEach(row=>rows.push([`Simulated amount (${row.currency})`,row.amount],[`Receipts (${row.currency})`,row.receipts]));
    rows.push(['Note','Recorded runs include retries, duplicates and batch extraction. Review counts are historical, not an outstanding balance.']);
    const url=URL.createObjectURL(new Blob([rowsCsv(rows)],{type:'text/csv;charset=utf-8'}));
    const anchor=document.createElement('a'); anchor.href=url; anchor.download='ledger-overview.csv'; anchor.click(); window.setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return <section><div className="page-heading"><div><span className="page-eyebrow">WORKSPACE LEDGER</span><h1>Ledger overview</h1><p>Counts across this workspace, including records not currently loaded in the inbox.</p></div><div className="report-actions"><button className="button button-outline" onClick={download} disabled={!data}>Download CSV</button><button className="button button-outline" onClick={()=>void load()}>Refresh overview</button></div></div>
    {error && <div className="inline-error" role="alert">{error}</div>}
    {!data ? <div className="loading-card">{error?'The overview could not be loaded.':'Loading ledger overview…'}</div> : <><div className="analytics-grid">{[
      ['Identified invoices',data.identified_invoices,'Distinct vendor and invoice-number pairs'],
      ['Recorded runs',data.total_runs,'Includes retries, duplicates and batch extraction'],
      ['Active runs',data.active_runs,'Queued or processing'],
      ['Failed or interrupted runs',data.failed_runs,'Recorded attempts'],
      ['Runs marked for review',data.review_runs,'Historical run outcomes, not an outstanding balance'],
      ['Flagged active versions',data.flagged_active_versions,'Identity or version flags'],
    ].map(([title,value,note])=><div className="content-card analytics-card" key={title}><span>{title}</span><strong>{value}</strong><small>{note}</small></div>)}</div>
      <div className="content-card analytics-payments"><h2>Simulated receipts</h2><p>{data.simulated_receipts} recorded receipt(s). Amounts are kept separate by currency; no real money moved.</p>{data.paid_by_currency.length ? <ul>{data.paid_by_currency.map(row=><li key={row.currency}><span>{row.currency} · {row.receipts} receipt(s)</span><strong>{formatAmount(row.amount,row.currency)}</strong></li>)}</ul> : <p>No simulated receipts recorded.</p>}</div>
      <div className="content-card analytics-note"><p>These are historical ledger counts, not savings, proven fraud or a list of currently payable invoices. Records without a usable identity are not counted as identified invoices. This overview does not yet offer date or vendor filters.</p><button className="text-button" onClick={openHistory}>Open run history</button></div></>}
  </section>;
}
