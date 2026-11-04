import { ArrowLeft, ArrowRight, Check, Compass, X } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { RunResult } from '../../types/api';
import './walkthrough.css';

interface Props {
  intakeOpen: boolean; selected: boolean; run: RunResult | null; runId: string | null;
  index: number; setIndex: (index: number) => void; close: () => void;
  reopen: () => void; openOverview: () => void;
}
export function Walkthrough({intakeOpen, selected, run, runId, index, setIndex, close, reopen, openOverview}: Props) {
  const card = useRef<HTMLElement>(null);
  const [targetAvailable, setTargetAvailable] = useState(false);
  const completed = Boolean(run && run.lifecycle !== 'running' && run.lifecycle !== 'queued');
  const steps = [
    {title: 'A quick tour of your workspace', text: 'We will add one invoice, read its result and find the supporting evidence. Your existing records stay intact. Nothing is processed or approved by pressing Next.', target: '[data-tour="inbox"]', ready: true},
    {title: 'Open a new invoice', text: 'Click the highlighted New invoice button. This opens intake without uploading or processing anything.', target: '[data-tour="new-invoice"]', ready: intakeOpen || Boolean(runId)},
    {title: 'Choose a document', text: 'Upload your own invoice, or click Sample invoices and select a file. For a review example, try invoice_1012.txt. Choose Simulated mode to avoid model charges. Existing records may affect duplicate outcomes.', target: '.intake-source-tabs', ready: (intakeOpen && selected) || Boolean(runId)},
    {title: 'Start processing when you are ready', text: 'Check the selected file and processing mode, then click Process invoice yourself. That action starts a real run in this workspace. Next never submits a document or makes a payment.', target: '[data-tour="process"]', ready: Boolean(runId)},
    {title: 'Follow the processing stages', text: completed ? 'Processing has stopped. Received, Extracted, Checked, Reviewed and Payment show the recorded stages. Completed processing does not always mean an invoice was paid.' : 'The worker is reading and checking the invoice. Wait for a result before continuing. If processing fails, the result will explain what happened.', target: '.stage-row', ready: completed},
    {title: 'Read the decision first', text: run?.outcome === 'paid' ? 'This run has a simulated payment outcome. Check its stored receipt. No real money moved.' : run?.outcome === 'needs_review' ? 'This invoice is on hold. A person needs to check the flagged evidence before any permitted simulated approval.' : 'Read the outcome and findings. Rejected, duplicate, superseded and failed runs are different from paid invoices. Never treat a finished run as proof of payment.', target: '.detail-head', ready: completed},
    {title: 'Compare the original and extracted fields', text: 'Read the source alongside the extracted vendor, dates, items and totals. PDFs have an original preview; other formats show retained text. Click Find in source on a finding to locate its evidence. These fields are for inspection, not inline editing.', target: '.document-pane', ready: completed},
    {title: 'Understand the review actions', text: run?.outcome === 'needs_review' ? 'Choose Approve, Reject or Assign to a department, then select a reason. The note is optional. Approval requires every permitted finding and the source confirmation. A handoff keeps payment on hold and sends no notification. You do not have to submit a decision to finish this tour.' : 'This run is not waiting for review, so no review form appears. Held invoices offer permitted actions, predefined reasons and optional notes. Unsupported issues cannot be force-approved. No review action is required for this tour.', target: run?.outcome === 'needs_review' ? '.review-card' : '.record-pane', ready: completed},
    {title: 'Open the audit trail', text: 'Click Activity above the invoice. It shows the events the server actually recorded, including department handoffs and review decisions. It does not imply that every agent ran for every input.', target: '[data-tour="activity"]', ready: true},
    {title: 'Save a readable report', text: 'Save activity PDF opens a readable processing trace. Choose Save as PDF in the print dialog. Technical JSON is optional and appears only in Developer mode.', target: '[data-tour="report"]', ready: true},
    {title: 'Explore the system and return any time', text: 'System design explains how the app works. Presentation gives a short overview. The sidebar also has Run history, Processing rules, the light/dark toggle and your workspace account. Reopen Guided walkthrough whenever you need a reminder.', target: '[data-tour="resources"]', ready: true},
  ];
  const step = steps[index];
  useEffect(() => {
    let current: Element | null | undefined;
    const highlight = () => {
      const element = document.querySelector(step.target);
      if (element === current) return;
      current?.classList.remove('tour-highlight'); current = element;
      setTargetAvailable(Boolean(element));
      element?.classList.add('tour-highlight');
      element?.scrollIntoView({block: 'center', behavior: 'instant'});
    };
    highlight();
    const observer = new MutationObserver(highlight);
    observer.observe(document.body, {childList:true, subtree:true});
    return () => { observer.disconnect(); current?.classList.remove('tour-highlight'); };
  }, [step.target, index]);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    card.current?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);
  useLayoutEffect(() => {
    const element = card.current;
    if (!element) return;
    const measure = () => document.documentElement.style.setProperty('--guide-height', `${element.getBoundingClientRect().height + 24}px`);
    measure();
    const observer = new ResizeObserver(measure); observer.observe(element);
    return () => { observer.disconnect(); document.documentElement.style.removeProperty('--guide-height'); };
  }, [index]);
  return <section className="walkthrough-card" ref={card} tabIndex={-1} role="region" aria-label="Guided walkthrough" onKeyDown={event => {if(event.key === 'Escape') {event.stopPropagation();close();}}}>
    <header><span><Compass size={17} />GUIDED WALKTHROUGH</span><button className="icon-button" aria-label="Close walkthrough" onClick={close}><X size={17}/></button></header>
    <div className="walkthrough-copy" aria-live="polite" aria-atomic="true"><small>Step {index + 1} of {steps.length}</small><h2>{step.title}</h2><p>{step.text}</p></div>
    {!intakeOpen && index >= 2 && index <= 3 && !runId && <button className="text-button" onClick={reopen}>Reopen new invoice</button>}
    {index >= 6 && index <= 7 && !targetAvailable && <button className="text-button" onClick={openOverview}>Return to invoice overview</button>}
    {!step.ready && <p className="walkthrough-wait">Complete the step above to continue. You can close the guide at any time.</p>}
    <footer><button className="button button-outline" disabled={index === 0} onClick={() => setIndex(index - 1)}><ArrowLeft size={14}/>Back</button><button className="button button-primary" disabled={!step.ready} onClick={() => index === steps.length - 1 ? close() : setIndex(index + 1)}>{index === steps.length - 1 ? <><Check size={14}/>Finish tour</> : <>Next<ArrowRight size={14}/></>}</button></footer>
  </section>;
}
