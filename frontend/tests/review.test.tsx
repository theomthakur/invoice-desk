import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiError } from '../src/api/client';
import { ReviewPanel } from '../src/features/review/ReviewPanel';
import type { RunResult } from '../src/types/api';

const held: RunResult = {
  run_id: 'run_1', workspace_id: 'ws_1', lifecycle: 'completed', outcome: 'needs_review',
  version_id: 'ver_1', content_hash: 'a'.repeat(64), findings: [{ code: 'VENDOR_IDENTITY_CHANGE', severity: 'REVIEW', message: 'Verify the former name.' }],
  approval: [], requires_scrutiny: false,
};
const options = { version_id: 'ver_1', content_hash: 'a'.repeat(64), resolvable_codes: ['VENDOR_IDENTITY_CHANGE'], blocked_codes: [], can_approve: true, can_reject: true };

describe('simulated human review', () => {
  it('requires an explicit confirmation for each finding and records a server decision', async () => {
    const api = new ApiClient(); const onReviewed = vi.fn();
    vi.spyOn(api, 'reviewOptions').mockResolvedValue(options);
    const review = vi.spyOn(api, 'review').mockResolvedValue({ accepted: true, message: 'Recorded', run: { ...held, outcome: 'paid' } });
    const user = userEvent.setup(); render(<ReviewPanel api={api} run={held} onReviewed={onReviewed} />);
    await screen.findByText('Confirm each item you verified');
    await user.type(screen.getByLabelText(/decision note/i), 'Checked the vendor record.');
    expect(screen.getByRole('button', { name: /approve & simulate payment/i })).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /vendor identity change/i }));
    await user.click(screen.getByRole('checkbox', { name: /checked the source/i }));
    await user.click(screen.getByRole('button', { name: /approve & simulate payment/i }));
    expect(review).toHaveBeenCalledWith('run_1', expect.objectContaining({ version_id: 'ver_1', content_hash: 'a'.repeat(64), decision: 'approve', resolved_codes: ['VENDOR_IDENTITY_CHANGE'] }));
    expect(onReviewed).toHaveBeenCalledWith(expect.objectContaining({ outcome: 'paid' }));
  });
  it('cannot approve a currency hold but allows a documented rejection', async () => {
    const api = new ApiClient();
    vi.spyOn(api, 'reviewOptions').mockResolvedValue({ ...options, resolvable_codes: [], blocked_codes: ['UNSUPPORTED_CURRENCY'], can_approve: false });
    const review = vi.spyOn(api, 'review').mockResolvedValue({ accepted: true, message: 'Rejected', run: { ...held, outcome: 'rejected' } });
    const user = userEvent.setup(); render(<ReviewPanel api={api} run={held} onReviewed={vi.fn()} />);
    await screen.findByText('Cannot approve yet');
    await user.type(screen.getByLabelText(/decision note/i), 'Cannot verify the currency.');
    expect(screen.getByRole('button', { name: /approve & simulate payment/i })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: /reject invoice/i }));
    expect(review).toHaveBeenCalledWith('run_1', expect.objectContaining({ decision: 'reject', resolved_codes: [] }));
  });
  it('shows a stale review error and leaves the invoice on hold', async () => {
    const api = new ApiClient(); const onReviewed = vi.fn();
    vi.spyOn(api, 'reviewOptions').mockResolvedValue(options);
    vi.spyOn(api, 'review').mockRejectedValue(new ApiError('Invoice changed.', 409, 'stale_decision'));
    const user = userEvent.setup(); render(<ReviewPanel api={api} run={held} onReviewed={onReviewed} />);
    await screen.findByText('Confirm each item you verified');
    await user.type(screen.getByLabelText(/decision note/i), 'Checked record.');
    await user.click(screen.getByRole('button', { name: /reject invoice/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Invoice changed.');
    expect(onReviewed).not.toHaveBeenCalled();
  });
});
