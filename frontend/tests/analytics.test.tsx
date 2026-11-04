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
  expect(screen.getByText('Failed or interrupted')).toBeVisible();
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
