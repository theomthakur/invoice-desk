import { AlertCircle, ArrowRightLeft, CheckCircle2, ClipboardCheck, LoaderCircle, XCircle } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { ApiClient } from '../../api/client';
import type { DepartmentRouting, HandoffChoice, ReviewOptions, RunResult } from '../../types/api';

type ReviewAction = 'approve' | 'reject' | 'route';
const rejectionReasons: HandoffChoice[] = [
  { value: 'unverified', label: 'The invoice details could not be verified' },
  { value: 'correction', label: 'The source invoice needs correction' },
  { value: 'payment_policy', label: 'The currency or payment policy is unsupported' },
  { value: 'identity', label: 'The vendor or invoice identity remains unresolved' },
];

export function ReviewPanel({ api, run, onReviewed }: { api: ApiClient; run: RunResult; onReviewed: (run: RunResult) => void }) {
  const [options, setOptions] = useState<ReviewOptions | null>(null);
  const [routing, setRouting] = useState<DepartmentRouting | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [routingError, setRoutingError] = useState<string | null>(null);
  const [action, setAction] = useState<ReviewAction>('approve');
  const [reason, setReason] = useState('');
  const [department, setDepartment] = useState('');
  const [comment, setComment] = useState('');
  const [checked, setChecked] = useState<string[]>([]);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const pending = useRef(false);
  const request = useRef<{ payload: string; token: string } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void api.reviewOptions(run.run_id, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setOptions(result);
          setAction(result.can_approve ? 'approve' : result.can_reject ? 'reject' : 'route');
        }
      })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Review options are unavailable.'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    void api.routing(run.run_id, controller.signal)
      .then(result => { if (!controller.signal.aborted) setRouting(result); })
      .catch((err: unknown) => { if (!controller.signal.aborted) setRoutingError(err instanceof Error ? err.message : 'Department routing is unavailable.'); });
    return () => controller.abort();
  }, [api, run.run_id]);

  const approvalReasons: HandoffChoice[] = [
    { value: 'source_verified', label: 'I verified the original document and all flagged details' },
    ...(options?.resolvable_codes.includes('VENDOR_IDENTITY_CHANGE') ? [{ value: 'vendor_verified', label: 'I verified the vendor name or identity change' }] : []),
    ...(options?.resolvable_codes.includes('REVISION_CONFLICT') ? [{ value: 'version_selected', label: 'I compared the versions and selected this invoice' }] : []),
    ...(run.requires_scrutiny ? [{ value: 'additional_checks', label: 'I completed the required additional checks' }] : []),
  ];
  const reasons = action === 'approve' ? approvalReasons : action === 'reject' ? rejectionReasons : routing?.reasons ?? [];
  const selectedReason = reasons.find(item => item.value === reason);
  const allChecked = options?.resolvable_codes.every(code => checked.includes(code)) ?? false;
  const permitted = action === 'approve' ? options?.can_approve && allChecked && acknowledged
    : action === 'reject' ? options?.can_reject : routing?.can_route && Boolean(department);
  const canSubmit = Boolean(permitted && selectedReason && !busy);

  async function submit() {
    if (pending.current || !options || !canSubmit || !selectedReason) return;
    pending.current = true; setBusy(true); setError(null); setSaved(false);
    const resolved = action === 'approve' ? checked : [];
    const note = comment.trim();
    const payload = JSON.stringify({ action, reason, department: action === 'route' ? department : null, note, resolved, version: options.version_id, hash: options.content_hash });
    if (request.current?.payload !== payload) request.current = { payload, token: `review_${crypto.randomUUID()}` };
    try {
      if (action === 'route') {
        const assignment = await api.routeReview(run.run_id, { version_id: options.version_id, content_hash: options.content_hash, department, reason_code: reason, note, idempotency_token: request.current.token });
        setRouting(previous => previous ? { ...previous, assignment, can_route: assignment.active } : previous);
        setSaved(true);
        // Refresh the actual result and audit events without changing the payment decision.
        onReviewed(await api.getRun(run.run_id));
      } else {
        if (!options.version_id || !options.content_hash) throw new Error('Reopen the invoice to load its current version.');
        const response = await api.review(run.run_id, {
          version_id: options.version_id, content_hash: options.content_hash, decision: action,
          comment: `Reason: ${selectedReason.label}.${note ? `\nAdditional note: ${note}` : ''}`,
          resolved_codes: resolved, idempotency_token: request.current.token,
        });
        if (!response.accepted) throw new Error(response.message || 'The review was not accepted.');
        onReviewed(response.run);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not record the review.');
    } finally { pending.current = false; setBusy(false); }
  }

  if (loading) return <div className="content-card review-card"><LoaderCircle className="spin" size={17} /> Loading review options…</div>;
  if (!options) return <div className="content-card review-card"><AlertCircle size={17} /><span>{error ?? 'Review options are unavailable.'}</span></div>;
  return <section className="content-card review-card" aria-label="Simulated review">
    <div className="card-heading"><div><span className="mini-label">REVIEW REQUIRED</span><h2>Review this invoice</h2><p>Choose an action and a reason. Add a note only if there is more to explain. Reviews and payments are simulated.</p></div><ClipboardCheck size={19} /></div>
    {routing?.assignment && <div className="handoff-summary" role={saved ? 'status' : undefined}><ArrowRightLeft size={17} /><div><strong>{routing.assignment.active ? 'Assigned to' : 'Previously assigned to'} {routing.assignment.department_label}</strong><p>{routing.assignment.reason}</p>{routing.assignment.note && <p>{routing.assignment.note}</p>}<small>Internal workspace record only. No department notification was sent.</small></div></div>}
    {options.blocked_codes.length > 0 && <div className="review-blocked"><AlertCircle size={16} /><div><strong>Cannot approve yet</strong><p>These issues cannot be cleared by this review: {options.blocked_codes.map(code => code.replaceAll('_', ' ').toLowerCase()).join(', ')}.</p></div></div>}
    <div className="review-form-grid"><label className="review-select-label" htmlFor="review-action">Review action<select id="review-action" value={action} disabled={busy} onChange={event => { setAction(event.target.value as ReviewAction); setReason(''); setAcknowledged(false); setError(null); setSaved(false); }}><option value="approve" disabled={!options.can_approve}>Approve and simulate payment</option><option value="reject" disabled={!options.can_reject}>Reject invoice</option><option value="route" disabled={!routing?.can_route}>Assign to a department</option></select></label>
      {action === 'route' && <label className="review-select-label" htmlFor="review-department">Department<select id="review-department" value={department} disabled={busy || !routing?.can_route} onChange={event => { setDepartment(event.target.value); setSaved(false); }}><option value="">Select a department</option>{routing?.departments.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>}
      <label className="review-select-label review-reason" htmlFor="review-reason">Decision reason<select id="review-reason" value={reason} disabled={busy} onChange={event => { setReason(event.target.value); setSaved(false); }}><option value="">Select a reason</option>{reasons.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
    </div>
    {action === 'approve' && options.resolvable_codes.length > 0 && <fieldset className="review-checks"><legend>Confirm each item you verified</legend>{options.resolvable_codes.map(code => {
      const details = run.findings.filter(finding => finding.code === code);
      return <label key={code}><input type="checkbox" checked={checked.includes(code)} disabled={busy} onChange={event => setChecked(previous => event.target.checked ? [...previous, code] : previous.filter(value => value !== code))} /><span><strong>{code.replaceAll('_', ' ').toLowerCase()}</strong>{details.map((item, index) => <small key={`${item.code}-${index}`}>{item.message}</small>)}</span></label>;
    })}</fieldset>}
    {action === 'approve' && options.can_approve && <label className="review-ack"><input type="checkbox" checked={acknowledged} disabled={busy} onChange={event => setAcknowledged(event.target.checked)} /><span>I checked the source and understand this approval can trigger a mock payment.</span></label>}
    {action === 'route' && <p className="routing-explanation">This records a department handoff and keeps the invoice on hold. It does not send an email, notify a team, or authorize payment.</p>}
    <label className="review-comment" htmlFor="review-comment">Decision note <span>optional</span></label><textarea id="review-comment" value={comment} disabled={busy} maxLength={1500} onChange={event => setComment(event.target.value)} placeholder="Add context, a reference, or anything the next reviewer should know." />
    {action === 'route' && routingError && <div className="inline-error review-error" role="alert">{routingError}</div>}
    {error && <div className="inline-error review-error" role="alert">{error} Reopen the invoice if its version or status changed.</div>}
    <div className="review-actions"><button className={`button ${action === 'reject' ? 'button-outline' : 'button-primary'}`} disabled={!canSubmit} onClick={() => void submit()}>{busy ? <LoaderCircle size={15} className="spin" /> : action === 'approve' ? <CheckCircle2 size={15} /> : action === 'route' ? <ArrowRightLeft size={15} /> : <XCircle size={15} />}{busy ? 'Recording…' : action === 'approve' ? 'Approve & simulate payment' : action === 'route' ? 'Record department handoff' : 'Reject invoice'}</button></div>
  </section>;
}
