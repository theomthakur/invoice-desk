import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { ApiClient } from '../src/api/client';
import { AnalyticsPage } from '../src/features/history/AnalyticsPage';

it('describes runs as attempts and displays simulated totals by currency', async () => {
  const api = new ApiClient();
  vi.spyOn(api,'analyticsOverview').mockResolvedValue({identified_invoices:1,total_runs:2,active_runs:0,failed_runs:0,review_runs:1,flagged_active_versions:0,simulated_receipts:1,paid_by_currency:[{currency:'USD',amount:'5000.00',receipts:1}]});
  render(<AnalyticsPage api={api} openHistory={vi.fn()}/>);
  expect(await screen.findByRole('heading',{name:'Invoice outcomes'})).toBeVisible();
  expect(screen.getByRole('heading',{name:'Activity by vendor'})).toBeVisible();
  expect(screen.queryByText('Identified invoices')).not.toBeInTheDocument();
  expect(screen.queryByText('Runs marked for review')).not.toBeInTheDocument();
  expect(screen.getByRole('heading',{name:'Invoice records'})).toBeVisible();
  expect(screen.getByRole('row',{name:/Distinct invoices.*1$/})).toBeVisible();
  expect(screen.getByText('$5,000.00')).toBeVisible();
  expect(screen.getByText(/no real money moved/)).toBeVisible();
});

it('applies and clears ledger filters without presenting filtered receipts as workspace totals', async () => {
  const api = new ApiClient();
  const all = {identified_invoices:1,total_runs:2,active_runs:0,failed_runs:0,review_runs:1,flagged_active_versions:0,simulated_receipts:1,paid_by_currency:[{currency:'USD',amount:'5000.00',receipts:1}],outcomes:{paid:1,failed:1},daily_runs:[{day:'2026-11-04',count:2}],identity_flags:[{flag:'revision_conflict',count:1}]};
  const lookup = vi.spyOn(api,'analyticsOverview').mockResolvedValueOnce(all).mockResolvedValueOnce({...all,simulated_receipts:0,paid_by_currency:[]}).mockResolvedValueOnce(all);
  const user = userEvent.setup();
  render(<AnalyticsPage api={api} openHistory={vi.fn()}/>);
  expect(await screen.findByText('$5,000.00')).toBeVisible();
  expect(screen.getByRole('img',{name:'Run counts by date: 2026-11-04: 2'})).toBeVisible();
  expect(screen.getByText('Failed or interrupted',{selector:'span'})).toBeVisible();
  expect(screen.getByRole('row',{name:/Conflicting revisions\s*1/})).toBeVisible();
  await user.type(screen.getByLabelText('Vendor contains'),'Acme');
  await user.selectOptions(screen.getByLabelText('Run outcome'),'rejected');
  await user.click(screen.getByRole('button',{name:'Apply filters'}));
  expect(await screen.findByText('No simulated receipts recorded.')).toBeVisible();
  expect(lookup).toHaveBeenLastCalledWith({date_from:'',date_to:'',vendor:'Acme',outcome:'rejected'});
  expect(screen.getByText(/Filtered results: vendor contains Acme/)).toBeVisible();
  await user.click(screen.getByRole('button',{name:'Clear filters'}));
  expect(await screen.findByText('$5,000.00')).toBeVisible();
});

