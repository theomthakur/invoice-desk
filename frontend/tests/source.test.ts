import { afterEach, expect, it, vi } from 'vitest';
import { ApiClient } from '../src/api/client';

afterEach(() => vi.unstubAllGlobals());

it('retains extracted text alongside a server-advertised original PDF', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response('Extracted invoice text', { headers: { 'content-type': 'text/plain', 'x-original-available': 'pdf' } }))
    .mockResolvedValueOnce(new Response('%PDF-test-original', { headers: { 'content-type': 'application/pdf' } }));
  vi.stubGlobal('fetch', fetchMock);

  const source = await new ApiClient().source('run_original');

  expect(source.text).toBe('Extracted invoice text');
  expect(source.pdf?.type).toBe('application/pdf');
  expect(await source.pdf?.text()).toBe('%PDF-test-original');
  expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/runs/run_original/original');
});

it('keeps historical text-only sources available without fetching a missing original', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response('Old extracted text', { headers: { 'content-type': 'text/plain' } }));
  vi.stubGlobal('fetch', fetchMock);

  await expect(new ApiClient().source('old_run')).resolves.toEqual({ text: 'Old extracted text', pdf: null, pages: 0 });
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it('rejects an original response with an unexpected content type', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(new Response('Text', { headers: { 'content-type': 'text/plain', 'x-original-available': 'pdf' } }))
    .mockResolvedValueOnce(new Response('<html>failure</html>', { headers: { 'content-type': 'text/html' } })));

  await expect(new ApiClient().source('bad_original')).rejects.toMatchObject({ code: 'invalid_response' });
});
