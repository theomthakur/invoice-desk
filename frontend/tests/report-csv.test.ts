import { expect, it } from 'vitest';
import { decisionCsv, tableCsv } from '../src/features/runs/reportCsv';
import type { RunResult } from '../src/types/api';

it('exports recorded values and escapes formulas, commas, quotes and newlines', () => {
  const run:RunResult={run_id:'run_1',workspace_id:'ws_1',lifecycle:'completed',outcome:'paid',filename:'=cmd',version_id:'ver_1',findings:[{code:'x',severity:'ERROR',message:'line 1, "quoted"\nline 2'}],approval:[],requires_scrutiny:false};
  const csv=decisionCsv(run);
  expect(csv).toContain('"Filename","\'=cmd"');
  expect(csv).toContain('"Outcome","Receipt unavailable"');
  expect(csv).toContain('"ERROR: line 1, ""quoted""\nline 2"');
  expect(csv).toContain('"Payment system","Simulated only"');
});

it('escapes spreadsheet formulas in selected list exports', () => {
  const csv=tableCsv([['Invoice','Vendor'],['INV-1','  =HYPERLINK("bad")'],['INV-2','Safe, Inc.']]);
  expect(csv).toContain('"\'  =HYPERLINK(""bad"")"');
  expect(csv).toContain('"Safe, Inc."');
});
