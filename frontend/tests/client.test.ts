import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiError } from '../src/api/client';

afterEach(() => vi.unstubAllGlobals());

describe('API client', () => {
  it.each([401, 403, 404, 409, 422, 429, 503])('reports a safe error for HTTP %i without assuming JSON', async (status) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>bad gateway</html>', { status, headers: { 'content-type': 'text/html' } })));
    await expect(new ApiClient().getRun('run_1')).rejects.toMatchObject({ status, code: 'request_failed' });
  });
  it('preserves the server error when it is JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'stale', message: 'Invoice changed.' } }), { status: 409, headers: { 'content-type': 'application/json' } })));
    await expect(new ApiClient().getRun('run_1')).rejects.toMatchObject({ status: 409, code: 'stale', message: 'Invoice changed.' });
  });
  it('handles empty and malformed success bodies explicitly', async () => {
    const client = new ApiClient();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(null, { status: 204 })).mockResolvedValueOnce(new Response('', { status: 200 })).mockResolvedValueOnce(new Response('<html>', { status: 200 })));
    await expect(client.config()).resolves.toBeUndefined();
    await expect(client.config()).rejects.toMatchObject({ code: 'invalid_response' });
    await expect(client.config()).rejects.toMatchObject({ code: 'invalid_response' });
  });
  it('sends CSRF and the exact idempotency token without sending a workspace ID', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ run_id: 'run_1', lifecycle: 'queued', already_existed: false }), { status: 202 }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ApiClient(); client.setCsrf('test-csrf');
    await client.run({ sample_id: 'sample_1' }, 'mock', 'req_12345678');
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('test-csrf');
    expect(JSON.parse(init.body as string)).toEqual({ sample_id: 'sample_1', mode: 'mock', request_token: 'req_12345678' });
    expect(init.credentials).toBe('same-origin');
  });
  it('uses the backend access-code header and keeps the code out of a JSON body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ workspace_id: 'w', persistence: 'ephemeral', modes_enabled: ['mock'], csrf_token: 'csrf' })));
    vi.stubGlobal('fetch', fetchMock);
    await new ApiClient().createSession('test-only-code');
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get('X-Demo-Code')).toBe('test-only-code');
    expect(init.body).toBeUndefined();
  });
  it('propagates cancellation rather than reporting a network error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('Canceled', 'AbortError')));
    await expect(new ApiClient().getRun('run_1')).rejects.toHaveProperty('name', 'AbortError');
  });
  it('wraps network failures without leaking the underlying error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('sensitive transport details')));
    const result = new ApiClient().getRun('run_1');
    await expect(result).rejects.toBeInstanceOf(ApiError);
    await expect(result).rejects.not.toHaveProperty('message', 'sensitive transport details');
  });
});
