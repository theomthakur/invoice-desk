import {render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {it,expect,vi} from 'vitest';
import {ApiClient} from '../src/api/client';
import {BulkHandoff} from '../src/features/history/BulkHandoff';
import type {HistoryItem, RunResult} from '../src/types/api';

it('records only eligible handoffs and reports each result without approving payment',async()=>{
  const api=new ApiClient();
  const routing={assignment:null,can_route:true,departments:[{value:'procurement',label:'Procurement'}],reasons:[{value:'inventory_check',label:'Inventory needs checking'}]};
  vi.spyOn(api,'routing').mockResolvedValue(routing);
  vi.spyOn(api,'getRun').mockResolvedValue({run_id:'one',version_id:'version',content_hash:'hash'} as RunResult);
  const route=vi.spyOn(api,'routeReview').mockResolvedValue({handoff_id:'h',department:'procurement',department_label:'Procurement',reason_code:'inventory_check',reason:'Inventory needs checking',note:'',created_at:'2026-11-04',active:true,notification_sent:false});
  const review=vi.spyOn(api,'review'); const refresh=vi.fn();
  const items=[{run_id:'one',invoice_number:'INV-1',outcome:'needs_review',lifecycle:'completed',started_at:'2026-11-04'}, {run_id:'two',invoice_number:'INV-2',outcome:'rejected',lifecycle:'completed',started_at:'2026-11-04'}] as HistoryItem[];
  const user=userEvent.setup(); render(<BulkHandoff api={api} items={items} close={vi.fn()} refresh={refresh}/>);
  await user.selectOptions(await screen.findByLabelText('Department'),'procurement');
  await user.selectOptions(screen.getByLabelText('Reason'),'inventory_check');
  expect(screen.getByRole('button',{name:'Record handoffs'})).toBeDisabled();
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button',{name:'Record handoffs'}));
  expect(await screen.findByText('INV-1: handoff recorded.')).toBeVisible();
  expect(screen.getByText('INV-2: skipped, not awaiting review.')).toBeVisible();
  expect(route).toHaveBeenCalledOnce(); expect(review).not.toHaveBeenCalled(); expect(refresh).toHaveBeenCalledOnce();
});
