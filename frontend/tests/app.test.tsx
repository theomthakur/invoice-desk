import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../src/App';

const calls: { path: string; method: string; body?: string }[] = [];
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } });
beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input); const method = init?.method ?? 'GET';
    calls.push({ path, method, body: typeof init?.body === 'string' ? init.body : undefined });
    if (path.endsWith('/session')) return json({ workspace_id: 'ws_1', persistence: 'local', modes_enabled: ['mock'], csrf_token: 'csrf' });
    if (path.endsWith('/config')) return json({ schema_version: '1', policy_version: '1', limits: { max_file_bytes: 2000000, max_pdf_pages: 30, max_line_items: 50 }, supported_extensions: ['txt', 'pdf'], enabled_modes: ['mock'], approval_threshold: '10000.00', approval_currency: 'USD', payment_currencies: ['USD'] });
    if (path.endsWith('/samples')) return json([{ sample_id: 'sample_1', name: 'Invoice one', format: 'txt' }]);
    if (path.endsWith('/history')) return json({ items: [], next_cursor: null });
    if (path.endsWith('/documents')) return json({ document_id: 'doc_1', filename: 'new.txt', format: 'txt', size_bytes: 4 });
    if (path.endsWith('/runs') && method === 'POST') return json({ run_id: 'run_1', lifecycle: 'queued', already_existed: false }, 202);
    if (path.includes('/events?')) return json({ run_id: 'run_1', lifecycle: 'completed', events: [{ seq: 1, timestamp: '2026-11-03T12:00:00Z', stage: 'validation', type: 'checked', payload: {} }], next_after_seq: 1 });
    if (path.endsWith('/source')) return new Response('Source text', { headers: { 'content-type': 'text/plain' } });
    if (path.endsWith('/runs/run_1')) return json({ run_id: 'run_1', workspace_id: 'ws_1', lifecycle: 'completed', outcome: 'rejected', filename: 'invoice.txt', findings: [{ code: 'MISSING_FIELD', severity: 'ERROR', message: 'Missing total' }], approval: [], requires_scrutiny: false, mode: 'mock' });
    throw new Error(`Unexpected route: ${method} ${path}`);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('invoice workspace', () => {
  it('does not start a run until an explicit click, then renders the real server outcome', async () => {
    const user = userEvent.setup(); render(<App />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start a new invoice' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: /try a sample/i }));
    await screen.findByRole('option', { name: 'Invoice one' });
    expect(calls.filter((c) => c.path.endsWith('/runs') && c.method === 'POST')).toHaveLength(0);
    await user.selectOptions(screen.getByLabelText('Use a sample invoice'), 'sample_1');
    expect(calls.filter((c) => c.path.endsWith('/runs') && c.method === 'POST')).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: /process invoice/i }));
    expect(await screen.findByText('Missing total')).toBeInTheDocument();
    expect(screen.getByText('Rejected')).toBeInTheDocument();
    expect(calls.filter((c) => c.path.endsWith('/runs') && c.method === 'POST')).toHaveLength(1);
    expect(screen.queryByText('Simulated payment recorded')).not.toBeInTheDocument();
  });
  it('uploads a selected file only when processing starts', async () => {
    const user = userEvent.setup(); render(<App />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start a new invoice' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: /try a sample/i }));
    await screen.findByRole('option', { name: 'Invoice one' });
    await user.click(screen.getByRole('button', { name: 'Upload file' }));
    await user.upload(screen.getByLabelText('Or upload a file'), new File(['test'], 'new.txt', { type: 'text/plain' }));
    expect(calls.some((c) => c.path.endsWith('/documents'))).toBe(false);
    await user.click(screen.getByRole('button', { name: /process invoice/i }));
    await screen.findByText('Missing total');
    expect(calls.filter((c) => c.path.endsWith('/documents'))).toHaveLength(1);
    const submitted = calls.find((c) => c.path.endsWith('/runs') && c.method === 'POST');
    expect(JSON.parse(submitted!.body!)).toMatchObject({ document_id: 'doc_1', mode: 'mock' });
  });
  it('shows empty history without starting work', async () => {
    const user = userEvent.setup(); render(<App />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start a new invoice' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: /try a sample/i }));
    await screen.findByRole('option', { name: 'Invoice one' });
    await user.click(screen.getByRole('button', { name: 'Close new invoice' }));
    await user.click(screen.getByRole('button', { name: 'Invoices' }));
    expect(screen.getByText('A clear view of every invoice')).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith('/runs') && c.method === 'POST')).toHaveLength(0));
  });
  it('lets the user return from a result to a fresh invoice', async () => {
    const user = userEvent.setup(); render(<App />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start a new invoice' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: /try a sample/i }));
    await screen.findByRole('option', { name: 'Invoice one' });
    await user.selectOptions(screen.getByLabelText('Use a sample invoice'), 'sample_1');
    await user.click(screen.getByRole('button', { name: /process invoice/i }));
    await screen.findByText('Missing total');
    await user.click(screen.getByRole('button', { name: 'Back to invoices' }));
    await user.click(screen.getByRole('button', { name: 'Start a new invoice' }));
    expect(screen.getByRole('heading', { name: 'New invoice' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Process invoice' })).toBeDisabled();
  });
  it('prevents duplicate clicks while the same request is in flight', async () => {
    let release: ((response: Response) => void) | undefined;
    const original = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((input, init) => {
      if (String(input).endsWith('/runs') && init?.method === 'POST') {
        calls.push({ path: String(input), method: 'POST', body: String(init.body) });
        return new Promise<Response>((resolve) => { release = resolve; });
      }
      return original(input, init);
    });
    const user = userEvent.setup(); render(<App />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start a new invoice' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: /try a sample/i }));
    await screen.findByRole('option', { name: 'Invoice one' });
    await user.selectOptions(screen.getByLabelText('Use a sample invoice'), 'sample_1');
    await user.dblClick(screen.getByRole('button', { name: 'Process invoice' }));
    expect(calls.filter((c) => c.path.endsWith('/runs') && c.method === 'POST')).toHaveLength(1);
    release?.(json({ run_id: 'run_1', lifecycle: 'queued', already_existed: false }, 202));
    expect(await screen.findByText('Missing total')).toBeInTheDocument();
  });
});
