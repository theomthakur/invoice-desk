import type { Lifecycle, Outcome } from '../types/api';

export function statusLabel(status?: Lifecycle | Outcome | 'reported_paid' | null) {
  const labels: Record<string, string> = {
    paid: 'Paid (simulated)', reported_paid: 'Payment reported', rejected: 'Rejected', needs_review: 'Needs review', duplicate: 'Duplicate',
    superseded: 'Superseded', queued: 'Queued', running: 'Processing', completed: 'Completed',
    failed: 'Failed', interrupted: 'Interrupted',
  };
  return status ? labels[status] ?? status : 'Not started';
}

export function Status({ status }: { status?: Lifecycle | Outcome | 'reported_paid' | null }) {
  const type = status === 'paid' || status === 'reported_paid' || status === 'completed' ? 'success'
    : status === 'rejected' || status === 'failed' ? 'danger'
      : status === 'needs_review' || status === 'interrupted' ? 'warning' : 'neutral';
  return <span className={`status status-${type}`}><span className="status-dot" />{statusLabel(status)}</span>;
}

export function formatAmount(amount?: string | null, currency?: string | null) {
  if (amount == null || amount === '') return 'Not available';
  // Keep the original decimal string; do not round through a JavaScript number.
  const match = /^(-?)(\d+)(\.\d+)?$/.exec(amount);
  if (!match) return `${currency ?? ''} ${amount}`.trim();
  const grouped = match[2].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const symbol = currency === 'USD' ? '$' : currency === 'EUR' ? '€' : `${currency ?? ''} `;
  return `${match[1]}${symbol}${grouped}${match[3] ?? ''}`.trim();
}
