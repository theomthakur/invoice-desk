import { ArrowRight, Check, ChevronDown, FileStack, FileText, LoaderCircle, UploadCloud, X } from 'lucide-react';
import { useEffect, useRef } from 'react';
import type { ConfigInfo, Mode, SampleInfo } from '../../types/api';

export function IntakePanel({ developerMode = false, selectedModel = '', setSelectedModel, guideActive = false, open, close, tab, setTab, samples, config, selectedSample, selectSample, selectedFile, chooseFile, mode, setMode, pending, pendingText, process, batch, connected, error }: {
  developerMode?: boolean; selectedModel?: string; setSelectedModel?: (model: string) => void;
  guideActive?: boolean; open: boolean; close: () => void; tab: 'upload' | 'sample'; setTab: (tab: 'upload' | 'sample') => void;
  samples: SampleInfo[]; config: ConfigInfo | null; selectedSample: string; selectSample: (value: string) => void;
  selectedFile: File | null; chooseFile: (file: File | null) => void; mode: Mode; setMode: (mode: Mode) => void;
  pending: boolean; pendingText: string; process: () => void; batch: () => void; connected: boolean; error: string | null;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    panel.current?.focus();
    return () => { document.body.style.overflow = oldOverflow; previous?.focus(); };
  }, [open, guideActive]);
  if (!open) return null;
  const disabled = pending || !connected;
  const valid = tab === 'upload' ? Boolean(selectedFile) : Boolean(selectedSample);
  return <div className="drawer-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) close(); }}>
    <div className="intake-panel" ref={panel} role="dialog" aria-modal={guideActive ? undefined : true} aria-labelledby="intake-title" tabIndex={-1} onKeyDown={(event) => {
      if (event.key === 'Escape' && !pending) close();
      if (event.key === 'Tab' && !guideActive) {
        const nodes = [...(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href]') ?? [])].filter((node) => !node.hasAttribute('hidden'));
        const first = nodes[0]; const last = nodes.at(-1);
        if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.current)) { event.preventDefault(); first?.focus(); }
      }
    }}>
      <div className="drawer-head"><div><span className="mini-label">INVOICE INTAKE</span><h2 id="intake-title">New invoice</h2><p>Add a document. We’ll take it from here.</p></div><button className="icon-button" aria-label="Close new invoice" onClick={close} disabled={pending}><X size={20} /></button></div>
      <div className="drawer-body"><div className="intake-source-tabs" role="group" aria-label="Invoice source"><button aria-pressed={tab === 'upload'} onClick={() => setTab('upload')} disabled={pending}><UploadCloud size={16} />Upload file</button><button aria-pressed={tab === 'sample'} onClick={() => setTab('sample')} disabled={pending}><FileStack size={16} />Sample invoices</button></div>
        {error && <div className="inline-error" role="alert">{error}</div>}
        {tab === 'upload' ? <><label className="drop-zone" htmlFor="invoice-upload" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); if (!disabled) chooseFile(event.dataTransfer.files[0]); }}>
          <input id="invoice-upload" ref={input} type="file" aria-label="Or upload a file" disabled={disabled} accept={config?.supported_extensions.map((ext) => ext.startsWith('.') ? ext : `.${ext}`).join(',')} onChange={(event) => chooseFile(event.target.files?.[0] ?? null)} />
          <span className="upload-emblem"><UploadCloud size={25} strokeWidth={1.6} /></span><strong>Drop an invoice here</strong><span>or <b>browse files</b> on your computer</span><small>{config ? `${config.supported_extensions.join(' · ').toUpperCase()} · up to ${(config.limits.max_file_bytes / 1024 / 1024).toFixed(0)} MB` : 'Connect to see supported formats'}</small>
        </label>{selectedFile && <div className="selected-document"><span className="file-symbol"><FileText size={19} /></span><div><strong>{selectedFile.name}</strong><small>{(selectedFile.size / 1024).toFixed(1)} KB · ready to process</small></div><Check size={18} /></div>}</>
        : <><label htmlFor="sample" className="input-label">Use a sample invoice</label><div className="select-wrap"><select id="sample" value={selectedSample} disabled={disabled} onChange={(event) => selectSample(event.target.value)}><option value="">Select an invoice</option>{samples.map((sample) => <option key={sample.sample_id} value={sample.sample_id}>{sample.name}</option>)}</select><ChevronDown size={16} /></div><p className="field-help">Sample invoices include different formats, revisions and exceptions.</p>
          <div className="batch-option"><FileStack size={19} /><div><strong>Process the full sample set</strong><p>Group duplicate files and revisions before any simulated payment.</p><button className="text-button" disabled={disabled} onClick={batch}>Run sample batch <ArrowRight size={14} /></button></div></div></>}
        <div className="intake-setting"><div><label htmlFor="model">AI model</label><p>{mode === 'live' ? (config?.model_provider === 'ollama' ? 'Runs on the server’s local model.' : `Real AI responses through ${config?.model_provider === 'xai' ? 'xAI' : 'OpenRouter'}. Uses API credit.`) : 'Offline test responses. No model API is called.'}</p></div><select id="model" aria-label="AI model" disabled={disabled} value={mode === 'mock' ? '__offline__' : selectedModel} onChange={(event) => { const value = event.target.value; setMode(value === '__offline__' ? 'mock' : 'live'); if (value !== '__offline__') setSelectedModel?.(value); }}>
          {config?.enabled_modes.includes('live') && (config.model_options?.length ? config.model_options : [{id: '', label: 'Configured model'}]).map(option => <option key={option.id} value={option.id}>{option.label}{config.model_provider === 'openrouter' ? ' · OpenRouter' : ''}</option>)}
          {config?.enabled_modes.includes('mock') && (developerMode || !config.enabled_modes.includes('live')) && <option value="__offline__">Offline test (no AI)</option>}
        </select></div>
        {!config?.enabled_modes.includes('live') && <p className="field-help">Live AI is not configured on this server. Configure the provider key, spending limit and browser access to enable it.</p>}
        <div className="intake-expectation"><h3>What happens next</h3><ol><li><span>1</span><div><strong>Read the invoice</strong><p>Extract vendor, dates, items and amounts.</p></div></li><li><span>2</span><div><strong>Check the details</strong><p>Validate totals, inventory and approval policy.</p></div></li><li><span>3</span><div><strong>Record the decision</strong><p>See the result, exceptions and mock receipt.</p></div></li></ol></div>
      </div><div className="drawer-footer"><span>All payments in this workspace are simulated.</span><div><button className="button button-outline" onClick={close} disabled={pending}>Cancel</button><button data-tour="process" className="button button-primary" onClick={process} disabled={disabled || !valid}>{pending ? pendingText || 'Processing…' : 'Process invoice'}{pending ? <LoaderCircle size={16} className="spin" /> : <ArrowRight size={16} />}</button></div></div>
    </div>
  </div>;
}
