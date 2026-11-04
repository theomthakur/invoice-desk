import { AlertCircle, CheckCircle2, ClipboardCheck, LoaderCircle } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { ApiClient } from '../../api/client';
import type { ReviewOptions, RunResult } from '../../types/api';

export function ReviewPanel({ api, run, onReviewed }: { api: ApiClient; run: RunResult; onReviewed: (run: RunResult) => void }) {
  const [options, setOptions] = useState<ReviewOptions | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [comment, setComment] = useState('');
  const [checked, setChecked] = useState<string[]>([]);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const request = useRef<{ payload: string; token: string } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void api.reviewOptions(run.run_id, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setOptions(result); })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Review options are unavailable.'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, run.run_id]);

  async function submit(decision: 'approve' | 'reject') {
    if (pending.current || !options?.version_id || !options.content_hash || !comment.trim()) return;
    pending.current = true; setBusy(true); setError(null);
    const resolved = decision === 'approve' ? checked : [];
    const payload = JSON.stringify({ version_id: options.version_id, content_hash: options.content_hash, decision, comment: comment.trim(), resolved_codes: resolved });
    if (request.current?.payload !== payload) request.current = { payload, token: `review_${crypto.randomUUID()}` };
    try {
      const response = await api.review(run.run_id, { ...JSON.parse(payload) as { version_id: string; content_hash: string; decision: 'approve' | 'reject'; comment: string; resolved_codes: string[] }, idempotency_token: request.current.token });
      if (!response.accepted) throw new Error(response.message || 'The review was not accepted.');
      onReviewed(response.run);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not record the review.');
    } finally { pending.current = false; setBusy(false); }
  }

  if (loading) return <div className="content-card review-card"><LoaderCircle className="spin" size={17} /> Loading review options…</div>;
  if (!options) return <div className="content-card review-card"><AlertCircle size={17} /><span>{error ?? 'Review options are unavailable.'}</span></div>;
  const allChecked = options.resolvable_codes.every((code) => checked.includes(code));
  const canApprove = options.can_approve && allChecked && acknowledged && Boolean(comment.trim()) && !busy;
  const canReject = options.can_reject && Boolean(comment.trim()) && !busy;
  return <section className="content-card review-card" aria-label="Simulated review">
    <div className="card-heading"><div><span className="mini-label">REVIEW REQUIRED</span><h2>Review this invoice</h2><p>Check the original document and record your decision. This review and any payment are simulated.</p></div><ClipboardCheck size={19} /></div>
    {options.blocked_codes.length > 0 && <div className="review-blocked"><AlertCircle size={16} /><div><strong>Cannot approve yet</strong><p>These issues cannot be cleared by this review: {options.blocked_codes.map((code) => code.replaceAll('_', ' ').toLowerCase()).join(', ')}.</p></div></div>}
    {options.resolvable_codes.length > 0 && <fieldset className="review-checks"><legend>Confirm each item you verified</legend>{options.resolvable_codes.map((code) => {
      const details = run.findings.filter((finding) => finding.code === code);
      return <label key={code}><input type="checkbox" checked={checked.includes(code)} disabled={busy} onChange={(event) => setChecked((previous) => event.target.checked ? [...previous, code] : previous.filter((value) => value !== code))} /><span><strong>{code.replaceAll('_', ' ').toLowerCase()}</strong>{details.map((item, index) => <small key={`${item.code}-${index}`}>{item.message}</small>)}</span></label>;
    })}</fieldset>}
    {options.can_approve && <label className="review-ack"><input type="checkbox" checked={acknowledged} disabled={busy} onChange={(event) => setAcknowledged(event.target.checked)} /><span>I checked the source and understand this approval can trigger a mock payment.</span></label>}
    <label className="review-comment" htmlFor="review-comment">Decision note <span>required</span></label><textarea id="review-comment" value={comment} disabled={busy} maxLength={2000} onChange={(event) => setComment(event.target.value)} placeholder="What did you check, or why are you rejecting this invoice?" />
    {error && <div className="inline-error review-error" role="alert">{error} If the invoice changed, reopen it and review the latest version.</div>}
    <div className="review-actions"><button className="button button-outline" disabled={!canReject} onClick={() => void submit('reject')}>Reject invoice</button><button className="button button-primary" disabled={!canApprove} onClick={() => void submit('approve')}>{busy ? <LoaderCircle size={15} className="spin" /> : <CheckCircle2 size={15} />}Approve &amp; simulate payment</button></div>
  </section>;
}
