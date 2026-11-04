import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiClient } from '../../api/client';
import type { InvoiceLookupResult } from '../../types/api';

export function InvoiceLookup({api, initialNumber, openRun}:{api:ApiClient; initialNumber:string; openRun:(id:string)=>void}) {
  const [number,setNumber]=useState(initialNumber);
  const [result,setResult]=useState<InvoiceLookupResult|null>(null);
  const [error,setError]=useState(''); const [loading,setLoading]=useState(false);
  const sequence=useRef(0);
  const search=useCallback(async (value:string) => {
    if (!value.trim()) return;
    const id=++sequence.current;
    setLoading(true); setError(''); setResult(null);
    try { const found=await api.lookupInvoice(value.trim()); if(id===sequence.current) setResult(found); }
    catch (err) {if(id===sequence.current) setError(err instanceof Error ? err.message : 'Lookup failed.');}
    finally {if(id===sequence.current) setLoading(false);}
  },[api]);
  useEffect(()=>{const currentSequence=sequence; if(initialNumber) void search(initialNumber); return () => {currentSequence.current++;};},[initialNumber,search]);
  return <section className="lookup-page"><div className="page-heading"><div><span className="page-eyebrow">WORKSPACE LEDGER</span><h1>Find invoice</h1><p>Look up an exact invoice number across your workspace, including other versions.</p></div></div>
    <form className="content-card lookup-form" onSubmit={event=>{event.preventDefault();void search(number);}}><label htmlFor="lookup-number">Invoice number</label><div><input id="lookup-number" maxLength={128} value={number} onChange={event=>setNumber(event.target.value)} placeholder="For example, INV-1004"/><button className="button button-primary" disabled={loading || !number.trim()}>{loading?'Searching…':'Find invoice'}</button></div><p>Numbers are matched ignoring case, spaces and punctuation. Choose a vendor if several invoices share the number.</p></form>
    {error && <div className="inline-error" role="alert">{error}</div>}
    {result && <div aria-live="polite">{result.matches.length === 0 && <div className="content-card lookup-result">No matching invoice was found in this workspace. Records without a usable invoice identity are not included.</div>}{result.truncated && <p className="inline-warning">More than 50 vendors share this number; only the first 50 are shown.</p>}{result.matches.map(match=><article className="content-card lookup-result" key={match.payable_id}><h2>{match.number} · {match.vendor}</h2><p>{match.paid ? 'A simulated payment is recorded for this invoice. Check its version and receipt before relying on it.' : 'No simulated payment is recorded for this invoice.'}</p>{match.versions_truncated && <p>Only the first 100 versions are shown.</p>}{match.versions.length ? <ul>{match.versions.map(version=><li key={version.version_id}><div><strong>{version.revision ? `Revision ${version.revision}` : 'Version'}</strong> · {version.status}{version.identity_flag && ` · hold: ${version.identity_flag.replaceAll('_',' ')}`}{version.version_id===match.payment_version_id && ' · simulated payment version'}<small>Recorded run outcome: {version.run_outcome?.replaceAll('_',' ') ?? 'not available'}</small></div>{version.run_id && <button className="button button-outline" onClick={()=>openRun(version.run_id!)}>Open record</button>}</li>)}</ul> : <p>No versions are recorded.</p>}</article>)}</div>}
  </section>;
}