it('opens current backlog records from a chart and links to the actual run', async()=>{
  const api=new ApiClient();const openRun=vi.fn();const user=userEvent.setup();
  vi.spyOn(api,'analyticsOverview').mockResolvedValue({identified_invoices:1,total_runs:3,active_runs:0,failed_runs:0,review_runs:1,flagged_active_versions:0,simulated_receipts:0,paid_by_currency:[],current_review_count:1,current_exception_count:1,backlog_by_department:[{key:'procurement',label:'Procurement',count:1}],exception_reasons:[{key:'TOTAL_MISMATCH',label:'Total mismatch',count:1}]});
  const records=vi.spyOn(api,'analyticsRecords').mockResolvedValue({records:[{run_id:'run_current',version_id:'version_current',invoice_number:'INV-42',vendor:'Widgets',filename:'invoice.txt',outcome:'needs_review',department:'procurement',department_label:'Procurement',reasons:['TOTAL_MISMATCH'],started_at:'2026-11-04T12:00:00Z'}],record_count:1,next_offset:null});
  render(<AnalyticsPage api={api} openHistory={vi.fn()} openRun={openRun}/>);
  await user.click(await screen.findByRole('button',{name:'View Procurement: 1 records'}));
  expect(await screen.findByText('INV-42')).toBeVisible();
  expect(records).toHaveBeenCalledWith('backlog','procurement',{date_from:'',date_to:'',vendor:'',outcome:''},0);
  await user.click(screen.getByRole('button',{name:'Open record'}));expect(openRun).toHaveBeenCalledWith('run_current');
  await user.click(screen.getByRole('button',{name:'Close records'}));expect(screen.queryByText('INV-42')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button',{name:'View Total mismatch: 1 records'}));
  expect(records).toHaveBeenLastCalledWith('exceptions','TOTAL_MISMATCH',{date_from:'',date_to:'',vendor:'',outcome:''},0);
});

it('discards old chart records when the applied filters change during loading',async()=>{
  const api=new ApiClient();const user=userEvent.setup();
  const overview={identified_invoices:1,total_runs:1,active_runs:0,failed_runs:0,review_runs:1,flagged_active_versions:0,simulated_receipts:0,paid_by_currency:[],backlog_by_department:[{key:'unassigned',label:'Unassigned',count:1}]};
  vi.spyOn(api,'analyticsOverview').mockResolvedValue(overview);
  let finish!:(value:Awaited<ReturnType<ApiClient['analyticsRecords']>>)=>void;
  vi.spyOn(api,'analyticsRecords').mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
  render(<AnalyticsPage api={api} openHistory={vi.fn()}/>);
  await user.click(await screen.findByRole('button',{name:'View Unassigned: 1 records'}));
  await user.type(screen.getByLabelText('Vendor contains'),'New vendor');
  await user.click(screen.getByRole('button',{name:'Apply filters'}));
  finish({records:[],record_count:999,next_offset:null});
  expect(await screen.findByText(/Filtered results: vendor contains New vendor/)).toBeVisible();
  expect(screen.queryByRole('region',{name:'Matching analytics records'})).not.toBeInTheDocument();
});

it('replaces the prior page and uses a stable cursor for the next records',async()=>{
  const api=new ApiClient();const user=userEvent.setup();
  vi.spyOn(api,'analyticsOverview').mockResolvedValue({identified_invoices:1,total_runs:2,active_runs:0,failed_runs:0,review_runs:2,flagged_active_versions:0,simulated_receipts:0,paid_by_currency:[],backlog_by_department:[{key:'unassigned',label:'Unassigned',count:2}]});
  const row={run_id:'run_first',version_id:null,invoice_number:'INV-FIRST',vendor:'Vendor',filename:'invoice.txt',outcome:'needs_review' as const,department:'unassigned',department_label:'Unassigned',reasons:['REVIEW_REQUIRED'],started_at:'2026-11-04T00:00:00Z'};
  const records=vi.spyOn(api,'analyticsRecords').mockResolvedValueOnce({records:[row],record_count:2,next_offset:1,next_cursor:'cursor-one'}).mockResolvedValueOnce({records:[{...row,run_id:'run_second',invoice_number:'INV-SECOND'}],record_count:1,next_offset:null,next_cursor:null});
  render(<AnalyticsPage api={api} openHistory={vi.fn()}/>);
  await user.click(await screen.findByRole('button',{name:'View Unassigned: 2 records'}));
  expect(await screen.findByText('INV-FIRST')).toBeVisible();
  await user.click(screen.getByRole('button',{name:'Next records'}));
  expect(await screen.findByText('INV-SECOND')).toBeVisible();
  expect(screen.queryByText('INV-FIRST')).not.toBeInTheDocument();
  expect(records).toHaveBeenLastCalledWith('backlog','unassigned',{date_from:'',date_to:'',vendor:'',outcome:''},0,'cursor-one');
});

it('lets the operator filter failed or interrupted processing separately from invoice outcomes',async()=>{
  const api=new ApiClient();const user=userEvent.setup();
  const lookup=vi.spyOn(api,'analyticsOverview').mockResolvedValue({identified_invoices:0,total_runs:2,active_runs:0,failed_runs:2,review_runs:0,flagged_active_versions:0,simulated_receipts:0,paid_by_currency:[],outcomes:{failed:2}});
  render(<AnalyticsPage api={api} openHistory={vi.fn()}/>);
  await screen.findByRole('heading',{name:'Invoice outcomes'});
  await user.selectOptions(screen.getByLabelText('Run outcome'),'failed');
  await user.click(screen.getByRole('button',{name:'Apply filters'}));
  expect(lookup).toHaveBeenLastCalledWith({date_from:'',date_to:'',vendor:'',outcome:'failed'});
});
