import type { RunEvent, RunResult } from '../../types/api';

type Stage = 'intake' | 'extraction' | 'validation' | 'approval' | 'payment';
export type StageState = 'complete' | 'in_progress' | 'reached' | 'blocked' | 'failed' | 'not_reached';

/** Report only what the recorded events and receipt actually establish. */
export function stageState(stage: Stage, run: RunResult, events: RunEvent[]): StageState {
  const relevant = events.filter(event => event.stage === stage || (stage === 'approval' && event.stage === 'critique'));
  const types = new Set(relevant.map(event => event.type));
  if (stage === 'payment' && run.receipt) return 'complete';
  if (stage === 'payment' && types.has('payment_refused')) return 'blocked';
  if (stage === 'approval' && types.has('gate_refused')) return 'blocked';
  const decision = [...relevant].reverse().find(event => event.type === 'final_decision');
  if (stage === 'approval' && decision && ['reject', 'needs_review'].includes(String(decision.payload.action))) return 'blocked';
  const completed = (stage === 'intake' && types.has('document_received'))
    || (stage === 'extraction' && (types.has('extracted') || types.has('parsed_structured')))
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
