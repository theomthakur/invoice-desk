import type { RunEvent, RunResult } from '../../types/api';

type Stage = 'intake' | 'extraction' | 'validation' | 'approval' | 'payment';
export type StageState = 'complete' | 'in_progress' | 'reached' | 'blocked' | 'failed' | 'not_reached';

/** Applied human decisions supersede earlier model decisions and refused gates. */
export function reviewDecision(events: RunEvent[]): string | undefined {
  const latest = [...events].reverse().find(event => ['review_applied','final_decision','gate_refused'].includes(event.type));
  if (latest?.type === 'review_applied') {
    return latest.payload.outcome === 'paid' ? 'approve' : latest.payload.outcome === 'rejected' ? 'reject' : latest.payload.outcome === 'needs_review' ? 'needs_review' : undefined;
  }
  return latest?.type === 'final_decision' ? String(latest.payload.action) : undefined;
}

/** Report only what the recorded events and receipt actually establish. */
export function stageState(stage: Stage, run: RunResult, events: RunEvent[]): StageState {
  const relevant = events.filter(event => event.stage === stage || (stage === 'extraction' && ['parsed_structured', 'reused_prepared_extraction', 'reused_stored_extraction'].includes(event.type) && event.stage === 'intake') || (stage === 'approval' && event.stage === 'critique'));
  const types = new Set(relevant.map(event => event.type));
  if (stage === 'payment' && run.receipt) return 'complete';
  if (stage === 'payment' && types.has('payment_refused')) return 'blocked';
  if (stage === 'approval' && reviewDecision(events)) return 'complete';
  if (stage === 'approval' && types.has('gate_refused')) return 'blocked';
  const decision = [...relevant].reverse().find(event => event.type === 'final_decision');
  if (stage === 'approval' && decision && ['approve', 'reject', 'needs_review'].includes(String(decision.payload.action))) return 'complete';
  const completed = (stage === 'intake' && types.has('document_received'))
    || (stage === 'extraction' && ['extracted', 'parsed_structured', 'reused_prepared_extraction', 'reused_stored_extraction'].some(type => types.has(type)))
    || (stage === 'validation' && types.has('validated'))
    || (stage === 'approval' && decision?.payload.action === 'approve');
  if (completed) return 'complete';
  const failure = [...events].reverse().find(event => event.type === 'node_failed');
  const failureStage: Record<string, Stage> = {intake:'intake',extract:'extraction',validate:'validation',register:'validation',unnormalized:'validation',catalog:'approval',review:'approval'};
  const failedStage = failure ? failureStage[String(failure.payload.node)] : undefined;
  const lastWork = [...events].reverse().find(event => ['intake','extraction','validation','approval','critique','payment'].includes(event.stage));
  if ((run.lifecycle === 'failed' || run.lifecycle === 'interrupted') && (failedStage === stage || (!failedStage && lastWork && relevant.includes(lastWork)))) return 'failed';
  const active = run.lifecycle === 'running' || run.lifecycle === 'queued';
  if (active && lastWork && relevant.includes(lastWork)) return 'in_progress';
  return relevant.length ? 'reached' : 'not_reached';
}
