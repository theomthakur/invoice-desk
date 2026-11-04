import {describe,it,expect} from 'vitest';
import {activitySummary} from '../src/features/runs/activity';
describe('agent activity labels',()=>{
  it('identifies the critic model request without calling it a reader',()=>{
    const result=activitySummary({seq:1,timestamp:'2026-11-04T12:00:00Z',stage:'critique',type:'model_request',payload:{label:'critic'}});
    expect(result.label).toBe('Model request started');
    expect(result.stage).toBe('Critique');
    expect(result.message).toBe('Role: critic.');
  });
  it('does not invent a role for events without a role label',()=>{
    const result=activitySummary({seq:1,timestamp:'2026-11-04T12:00:00Z',stage:'approval',type:'model_request',payload:{}});
    expect(result.message).toBeNull();
  });
});
