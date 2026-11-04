import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiError } from '../src/api/client';
import { ReviewPanel } from '../src/features/review/ReviewPanel';
import type { DepartmentRouting, RunResult } from '../src/types/api';

const held: RunResult = {
  run_id: 'run_1', workspace_id: 'ws_1', lifecycle: 'completed', outcome: 'needs_review',
  version_id: 'ver_1', content_hash: 'a'.repeat(64), findings: [{ code: 'VENDOR_IDENTITY_CHANGE', severity: 'REVIEW', message: 'Verify the former name.' }],
  approval: [], requires_scrutiny: false,
};
const options = { version_id: 'ver_1', content_hash: 'a'.repeat(64), resolvable_codes: ['VENDOR_IDENTITY_CHANGE'], blocked_codes: [], can_approve: true, can_reject: true };
const routing: DepartmentRouting = { assignment: null, can_route: true, departments: [{value: 'procurement', label: 'Procurement'}], reasons: [{value: 'inventory_check', label: 'Inventory needs checking'}] };
function client() {
  const api = new ApiClient();
  vi.spyOn(api, 'routing').mockResolvedValue(routing);
  vi.spyOn(api, 'reviewOptions').mockResolvedValue(options);
  return api;
}

describe('simulated human review', () => {
  it('requires a selected reason and all source checks, but no typed note', async () => {
    const api = client(); const onReviewed = vi.fn();
    const review = vi.spyOn(api, 'review').mockResolvedValue({ accepted: true, message: 'Recorded', run: { ...held, outcome: 'paid' } });
    const user = userEvent.setup(); render(<ReviewPanel api={api} run={held} onReviewed={onReviewed} />);
    await screen.findByText('Confirm each item you verified');
    await user.selectOptions(screen.getByLabelText('Decision reason'), 'vendor_verified');
    expect(screen.getByRole('button', { name: /approve & simulate payment/i })).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /vendor identity change/i }));
    expect(screen.getByRole('button', { name: /approve & simulate payment/i })).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /checked the source/i }));
    await user.click(screen.getByRole('button', { name: /approve & simulate payment/i }));
    expect(review).toHaveBeenCalledWith('run_1', expect.objectContaining({ version_id: 'ver_1', content_hash: 'a'.repeat(64), decision: 'approve', resolved_codes: ['VENDOR_IDENTITY_CHANGE'], comment: 'Reason: I verified the vendor name or identity change.' }));
    expect(onReviewed).toHaveBeenCalledWith(expect.objectContaining({ outcome: 'paid' }));
  });
  it('cannot approve a currency hold but allows a reasoned rejection', async () => {
    const api = client();
    vi.mocked(api.reviewOptions).mockResolvedValue({ ...options, resolvable_codes: [], blocked_codes: ['UNSUPPORTED_CURRENCY'], can_approve: false });
    const review = vi.spyOn(api, 'review').mockResolvedValue({ accepted: true, message: 'Rejected', run: { ...held, outcome: 'rejected' } });
    const user = userEvent.setup(); render(<ReviewPanel api={api} run={held} onReviewed={vi.fn()} />);
    await screen.findByText('Cannot approve yet');
    expect(screen.getByRole('option', { name: 'Approve and simulate payment' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /reject invoice/i })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Decision reason'), 'payment_policy');
    await user.type(screen.getByLabelText(/decision note/i), 'Needs a separate policy.');
    await user.click(screen.getByRole('button', { name: /reject invoice/i }));
    expect(review).toHaveBeenCalledWith('run_1', expect.objectContaining({ decision: 'reject', resolved_codes: [], comment: expect.stringContaining('Additional note: Needs a separate policy.') }));
  });
  it('shows a stale review error and leaves the invoice on hold', async () => {
    const api = client(); const onReviewed = vi.fn();
    vi.spyOn(api, 'review').mockRejectedValue(new ApiError('Invoice changed.', 409, 'stale_decision'));
    const user = userEvent.setup(); render(<ReviewPanel api={api} run={held} onReviewed={onReviewed} />);
    await screen.findByText('Confirm each item you verified');
    await user.selectOptions(screen.getByLabelText('Review action'), 'reject');
    await user.selectOptions(screen.getByLabelText('Decision reason'), 'correction');
    await user.click(screen.getByRole('button', { name: /reject invoice/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Invoice changed.');
    expect(onReviewed).not.toHaveBeenCalled();
  });
  it('records a department and reason without a note or payment authorization', async () => {
    const api = client(); const onReviewed = vi.fn();
    const assignment = { handoff_id: 'handoff_1', department: 'procurement', department_label: 'Procurement', reason_code: 'inventory_check', reason: 'Inventory needs checking', note: '', created_at: '2026-11-04T10:00:00Z', active: true, notification_sent: false as const };
    const handoff = vi.spyOn(api, 'routeReview').mockResolvedValue(assignment);
    const review = vi.spyOn(api, 'review');
    vi.spyOn(api, 'getRun').mockResolvedValue(held);
    const user = userEvent.setup(); render(<ReviewPanel api={api} run={held} onReviewed={onReviewed} />);
    await screen.findByText('Confirm each item you verified');
    await user.selectOptions(screen.getByLabelText('Review action'), 'route');
    await user.selectOptions(screen.getByLabelText('Department'), 'procurement');
    expect(screen.getByRole('button', {name: 'Record department handoff'})).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Decision reason'), 'inventory_check');
    await user.click(screen.getByRole('button', {name: 'Record department handoff'}));
    expect(await screen.findByText('Assigned to Procurement')).toBeVisible();
    expect(handoff).toHaveBeenCalledWith('run_1', expect.objectContaining({ version_id: 'ver_1', content_hash: 'a'.repeat(64), department: 'procurement', reason_code: 'inventory_check', note: '' }));
    expect(review).not.toHaveBeenCalled();
    expect(onReviewed).toHaveBeenCalledWith(held);
  });
  it('clears the previous reason when the reviewer changes action', async () => {
    const api = client();
    const user = userEvent.setup(); render(<ReviewPanel api={api} run={held} onReviewed={vi.fn()} />);
    await screen.findByText('Confirm each item you verified');
    await user.selectOptions(screen.getByLabelText('Decision reason'), 'vendor_verified');
    await user.selectOptions(screen.getByLabelText('Review action'), 'reject');
    expect(screen.getByLabelText('Decision reason')).toHaveValue('');
    expect(screen.getByRole('button', {name: 'Reject invoice'})).toBeDisabled();
  });
});
