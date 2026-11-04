import type {
  BatchResult, ConfigInfo, DocumentInfo, EventsPage, HistoryPage, Mode, RunAccepted, RunResult,
  ReviewOptions, SampleInfo, SessionInfo,
} from '../types/api';

export class ApiError extends Error {
  constructor(message: string, public status: number, public code = 'request_failed') {
    super(message);
    this.name = 'ApiError';
  }
}

function pageCount(response: Response) {
  const count = Number(response.headers.get('x-pdf-pages') ?? '1');
  if (!Number.isSafeInteger(count) || count < 1 || count > 1000) {
    throw new ApiError('The original PDF page count is invalid.', response.status, 'invalid_response');
  }
  return count;
}

export class ApiClient {
  private csrf: string | null = null;
  constructor(private readonly base = '/api/v1') {}

  setCsrf(token?: string | null) { this.csrf = token ?? null; }

  private async response(path: string, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers);
    if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    if (this.csrf && init.method && !['GET', 'HEAD'].includes(init.method)) headers.set('X-CSRF-Token', this.csrf);
    let response: Response;
    try {
      response = await fetch(`${this.base}${path}`, { ...init, headers, credentials: 'same-origin' });
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') throw error;
      throw new ApiError('Could not reach the server. Check the connection and try again.', 0, 'network_error');
    }
    if (!response.ok) {
      const contentType = response.headers.get('content-type') ?? '';
      let message = `Request failed (${response.status}). Please try again.`;
      let code = 'request_failed';
      if (contentType.includes('json')) {
        try {
          const body = await response.json() as { error?: { message?: string; code?: string }; detail?: unknown };
          if (typeof body.error?.message === 'string') message = body.error.message;
          else if (typeof body.detail === 'string') message = body.detail;
          if (typeof body.error?.code === 'string') code = body.error.code;
        } catch { /* Error pages may be empty or truncated. */ }
      }
      throw new ApiError(message, response.status, code);
    }
    return response;
  }

  private async json<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await this.response(path, init);
    if (response.status === 204) return undefined as T;
    const text = await response.text();
    if (!text.trim()) throw new ApiError('The server returned an empty response.', response.status, 'invalid_response');
    try { return JSON.parse(text) as T; }
    catch { throw new ApiError('The server returned an unreadable response.', response.status, 'invalid_response'); }
  }

  async createSession(passcode = '') {
    const result = await this.json<SessionInfo>('/session', { method: 'POST', headers: passcode ? { 'X-Demo-Code': passcode } : {} });
    this.setCsrf(result.csrf_token);
    return result;
  }
  async session() { return this.json<SessionInfo>('/session'); }
  config() { return this.json<ConfigInfo>('/config'); }
  samples() { return this.json<SampleInfo[]>('/samples'); }
  history(cursor?: string) { return this.json<HistoryPage>(`/history${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`); }
  async upload(file: File) {
    const form = new FormData(); form.append('file', file);
    return this.json<DocumentInfo>('/documents', { method: 'POST', body: form });
  }
  run(source: { document_id: string } | { sample_id: string }, mode: Mode, request_token: string) {
    return this.json<RunAccepted>('/runs', { method: 'POST', body: JSON.stringify({ ...source, mode, request_token }) });
  }
  batch(mode: Mode, request_token: string) {
    return this.json<{ batch_id: string; lifecycle: Lifecycle }>('/batches', {
      method: 'POST', body: JSON.stringify({ sample_ids: null, mode, request_token }),
    });
  }
  getBatch(id: string, signal?: AbortSignal) { return this.json<BatchResult>(`/batches/${encodeURIComponent(id)}`, { signal }); }
  getRun(id: string, signal?: AbortSignal) { return this.json<RunResult>(`/runs/${encodeURIComponent(id)}`, { signal }); }
  events(id: string, after: number, signal?: AbortSignal) {
    return this.json<EventsPage>(`/runs/${encodeURIComponent(id)}/events?after_seq=${after}`, { signal });
  }
  reviewOptions(id: string, signal?: AbortSignal) {
    return this.json<ReviewOptions>(`/runs/${encodeURIComponent(id)}/review-options`, { signal });
  }
  review(id: string, body: { version_id: string; content_hash: string; decision: 'approve' | 'reject'; comment: string; resolved_codes: string[]; idempotency_token: string }) {
    return this.json<{ run: RunResult; accepted: boolean; message: string }>(`/runs/${encodeURIComponent(id)}/review`, { method: 'POST', body: JSON.stringify(body) });
  }
  async source(id: string, signal?: AbortSignal) {
    const response = await this.response(`/runs/${encodeURIComponent(id)}/source`, { signal });
    const type = response.headers.get('content-type') ?? '';
    if (type.includes('application/json')) {
      const json = await response.json() as { text?: string; source_text?: string };
      return { text: json.text ?? json.source_text ?? 'Source text is not available.', pdf: null, pages: 0 };
    }
    if (type.includes('application/pdf')) return { text: null, pdf: await response.blob(), pages: pageCount(response) };
    if (!type.startsWith('text/')) return { text: 'Source text is not available for this file.', pdf: null, pages: 0 };
    const text = await response.text();
    let pdf: Blob | null = null;
    let pages = 0;
    if (response.headers.get('x-original-available') === 'pdf') {
      const original = await this.response(`/runs/${encodeURIComponent(id)}/original`, { signal });
      if (!original.headers.get('content-type')?.startsWith('application/pdf')) {
        throw new ApiError('The original document response was not a PDF.', original.status, 'invalid_response');
      }
      pdf = await original.blob();
      pages = pageCount(original);
    }
    return { text, pdf, pages };
  }
  previewUrl(id: string, page: number) { return `${this.base}/runs/${encodeURIComponent(id)}/preview?page=${page}`; }
  async export(id: string) {
    const response = await this.response(`/runs/${encodeURIComponent(id)}/export`);
    return response.blob();
  }
}
type Lifecycle = import('../types/api').Lifecycle;
