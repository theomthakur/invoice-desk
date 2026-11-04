import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { ApiClient } from '../src/api/client';
import { InvoiceLookup } from '../src/features/history/InvoiceLookup';

it('shows separate vendors and opens the selected version without changing the ledger', async () => {
  const api = new ApiClient(); const openRun = vi.fn();
  const lookup = vi.spyOn(api, 'lookupInvoice').mockResolvedValue({truncated:false, matches:[
    {payable_id:'pay_1', number:'INV-4', vendor:'First vendor', paid:true, payment_version_id:'v1', versions_truncated:false, versions:[{version_id:'v1', revision:null, status:'active', identity_flag:null, run_id:'run_1', run_outcome:'paid'}]},
    {payable_id:'pay_2', number:'INV-4', vendor:'Second vendor', paid:false, payment_version_id:null, versions_truncated:false, versions:[{version_id:'v2', revision:'2', status:'active', identity_flag:'conflict', run_id:'run_2', run_outcome:'needs_review'}]},
  ]});
  const user=userEvent.setup(); render(<InvoiceLookup api={api} initialNumber="" openRun={openRun}/>);
  await user.type(screen.getByLabelText('Invoice number'), 'INV-4');
  await user.click(screen.getByRole('button',{name:'Find invoice'}));
  expect(await screen.findByText('INV-4 · First vendor')).toBeVisible();
  expect(screen.getByText('INV-4 · Second vendor')).toBeVisible();
  expect(screen.getByText(/simulated payment version/)).toBeVisible();
  await user.click(screen.getAllByRole('button',{name:'Open record'})[1]);
  expect(openRun).toHaveBeenCalledWith('run_2');
  expect(lookup).toHaveBeenCalledWith('INV-4');
});
