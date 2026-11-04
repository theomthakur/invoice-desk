import type { RunResult } from '../../types/api';

/** CSV for a spreadsheet. Prefix formula-like values and quote every cell. */
function cell(value: unknown): string {
  let text = value == null ? '' : String(value);
  if (/^[\s\uFEFF]*[=+\-@\t\r]/u.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"','""')}"`;
}
export function rowsCsv(rows: [unknown, unknown][]): string {
  return '\uFEFF'+[['Field','Value'],...rows].map(row=>row.map(cell).join(',')).join('\r\n')+'\r\n';
}
export function decisionCsv(run: RunResult): string {
  const rows: [string, unknown][] = [
    ['Report','Invoice decision report'], ['Payment system','Simulated only'], ['Run',run.run_id],
    ['Version',run.version_id], ['Linked run',run.attempt_of], ['Link type',run.relation_kind], ['Filename',run.filename], ['Lifecycle',run.lifecycle],
    ['Outcome',run.outcome === 'paid' && !run.receipt ? 'Receipt unavailable' : run.outcome],
    ['Received',run.started_at], ['Finished',run.finished_at], ['Invoice number',run.invoice?.invoice_number],
    ['Vendor',run.invoice?.vendor_name], ['Currency',run.invoice?.currency], ['Invoice date',run.invoice?.invoice_date],
    ['Due date',run.invoice?.due_date], ['Subtotal',run.invoice?.subtotal], ['Tax',run.invoice?.tax],
    ['Shipping',run.invoice?.shipping], ['Total',run.invoice?.total],
  ];
  run.invoice?.lines.forEach((line,index)=>{const n=index+1; rows.push([`Line ${n} description`,line.description],[`Line ${n} quantity`,line.quantity],[`Line ${n} unit price`,line.unit_price],[`Line ${n} amount`,line.amount]);});
  run.findings.forEach((finding,index)=>rows.push([`Finding ${index+1}`,`${finding.severity}: ${finding.message}`]));
  run.approval.forEach((decision,index)=>rows.push([`Decision ${index+1}`,`${decision.stage} · ${decision.action} · ${decision.actor}: ${decision.rationale}`]));
  rows.push(['Simulated receipt',run.receipt?.transaction_id],['Receipt amount',run.receipt?.amount],['Receipt currency',run.receipt?.currency]);
  return rowsCsv(rows);
}
export function downloadDecisionCsv(run: RunResult) {
  const url=URL.createObjectURL(new Blob([decisionCsv(run)],{type:'text/csv;charset=utf-8'}));
  const anchor=document.createElement('a'); anchor.href=url; anchor.download=`invoice-decision-${run.run_id}.csv`; anchor.click();
  window.setTimeout(()=>URL.revokeObjectURL(url),1000);
}
