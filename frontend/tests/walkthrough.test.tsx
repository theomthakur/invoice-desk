import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Walkthrough } from '../src/features/walkthrough/Walkthrough';

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class {observe() {} disconnect() {}});
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => vi.unstubAllGlobals());
const defaults = { intakeOpen:false, selected:false, run:null, runId:null, index:0, setIndex:vi.fn(), close:vi.fn(), reopen:vi.fn(), openOverview:vi.fn() };
it('does not advance intake until the user opens it, and close works', async () => {
  const props = {...defaults, index:1, setIndex:vi.fn(), close:vi.fn()};
  const view=render(<Walkthrough {...props}/>);
  const user=userEvent.setup();
  expect(screen.getByRole('button', {name:'Next'})).toBeDisabled();
  view.rerender(<Walkthrough {...props} intakeOpen/>);
  await user.click(screen.getByRole('button', {name:'Next'}));
  expect(props.setIndex).toHaveBeenCalledWith(2);
  await user.click(screen.getByRole('button', {name:'Close walkthrough'}));
  expect(props.close).toHaveBeenCalledOnce();
});
it('waits for explicit processing and offers a recovery if intake was closed', async () => {
  const props={...defaults,index:3,reopen:vi.fn()};
  render(<Walkthrough {...props}/>);
  expect(screen.getByRole('button',{name:'Next'})).toBeDisabled();
  const user=userEvent.setup();
  await user.click(screen.getByRole('button',{name:'Reopen new invoice'}));
  expect(props.reopen).toHaveBeenCalledOnce();
  expect(screen.getByText(/Next never submits a document/)).toBeVisible();
});
it('cleans the highlight and guide spacing when closed', () => {
  const target=document.createElement('div');target.dataset.tour='inbox';document.body.append(target);
  const view=render(<Walkthrough {...defaults}/>);
  expect(target).toHaveClass('tour-highlight');
  view.rerender(<Walkthrough {...defaults} index={7}/>);
  expect(screen.getByRole('button', {name:'Return to invoice overview'})).toBeVisible();
  expect(target).not.toHaveClass('tour-highlight');
  view.unmount();
  expect(target).not.toHaveClass('tour-highlight');
  expect(document.documentElement.style.getPropertyValue('--guide-height')).toBe('');
  target.remove();
});
