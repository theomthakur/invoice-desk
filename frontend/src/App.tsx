import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, CircleAlert, Clock3, FileStack, FileText, GitBranch, Inbox, LoaderCircle, LockKeyhole, PanelLeftClose, PanelLeftOpen, Plus, Presentation as PresentationIcon, RefreshCw, Settings2, ShieldCheck, X } from 'lucide-react';
import { ApiClient, ApiError } from './api/client';
import { Status, formatAmount } from './components/Status';
import { RunDetails } from './features/runs/RunDetails';
import { InvoiceQueue, type QueueFilter } from './features/history/InvoiceQueue';
import { IntakePanel } from './features/intake/IntakePanel';
import { SystemDesign } from './features/resources/SystemDesign';
import { ProjectPresentation } from './features/resources/ProjectPresentation';
import type { BatchResult, ConfigInfo, HistoryItem, Mode, RunEvent, RunResult, SampleInfo, SessionInfo } from './types/api';
import './styles/app.css';

const api = new ApiClient();
const isActive = (status: string) => status === 'running' || status === 'queued';
const messageOf = (error: unknown) => error instanceof Error ? error.message : 'Something went wrong. Please try again.';
const token = () => `req_${crypto.randomUUID()}`;

function Brand() {
  return <div className="brand"><img src="/brand-mark.svg" width="42" height="42" alt="" /><span className="brand-name">Invoice<span>Desk</span><small>LESS ADMIN. MORE CLARITY.</small></span></div>;
}

