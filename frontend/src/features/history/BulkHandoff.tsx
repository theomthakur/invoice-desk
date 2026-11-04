import { useEffect, useRef, useState } from 'react';
import { ApiClient } from '../../api/client';
import type { DepartmentRouting, HistoryItem } from '../../types/api';

export function BulkHandoff({api,items,close,refresh}:{api:ApiClient;items:HistoryItem[];close:()=>void;refresh:()=>void}) {
  const [options,setOptions]=useState<DepartmentRouting|null>(null);
  const [department,setDepartment]=useState(''); const [reason,setReason]=useState(''); const [note,setNote]=useState('');
  const [confirmed,setConfirmed]=useState(false); const [busy,setBusy]=useState(false); const [results,setResults]=useState<string[]>([]); const [error,setError]=useState('');
  const running=useRef(false);
  const eligible=items.filter(item=>item.outcome==='needs_review');
  const firstEligibleId = eligible[0]?.run_id;
  useEffect(()=>{const controller=new AbortController(); if(firstEligibleId) void api.routing(firstEligibleId,controller.signal).then(setOptions).catch((err:unknown)=>{if(!controller.signal.aborted)setError(err instanceof Error?err.message:'Unable to load options');}); return()=>controller.abort();},[api,firstEligibleId]);
  async function submit() {
    if(running.current || !confirmed || !department || !reason) return;
    running.current=true;setBusy(true);setError(''); const lines:string[]=[];
    for(const item of items) {
      const label=item.invoice_number ?? item.filename ?? item.run_id;
      if(item.outcome!=='needs_review') {lines.push(`${label}: skipped, not awaiting review.`);continue;}
      try {
        const [run,routing]=await Promise.all([api.getRun(item.run_id),api.routing(item.run_id)]);
        if(!routing.can_route || !routing.departments.some(d=>d.value===department) || !routing.reasons.some(r=>r.value===reason)) {lines.push(`${label}: skipped, assignment unavailable.`);continue;}
        await api.routeReview(item.run_id,{version_id:run.version_id ?? null,content_hash:run.content_hash ?? null,department,reason_code:reason,note:note.trim(),idempotency_token:`handoff_${crypto.randomUUID()}`});
        lines.push(`${label}: handoff recorded.`);
      } catch(err) {lines.push(`${label}: ${err instanceof Error ? err.message : 'Could not record handoff.'} Check the invoice before retrying.`);}
      setResults([...lines]);
    }
    setResults(lines);setBusy(false);running.current=false;setConfirmed(false);refresh();
  }
  return <section className="bulk-handoff" aria-label="Assign selected invoices"><h3>Assign selected invoices</h3><p>Records an internal handoff for eligible review holds only. No notification is sent and payment remains on hold. Each invoice is checked separately.</p>{error && <p role="alert">{error}</p>}
    {options && <div className="bulk-fields"><label>Department<select aria-label="Department" value={department} disabled={busy} onChange={e=>setDepartment(e.target.value)}><option value="">Choose a department</option>{options.departments.map(d=><option value={d.value} key={d.value}>{d.label}</option>)}</select></label><label>Reason<select aria-label="Reason" value={reason} disabled={busy} onChange={e=>setReason(e.target.value)}><option value="">Choose a reason</option>{options.reasons.map(r=><option value={r.value} key={r.value}>{r.label}</option>)}</select></label><label>Optional note<input value={note} maxLength={1500} disabled={busy} onChange={e=>setNote(e.target.value)}/></label></div>}
    {!eligible.length && <p>Select at least one invoice marked Needs review.</p>}<label className="bulk-confirm"><input type="checkbox" disabled={busy} checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/> I want to record this handoff for eligible selected invoices.</label><div className="bulk-buttons"><button className="button button-primary" disabled={busy || !confirmed || !department || !reason || !eligible.length} onClick={()=>void submit()}>{busy?'Recording…':'Record handoffs'}</button><button className="button button-outline" disabled={busy} onClick={close}>Close</button></div>{results.length>0 && <div role="status"><strong>Results</strong><ul>{results.map((line,i)=><li key={i}>{line}</li>)}</ul></div>}
  </section>;
}
