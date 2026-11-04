import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { InvoiceQueue, inQueue } from '../src/features/history/InvoiceQueue';
import type { HistoryItem } from '../src/types/api';

const items: HistoryItem[] = [
  { run_id: 'run_1', invoice_number: 'INV-81', vendor: 'Aster Supply', lifecycle: 'completed', outcome: 'needs_review', filename: 'aster.pdf', amount: '200.00', currency: 'USD', started_at: '2026-11-03T11:00:00Z' },
  { run_id: 'run_2', invoice_number: 'INV-82', vendor: 'Briar Tools', lifecycle: 'completed', outcome: 'paid', filename: 'briar.json', amount: '300.00', currency: 'USD', started_at: '2026-11-03T12:00:00Z' },
  { run_id: 'run_3', invoice_number: 'INV-83', vendor: 'Cedar Labs', lifecycle: 'running', filename: 'cedar.csv', started_at: '2026-11-03T13:00:00Z' },
];
function queue() { render(<InvoiceQueue items={items} filter="all" onFilter={vi.fn()} openRun={vi.fn()} newInvoice={vi.fn()} trySamples={vi.fn()} connected={true} cursor={null} loadMore={vi.fn()} loading={false} />); }
describe('invoice queue', () => {
  it('searches real records by invoice, vendor and filename', async () => {
    queue();
    await userEvent.type(screen.getByLabelText('Search invoices'), 'aster.pdf');
    expect(screen.getByRole('button', { name: 'INV-81' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'INV-82' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Clear search' }));
    expect(screen.getByRole('button', { name: 'INV-82' })).toBeInTheDocument();
  });
  it('sorts the queue by received time without changing server values', async () => {
    queue();
    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('INV-83');
    await userEvent.selectOptions(screen.getByLabelText('Sort invoices'), 'oldest');
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('INV-81');
  });
  it('distinguishes review holds, in-flight work and blocked outcomes', () => {
    expect(items.filter((item) => inQueue(item, 'attention')).map((item) => item.run_id)).toEqual(['run_1']);
    expect(items.filter((item) => inQueue(item, 'processing')).map((item) => item.run_id)).toEqual(['run_3']);
    expect(inQueue({ ...items[0], outcome: 'rejected' }, 'blocked')).toBe(true);
    expect(inQueue({ ...items[0], lifecycle: 'failed', outcome: null }, 'blocked')).toBe(true);
  });
  it('does not claim a paid receipt from history alone', () => {
    queue();
    expect(screen.queryByText('Paid')).not.toBeInTheDocument();
    expect(screen.getByText('Payment reported', { selector: '.status' })).toBeInTheDocument();
  });
});