function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try { return localStorage.getItem('invoice_desk_sidebar') === 'collapsed'; }
    catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem('invoice_desk_sidebar', sidebarCollapsed ? 'collapsed' : 'expanded'); }
    catch { /* The toggle still works when browser storage is unavailable. */ }
  }, [sidebarCollapsed]);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [config, setConfig] = useState<ConfigInfo | null>(null);
  const [samples, setSamples] = useState<SampleInfo[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [screen, setScreen] = useState<'workspace' | 'history' | 'rules' | 'architecture' | 'presentation'>('workspace');
  const [initializing, setInitializing] = useState(true);
  const [accessRequired, setAccessRequired] = useState(false);
  const [passcode, setPasscode] = useState('');
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [selectedSample, setSelectedSample] = useState('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [mode, setMode] = useState<Mode>('mock');
  const [run, setRun] = useState<RunResult | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const currentRun = useRef<string | null>(null);
  currentRun.current = runId;
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [source, setSource] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [pdfPages, setPdfPages] = useState(1);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batch, setBatch] = useState<BatchResult | null>(null);
  const [pending, setPending] = useState(false);
  const [pendingText, setPendingText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [intakeOpen, setIntakeOpen] = useState(false);
  const [intakeTab, setIntakeTab] = useState<'upload' | 'sample'>('upload');
  const [queueFilter, setQueueFilter] = useState<QueueFilter>('all');
  const [historyLoading, setHistoryLoading] = useState(false);
  const requestRef = useRef<{ kind: 'run' | 'batch'; token: string } | null>(null);
  const uploadRef = useRef<string | null>(null);
  const pendingRef = useRef(false);

  const refreshHistory = useCallback(async (cursor?: string) => {
    const result = await api.history(cursor);
    setHistory((previous) => cursor ? [...previous, ...result.items] : result.items);
    setHistoryCursor(result.next_cursor ?? null);
  }, []);

  const loadWorkspace = useCallback(async (info: SessionInfo) => {
    api.setCsrf(info.csrf_token);
    const [cfg, sampleList] = await Promise.all([api.config(), api.samples()]);
    setSession(info); setConfig(cfg); setSamples(sampleList);
    if (!cfg.enabled_modes.includes('mock') && cfg.enabled_modes.includes('live')) setMode('live');
    setConnectionError(null); setAccessRequired(false);
    try { await refreshHistory(); } catch (err) { setError(`History could not be loaded: ${messageOf(err)}`); }
  }, [refreshHistory]);

  const connect = useCallback(async (providedPasscode = '') => {
    setInitializing(true); setConnectionError(null);
    try {
      // POST creates or resumes the same server-owned session and returns the CSRF token.
      await loadWorkspace(await api.createSession(providedPasscode));
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        setAccessRequired(true);
        if (providedPasscode) setConnectionError('The access code was not accepted. Please try again.');
      }
      else setConnectionError(messageOf(err));
    } finally { setInitializing(false); }
  }, [loadWorkspace]);

  useEffect(() => { void connect(); }, [connect]);
  useEffect(() => () => { if (pdfUrl) URL.revokeObjectURL(pdfUrl); }, [pdfUrl]);
  useEffect(() => {
    if (!session || runId || batchId || !history.some((item) => isActive(item.lifecycle))) return;
    const timer = setTimeout(() => { void refreshHistory().catch(() => {}); }, 1500);
    return () => clearTimeout(timer);
  }, [session, runId, batchId, history, refreshHistory]);

  useEffect(() => {
    if (!runId) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let after = 0;
    let failures = 0;
    setRun(null); setEvents([]); setSource(null); setPdfUrl(null); setSourceError(null); setPollError(null);
    async function poll() {
      try {
        const [fresh, page] = await Promise.all([api.getRun(runId!, abort.signal), api.events(runId!, after, abort.signal)]);
        if (abort.signal.aborted) return;
        setRun(fresh);
        setEvents((previous) => {
          const merged = new Map(previous.map((event) => [event.seq, event]));
          page.events.forEach((event) => merged.set(event.seq, event));
          return [...merged.values()].sort((a, b) => a.seq - b.seq);
        });
        const previousAfter = after;
        after = Math.max(after, page.next_after_seq, ...page.events.map((event) => event.seq));
        failures = 0; setPollError(null);
        if (isActive(fresh.lifecycle)) timer = setTimeout(poll, 1000);
        else {
          // Refresh history after terminal state, and drain any pages the server capped.
          if (page.events.length > 0 && after > previousAfter) { timer = setTimeout(poll, 0); return; }
          void refreshHistory().catch(() => {});
          void api.source(runId!, abort.signal).then((content) => { if (!abort.signal.aborted) { setSource(content.text); setPdfPages(content.pages || 1); if (content.pdf) setPdfUrl(URL.createObjectURL(content.pdf)); } }).catch((err: unknown) => { if (!abort.signal.aborted) setSourceError(messageOf(err)); });
        }
      } catch (err) {
        if (abort.signal.aborted) return;
        setPollError(`Connection interrupted. Retrying without losing the saved run. ${messageOf(err)}`);
        failures += 1; timer = setTimeout(poll, Math.min(15000, 1000 * 2 ** Math.min(failures, 4)));
      }
    }
    void poll();
    return () => { abort.abort(); if (timer) clearTimeout(timer); };
  }, [runId, refreshHistory]);

  useEffect(() => {
    if (!batchId) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    async function poll() {
      try {
        const result = await api.getBatch(batchId!, abort.signal);
        if (abort.signal.aborted) return;
        setBatch(result); setPollError(null); failures = 0;
        if (isActive(result.lifecycle)) timer = setTimeout(poll, 1000);
        else void refreshHistory().catch(() => {});
      } catch (err) {
        if (abort.signal.aborted) return;
        setPollError(`Batch status is temporarily unavailable. ${messageOf(err)}`);
        timer = setTimeout(poll, Math.min(15000, 1000 * 2 ** Math.min(++failures, 4)));
      }
    }
    void poll();
    return () => { abort.abort(); if (timer) clearTimeout(timer); };
  }, [batchId, refreshHistory]);

  function chooseFile(file: File | null) {
    if (!file) return;
    const extension = file.name.split('.').pop()?.toLowerCase();
    const supported = config?.supported_extensions.map((s) => s.replace(/^\./, '').toLowerCase()) ?? [];
    if (!extension || !supported.includes(extension)) { setError('Please choose a supported invoice file.'); return; }
    if (config && file.size > config.limits.max_file_bytes) { setError('This file is larger than the allowed upload limit.'); return; }
    setSelectedFile(file); setSelectedSample(''); setError(null); requestRef.current = null; uploadRef.current = null;
  }
  function resetSelection() { setSelectedFile(null); setSelectedSample(''); requestRef.current = null; uploadRef.current = null; }
  function openRun(id: string) { setBatchId(null); setBatch(null); setRunId(id); setScreen('workspace'); setError(null); }
  function newInvoice() { resetSelection(); setIntakeTab('upload'); setIntakeOpen(true); setError(null); }

  async function startRun() {
    if (pendingRef.current || (!selectedFile && !selectedSample)) return;
    pendingRef.current = true; setPending(true); setError(null);
    try {
      if (requestRef.current?.kind !== 'run') requestRef.current = { kind: 'run', token: token() };
      let selected: { document_id: string } | { sample_id: string };
      if (selectedFile) { setPendingText('Uploading file…'); uploadRef.current ??= (await api.upload(selectedFile)).document_id; selected = { document_id: uploadRef.current }; }
      else selected = { sample_id: selectedSample };
      setPendingText('Starting processing…');
      const result = await api.run(selected, mode, requestRef.current.token);
      requestRef.current = null; setIntakeOpen(false); openRun(result.run_id);
      void refreshHistory().catch(() => {});
    } catch (err) { setError(messageOf(err)); }
    finally { pendingRef.current = false; setPending(false); setPendingText(''); }
  }
  async function startBatch() {
    if (pendingRef.current) return;
    pendingRef.current = true; setPending(true); setPendingText('Starting sample batch…'); setError(null);
    try {
      if (requestRef.current?.kind !== 'batch') requestRef.current = { kind: 'batch', token: token() };
      const result = await api.batch(mode, requestRef.current.token);
      requestRef.current = null; setIntakeOpen(false); setRunId(null); setRun(null); setBatch(null); setBatchId(result.batch_id); setScreen('workspace');
    } catch (err) { setError(messageOf(err)); }
    finally { pendingRef.current = false; setPending(false); setPendingText(''); }
  }
  async function exportRun() {
    if (!runId || exporting) return;
    setExporting(true);
    try {
      const blob = await api.export(runId); const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = `invoice-run-${runId}.json`; anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) { setError(messageOf(err)); } finally { setExporting(false); }
  }

  function reviewCompleted(result: RunResult) {
    void refreshHistory().catch(() => {});
    if (currentRun.current !== result.run_id) return;
    setRun(result);
    void api.events(result.run_id, 0).then((page) => {
      if (currentRun.current === result.run_id) setEvents(page.events);
    }).catch(() => {});
  }

  function showInbox(filter: QueueFilter = 'all') {
    setRunId(null); setRun(null); setBatchId(null); setBatch(null); setScreen('workspace'); setQueueFilter(filter); setPollError(null);
    if (session) void refreshHistory().catch(() => {});
  }
  async function reloadHistory(cursor?: string) {
    if (historyLoading) return;
    setHistoryLoading(true);
    try { await refreshHistory(cursor); } catch (err) { setError(messageOf(err)); }
    finally { setHistoryLoading(false); }
  }
  const attentionCount = history.filter((item) => item.outcome === 'needs_review').length;
  const currentTitle = screen === 'architecture' ? 'System design' : screen === 'presentation' ? 'Presentation' : screen === 'rules' ? 'Processing rules' : screen === 'history' ? 'Run history' : runId ? 'Invoice record' : batchId ? 'Batch results' : 'Invoices';
  function openResource(value: 'architecture' | 'presentation') {
    setRunId(null); setRun(null); setBatchId(null); setBatch(null); setScreen(value); setPollError(null);
  }

  return <div className={`app-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
    <a className="skip-link" href="#main-content">Skip to content</a>
    <aside className="sidebar" id="workspace-navigation"><Brand />
      <div className="company-switch"><div className="company-avatar">A<span /></div><div><strong>Acme Corp</strong><span>Accounts payable workspace</span></div></div>
      <div className="sidebar-label">WORKSPACE</div>
      <nav aria-label="Main navigation">
        <button className={`nav-item ${screen === 'workspace' && queueFilter !== 'attention' ? 'active' : ''}`} aria-label="Invoices" title="Invoices" onClick={() => showInbox()}><Inbox size={18} /><span>Invoices</span><span className="nav-count">{history.length}</span></button>
        <button className={`nav-item ${screen === 'workspace' && queueFilter === 'attention' ? 'active' : ''}`} aria-label="Needs review" title="Needs review" onClick={() => showInbox('attention')}><CircleAlert size={18} /><span>Needs review</span>{attentionCount > 0 && <span className="nav-count amber">{attentionCount}</span>}</button>
        <button className={`nav-item ${screen === 'history' ? 'active' : ''}`} aria-label="Run history" title="Run history" onClick={() => { setRunId(null); setBatchId(null); setScreen('history'); }}><Clock3 size={18} /><span>Run history</span></button>
        <button className={`nav-item ${screen === 'rules' ? 'active' : ''}`} aria-label="Processing rules" title="Processing rules" onClick={() => { setRunId(null); setBatchId(null); setScreen('rules'); }}><Settings2 size={18} /><span>Processing rules</span></button>
      </nav>
      <div className="sidebar-note"><FileStack size={22} strokeWidth={1.5} /><strong>A paper trail.<br />Without the paperwork.</strong><p>Documents, checks and decisions. All in one place.</p></div>
      <div className="sidebar-bottom"><div className="workspace-caption"><span className={`connection-dot ${session ? 'online' : ''}`} />{session ? 'Workspace connected' : 'Workspace offline'}</div><p>{session?.persistence === 'ephemeral' ? 'Temporary storage. Records may reset when the server restarts.' : session ? 'History is stored in this workspace.' : 'Connect to the server to load your records.'}</p><div className="sidebar-profile"><span className="avatar">AP</span><div><strong>Acme workspace</strong><small>Demo environment</small></div><LockKeyhole size={14} /></div></div>
    </aside>
    <main className="main-area" id="main-content"><header className="topbar"><div className="topbar-location"><button className="icon-button sidebar-toggle" aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'} title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'} aria-expanded={!sidebarCollapsed} aria-controls="workspace-navigation" onClick={() => setSidebarCollapsed((value) => !value)}>{sidebarCollapsed ? <PanelLeftOpen size={19} /> : <PanelLeftClose size={19} />}</button><div className="breadcrumb"><span>Workspace</span><span>/</span><strong>{currentTitle}</strong></div></div><div className="top-right"><nav className="resource-nav" aria-label="Project resources"><button aria-current={screen === 'architecture' ? 'page' : undefined} onClick={() => openResource('architecture')}><GitBranch size={15} />System design</button><button aria-current={screen === 'presentation' ? 'page' : undefined} onClick={() => openResource('presentation')}><PresentationIcon size={15} />Presentation</button></nav><span className="demo-badge"><span />Demo workspace</span><span className="top-divider" /><span className="avatar">AP</span></div></header>
      <div className="page-content">
        {initializing && <div className="connection-banner" role="status"><LoaderCircle size={16} className="spin" /> Connecting to your workspace…</div>}
        {connectionError && <div className="connection-banner error-banner" role="alert"><CircleAlert size={17} /><span>{connectionError}</span><button onClick={() => void connect()}>Retry connection</button></div>}
        {accessRequired && <form className="access-panel" onSubmit={(event) => { event.preventDefault(); void connect(passcode); }}><LockKeyhole size={19} /><label htmlFor="passcode">Workspace access code</label><input id="passcode" type="password" value={passcode} onChange={(event) => setPasscode(event.target.value)} required /><button className="button button-primary" disabled={initializing}>Connect</button></form>}
        {error && !intakeOpen && <div className="inline-error global-error" role="alert"><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError(null)}><X size={17} /></button></div>}
        {pollError && <div className="inline-warning" role="status">{pollError}</div>}
        {screen === 'architecture' ? <SystemDesign config={config} />
        : screen === 'presentation' ? <ProjectPresentation openInvoices={() => showInbox()} />
        : runId ? <><button className="back-link" onClick={() => showInbox()}><ArrowLeft size={15} />Back to invoices</button>{!run ? <div className="loading-card"><LoaderCircle size={20} className="spin" />Loading invoice record…</div> : <RunDetails key={run.run_id} run={run} events={events} source={source} pdfUrl={pdfUrl} pdfPages={pdfPages} sourceError={sourceError} onExport={() => void exportRun()} exporting={exporting} api={api} onReviewed={reviewCompleted} />}</>
        : batchId ? <section><button className="back-link" onClick={() => showInbox()}><ArrowLeft size={15} />Back to invoices</button><div className="page-heading"><div><h1>Batch results</h1><p>Duplicate files and revisions are grouped before payment.</p></div><button className="button button-primary" onClick={newInvoice}><Plus size={16} />New invoice</button></div>
          {!batch ? <div className="loading-card"><LoaderCircle size={20} className="spin" />Loading batch…</div> : <div className="content-card"><div className="card-heading"><div><h3>Sample batch</h3><p>{batch.files.length} files in this batch</p></div><Status status={batch.lifecycle} /></div><div className="batch-summary">{Object.entries(batch.counts).map(([label, count]) => <div key={label}><span>{label.replaceAll('_', ' ')}</span><strong>{count}</strong></div>)}{Object.entries(batch.paid_by_currency).map(([currency, amount]) => <div key={currency}><span>Mock paid · {currency}</span><strong>{formatAmount(amount, currency)}</strong></div>)}</div><div className="table-wrap"><table><thead><tr><th>FILE</th><th>DISPOSITION</th><th>RESULT</th><th>REASON</th><th /></tr></thead><tbody>{batch.files.map((file) => <tr key={file.file}><td><strong>{file.file}</strong></td><td>{file.kind}</td><td><Status status={file.outcome === 'paid' ? 'completed' : file.outcome} /></td><td>{file.reason}{file.related_file && <small>Related file: {file.related_file}</small>}</td><td>{file.run_id && <button className="text-button" onClick={() => openRun(file.run_id!)}>Open<ArrowRight size={14} /></button>}</td></tr>)}</tbody></table></div></div>}</section>
        : screen === 'rules' ? <section className="rules-page"><div className="page-heading"><div><span className="page-eyebrow">WORKSPACE SETTINGS</span><h1>Processing rules</h1><p>The server’s current limits and approval settings.</p></div>{config && <span className="version-tag">Policy {config.policy_version}</span>}</div>{config ? <div className="rules-grid"><div className="content-card"><span className="rules-icon"><ShieldCheck size={22} /></span><h2>Approvals & payments</h2><dl><div><dt>Additional scrutiny above</dt><dd>{formatAmount(config.approval_threshold, config.approval_currency)}</dd></div><div><dt>Automatic payment currencies</dt><dd>{config.payment_currencies.join(', ')}</dd></div><div><dt>Payment integration</dt><dd>Simulated only</dd></div></dl><p>Findings and current policy are checked again before a mock payment is recorded. A model’s approval does not bypass these checks.</p></div><div className="content-card"><span className="rules-icon"><FileText size={22} /></span><h2>Document intake</h2><dl><div><dt>Supported formats</dt><dd>{config.supported_extensions.join(', ').toUpperCase()}</dd></div><div><dt>Maximum file size</dt><dd>{(config.limits.max_file_bytes / 1024 / 1024).toFixed(0)} MB</dd></div><div><dt>Maximum PDF pages</dt><dd>{config.limits.max_pdf_pages}</dd></div><div><dt>Maximum line items</dt><dd>{config.limits.max_line_items}</dd></div></dl><p>Original source text is preserved with the run. Unknown or invalid fields are recorded as findings.</p></div></div> : <div className="loading-card">Connect to the workspace to load processing rules.</div>}</section>
        : <><div className="page-heading"><div><span className="page-eyebrow">ACCOUNTS PAYABLE</span><h1>{screen === 'history' ? 'Run history' : queueFilter === 'attention' ? 'Needs review' : 'Invoices'}</h1><p>{screen === 'history' ? 'Every processing attempt, with its outcome and recorded decisions.' : queueFilter === 'attention' ? 'Invoices on hold for a person to verify the details.' : 'Manage incoming invoices, track processing, and review exceptions.'}</p></div><div className="page-actions"><button className="button button-outline refresh-button" aria-label="Refresh invoices" title="Reload the invoice list. Does not reprocess invoices or make payments." disabled={!session || historyLoading} onClick={() => void reloadHistory()}><RefreshCw size={16} className={historyLoading ? 'spin' : ''} /><span>{historyLoading ? 'Refreshing…' : 'Refresh'}</span></button><button className="button button-primary" aria-label="Start a new invoice" disabled={!session} onClick={newInvoice}><Plus size={17} />New invoice</button></div></div>
          <InvoiceQueue items={history} filter={queueFilter} onFilter={setQueueFilter} openRun={openRun} newInvoice={newInvoice} trySamples={() => { newInvoice(); setIntakeTab('sample'); }} connected={Boolean(session)} cursor={historyCursor} loadMore={() => void reloadHistory(historyCursor ?? undefined)} loading={historyLoading} />
          {history.length > 0 && screen === 'workspace' && <div className="sample-entry"><FileStack size={17} /><span>Want to explore more scenarios?</span><button className="text-button" disabled={!session} onClick={() => { newInvoice(); setIntakeTab('sample'); }}>Open sample invoices<ArrowRight size={14} /></button></div>}
        </>}
        <footer className="footer"><span>Invoice Desk <span className="footer-dot">·</span> Acme Corp case study</span><span><ShieldCheck size={13} />No real money moves</span></footer>
      </div>
    </main>
    <IntakePanel open={intakeOpen} close={() => { if (!pending) { setIntakeOpen(false); setError(null); } }} tab={intakeTab} setTab={(value) => { setIntakeTab(value); resetSelection(); setError(null); }} samples={samples} config={config} selectedSample={selectedSample} selectSample={(value) => { setSelectedSample(value); setSelectedFile(null); uploadRef.current = null; requestRef.current = null; }} selectedFile={selectedFile} chooseFile={chooseFile} mode={mode} setMode={(value) => { setMode(value); requestRef.current = null; }} pending={pending} pendingText={pendingText} process={() => void startRun()} batch={() => void startBatch()} connected={Boolean(session)} error={error} />
  </div>;
}

export default App;
