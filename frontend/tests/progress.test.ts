import {describe,it,expect} from 'vitest';
import {stageState} from '../src/features/runs/progress';
import type {RunEvent,RunResult} from '../src/types/api';
const run:RunResult={run_id:'one',workspace_id:'ws',lifecycle:'completed',outcome:'needs_review',findings:[],approval:[],requires_scrutiny:false};
const event=(stage:string,type:string,payload:Record<string,unknown>={}):RunEvent=>({seq:1,timestamp:'2026-11-04T00:00:00Z',stage,type,payload});
describe('honest stage progress',()=>{
  it.each(['parsed_structured','reused_prepared_extraction','reused_stored_extraction'])('recognizes successful intake reading: %s',type=>{
    expect(stageState('extraction',run,[event('intake',type)])).toBe('complete');
  });
  it('does not infer extraction merely from receiving a file',()=>{
    expect(stageState('extraction',run,[event('intake','document_received')])).toBe('not_reached');
  });
  it('does not mark a refused payment as complete',()=>{
    expect(stageState('payment',run,[event('payment','payment_attempt'),event('payment','payment_refused')])).toBe('blocked');
  });
  it('requires an actual receipt to mark payment complete',()=>{
    expect(stageState('payment',run,[event('payment','payment_attempt')])).toBe('reached');
    expect(stageState('payment',{...run,outcome:'paid',receipt:{payment_id:'p',transaction_id:'t',vendor:'v',amount:'2',currency:'USD',created_at:'2026-11-04',simulated:true}},[])).toBe('complete');
  });
  it('marks rejected and held decisions as blocked',()=>{
    expect(stageState('approval',run,[event('approval','final_decision',{action:'reject'})])).toBe('blocked');
    expect(stageState('approval',run,[event('approval','final_decision',{action:'needs_review'})])).toBe('blocked');
  });
  it('marks an interrupted active stage as failed rather than successful',()=>{
    expect(stageState('extraction',{...run,lifecycle:'interrupted'},[event('extraction','model_request'),event('error','run_interrupted')])).toBe('failed');
    expect(stageState('extraction',{...run,lifecycle:'running'},[event('extraction','model_request')])).toBe('in_progress');
    expect(stageState('validation',run,[])).toBe('not_reached');
  });
  it('does not blame a completed validation when the review node fails',()=>{
    const events=[event('validation','validated'),event('error','node_failed',{node:'review'})];
    const failed={...run,lifecycle:'failed' as const};
    expect(stageState('validation',failed,events)).toBe('complete');
    expect(stageState('approval',failed,events)).toBe('failed');
  });
});
