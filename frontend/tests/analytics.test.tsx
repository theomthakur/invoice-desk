import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ApiClient } from '../src/api/client';
import { AnalyticsPage } from '../src/features/history/AnalyticsPage';

it('describes runs as attempts and displays simulated totals by currency', async () => {
  const api = new ApiClient();
  vi.spyOn(api,'analyticsOverview').mockResolvedValue({identified_invoices:1,total_runs:2,active_runs:0,failed_runs:0,review_runs:1,flagged_active_versions:0,simulated_receipts:1,paid_by_currency:[{currency:'USD',amount:'5000.00',receipts:1}]});
  render(<AnalyticsPage api={api} openHistory={vi.fn()}/>);
  expect(await screen.findByText('Identified invoices')).toBeVisible();
  expect(screen.getByText('Includes retries, duplicates and batch extraction')).toBeVisible();
  expect(screen.getByText('Historical run outcomes, not an outstanding balance')).toBeVisible();
  expect(screen.getByText('$5,000.00')).toBeVisible();
  expect(screen.getByText(/no real money moved/)).toBeVisible();
});
