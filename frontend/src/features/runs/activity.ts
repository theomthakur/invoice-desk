import type { RunEvent } from '../../types/api';

const labels: Record<string, string> = {
  run_queued: 'Processing queued', run_started: 'Processing started', run_finished: 'Processing finished', document_received: 'Document received',
  parsed_structured: 'Structured document parsed', reused_stored_extraction: 'Previous reading of this file reused',
  reused_prepared_extraction: 'Prepared reading reused', extracted: 'Invoice details extracted',
  extraction_checked: 'Extracted details checked against the source', reflection_result: 'A second reading was assessed', reflection_skipped: 'A second reading was skipped', reflection_failed: 'A second reading failed',
  validated: 'Invoice details checked', registered: 'Invoice registered in the ledger', catalog_evidence: 'Inventory evidence collected',
  context_loaded: 'Approval context prepared', proposal: 'Approver recommendation recorded', revision: 'Approver revised the recommendation', critique: 'Critic assessment recorded', final_decision: 'Decision recorded',
  payment_attempt: 'Simulated payment attempted', payment_made: 'Simulated payment recorded',
  payment_refused: 'Payment was stopped', gate_refused: 'Approval was stopped',
  review_submitted: 'Simulated review submitted', review_refused: 'Review was not accepted',
  department_handoff: 'Internal department handoff recorded', node_failed: 'A processing step failed',
  run_crashed: 'Processing failed', model_retry: 'Model request retried', output_invalid: 'Model output failed validation',
  tool_not_called: 'Required tool was not called', unresolved: 'Review disagreement remains unresolved',
  audit_skipped: 'Decision record could not be saved', extraction_only_finished: 'Document reading finished',
  model_request: 'A reader request started', model_response: 'A reader response was received', model_error: 'A reader request failed', tool_call: 'A tool call was recorded', run_interrupted: 'Processing was interrupted',
};
const stages: Record<string, string> = {intake:'Intake', extraction:'Extraction', validation:'Validation', approval:'Approval', critique:'Critique', payment:'Payment', completion:'Completion', error:'Error'};
export function activitySummary(event: RunEvent) {
  const label = labels[event.type] ?? 'Other recorded activity';
  const {message, rationale, action, agrees, line_items} = event.payload;
  let description = typeof message === 'string' ? message : null;
  if ((event.type === 'proposal' || event.type === 'revision') && typeof rationale === 'string') description = `${typeof action === 'string' ? `Recommendation: ${action}. ` : ''}${rationale}`;
  if (event.type === 'critique' && typeof agrees === 'boolean') description = agrees ? 'The critic agreed with the recommendation.' : 'The critic did not agree with the recommendation.';
  if ((event.type === 'parsed_structured' || event.type === 'extracted') && typeof line_items === 'number') description = `${line_items} line item(s) recorded.`;
  return {label, stage: stages[event.stage] ?? 'Activity', message: description};
}
