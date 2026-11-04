import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { RunDetails } from '../src/features/runs/RunDetails';
import { formatAmount } from '../src/components/Status';
import type { RunResult } from '../src/types/api';

const base: RunResult = {
  run_id: 'run_1', workspace_id: 'ws_1', lifecycle: 'completed', outcome: 'needs_review',
  filename: 'invoice.txt', findings: [{ code: 'UNSUPPORTED_CURRENCY', severity: 'REVIEW', message: 'Currency needs review.' }],
  approval: [], requires_scrutiny: false, mode: 'mock',
};

describe('run details', () => {
  it('shows review findings and no fake payment control', () => {
    render(<RunDetails run={base} events={[]} source="original source" sourceError={null} onExport={vi.fn()} exporting={false} />);
    expect(screen.getByText('Needs review')).toBeInTheDocument();
    expect(screen.getByText('Currency needs review.')).toBeInTheDocument();
    expect(screen.queryByText('Mock payment recorded')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument();
  });
  it('requires a stored receipt before displaying paid', () => {
    render(<RunDetails run={{ ...base, outcome: 'paid' }} events={[]} source={null} sourceError={null} onExport={vi.fn()} exporting={false} />);
    expect(screen.getAllByText('Receipt unavailable').length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText('Paid')).not.toBeInTheDocument();
  });
  it('shows a mock receipt only when it is present', () => {
    const receipt = { payment_id: 'p1', transaction_id: 'mock_123', amount: '5000.00', currency: 'USD', vendor: 'Vendor', created_at: '2026-11-03T12:00:00Z', simulated: true as const };
    render(<RunDetails run={{ ...base, outcome: 'paid', receipt }} events={[]} source={null} sourceError={null} onExport={vi.fn()} exporting={false} />);
    expect(screen.getByText('Paid (simulated)')).toBeInTheDocument();
    expect(screen.getByText('Mock payment recorded')).toBeInTheDocument();
    expect(screen.getAllByText(/mock_123/).length).toBeGreaterThanOrEqual(1);
  });
  it('prints a report with recorded values without interpreting source as markup', async () => {
    const print = vi.spyOn(window, 'print').mockImplementation(() => {});
    render(<RunDetails run={base} events={[]} source="text" sourceError={null} onExport={vi.fn()} exporting={false} />);
    await userEvent.click(screen.getByRole('button', {name:'Print decision report'}));
    expect(print).toHaveBeenCalledOnce();
    expect(document.querySelector('.decision-report')).toHaveTextContent('Currency needs review.');
    expect(document.querySelector('.decision-report')).toHaveTextContent('No simulated receipt recorded.');
    print.mockRestore();
  });
  it('shows real event summaries with unknown details available', async () => {
    const events = [{seq:1, stage:'intake', type:'parsed_structured', timestamp:'2026-11-03T12:00:00Z', payload:{line_items:2}}, {seq:2, stage:'error', type:'new_event', timestamp:'2026-11-03T12:00:01Z', payload:{code:'failure'}}];
    render(<RunDetails run={base} events={events} source="text" sourceError={null} onExport={vi.fn()} exporting={false} />);
    await userEvent.click(screen.getByRole('tab', {name:/Activity/}));
    expect(screen.getByText('Structured document parsed')).toBeInTheDocument();
    expect(screen.getByText('Other recorded activity')).toBeInTheDocument();
    expect(screen.queryByText('Simulated payment recorded')).not.toBeInTheDocument();
    expect(screen.getByText('new_event')).toBeInTheDocument();
  });
  it('offers a copyable correction draft without sending a message', () => {
    render(<RunDetails run={{...base, outcome:'rejected'}} events={[]} source="original" sourceError={null} onExport={vi.fn()} exporting={false} />);
    expect((screen.getByLabelText('Correction request draft') as HTMLTextAreaElement).value).toContain('Currency needs review.');
    expect(screen.getByText(/No message is sent/)).toBeInTheDocument();
  });
  it('renders untrusted source as text', async () => {
    render(<RunDetails run={base} events={[]} source={'<img src=x onerror=alert(1)>'} sourceError={null} onExport={vi.fn()} exporting={false} />);
    await userEvent.click(screen.getByRole('tab', { name: 'Source document' }));
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(document.querySelector('img')).toBeNull();
  });
  it('formats decimal strings without losing precision', () => {
    expect(formatAmount('12345678901234567890.12', 'USD')).toBe('$12,345,678,901,234,567,890.12');
  });
  it('opens evidence in source text without treating the quotation as HTML', async () => {
    const finding = { code: 'TOTAL_MISMATCH', severity: 'ERROR' as const, message: 'Amounts differ.', evidence: 'Total: 10.00' };
    render(<RunDetails run={{ ...base, findings: [finding] }} events={[]} source={'Invoice\nTotal: 10.00\nEnd'} sourceError={null} onExport={vi.fn()} exporting={false} />);
    await userEvent.click(screen.getByRole('button', { name: /find in source/i }));
    expect(screen.getByText('Total: 10.00').tagName).toBe('MARK');
  });
  it('lets an operator switch between the original PDF and extracted text', async () => {
    render(<RunDetails run={{ ...base, filename: 'invoice.pdf' }} events={[]} source="Extracted PDF text" pdfUrl="blob:test-pdf" sourceError={null} onExport={vi.fn()} exporting={false} />);
    expect(screen.getByAltText('Original invoice PDF page 1')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Extracted text' }));
    expect(screen.getByText('Extracted PDF text')).toBeInTheDocument();
    expect(screen.queryByAltText('Original invoice PDF page 1')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Original PDF' }));
    expect(screen.getByAltText('Original invoice PDF page 1')).toBeInTheDocument();
  });
  it('locates PDF evidence in the retained extracted text', async () => {
    const finding = { code: 'TOTAL_MISMATCH', severity: 'ERROR' as const, message: 'Amounts differ.', evidence: 'Total: 10.00' };
    render(<RunDetails run={{ ...base, filename: 'invoice.pdf', findings: [finding] }} events={[]} source={'Invoice\nTotal: 10.00\nEnd'} pdfUrl="blob:test-pdf" sourceError={null} onExport={vi.fn()} exporting={false} />);
    await userEvent.click(screen.getByRole('button', { name: /find in source/i }));
    expect(screen.getByText('Total: 10.00').tagName).toBe('MARK');
    expect(screen.queryByAltText('Original invoice PDF page 1')).not.toBeInTheDocument();
  });
});
