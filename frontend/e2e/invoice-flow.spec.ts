import { expect, test, type Page } from '@playwright/test';

async function runSample(page: Page, name: string) {
  await page.getByRole('button', { name: 'Start a new invoice' }).click();
  await page.getByRole('button', { name: 'Sample invoices', exact: true }).click();
  await page.getByLabel('Use a sample invoice').selectOption({ label: name });
  const accepted = page.waitForResponse((response) =>
    response.url().endsWith('/api/v1/runs') && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Process invoice', exact: true }).click();
  const response = await accepted;
  expect(response.status()).toBe(202);
  const { run_id: runId } = await response.json() as { run_id: string };
  await expect(page.getByRole('button', { name: 'Export trace' })).toBeEnabled({ timeout: 30_000 });
  return runId;
}

test('sidebar can collapse, keep navigation usable and remember the choice', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  if (process.env.UPDATE_SCREENSHOTS === '1') await page.screenshot({path:'docs/screenshots/sidebar-navigation.png', animations:'disabled'});
  const sidebar = page.getByRole('complementary');
  await expect(sidebar.getByRole('button', {name:'System design', exact:true})).toBeVisible();
  await expect(sidebar.getByRole('button', {name:'Presentation', exact:true})).toBeVisible();
  await expect(page.getByRole('region', {name:'Workspace account'})).toBeVisible();
  await expect(page.locator('.sidebar-account')).toContainText('Acme workspace');
  const collapse = page.getByRole('button', { name: 'Collapse sidebar', exact: true });
  await expect(collapse).toHaveAttribute('aria-expanded', 'true');
  await collapse.click();
  await expect(page.locator('.sidebar')).toHaveCSS('width', '76px');
  await expect(page.getByRole('region', {name:'Workspace account'})).toBeVisible();
  await page.getByRole('button', { name: 'Processing rules', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Processing rules', exact: true })).toBeVisible();
  await page.reload();
  const expand = page.getByRole('button', { name: 'Expand sidebar', exact: true });
  await expect(expand).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('.sidebar')).toHaveCSS('width', '76px');
  await expand.click();
  await expect(page.locator('.sidebar')).toHaveCSS('width', '252px');
  await page.reload();
  await expect(page.getByRole('button', { name: 'Collapse sidebar', exact: true })).toBeVisible();
});

test('project resources explain the real system without starting invoice work', async ({ page }) => {
  const processingRequests: string[] = [];
  page.on('request', request => {
    if (request.method() === 'POST' && /\/api\/v1\/(runs|batches)(?:\/|$)/.test(new URL(request.url()).pathname)) processingRequests.push(request.url());
  });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start a new invoice' })).toBeEnabled();
  await page.getByRole('button', { name: 'System design', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'System design', exact: true })).toBeVisible();
  await expect(page.getByRole('button', {name:'System design', exact:true})).toHaveAttribute('aria-current','page');
  await expect(page.getByRole('img', { name: 'Invoice Desk current system architecture' })).toBeVisible();
  await expect(page.getByText('Different roles. One controlled decision.')).toBeVisible();
  await page.getByRole('button', { name: 'Presentation', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'An invoice should end with a clear next step.' })).toBeVisible();
  await page.getByRole('button', { name: 'Next slide' }).click();
  await expect(page.getByRole('heading', { name: 'Upload. Check. Decide.' })).toBeVisible();
  await page.getByRole('button', { name: 'Previous slide' }).click();
  await expect(page.getByText('Slide 1 of 7')).toBeVisible();
  await page.getByRole('button', { name: 'Open invoice workspace' }).click();
  await page.getByRole('button', { name: 'Refresh invoices' }).click();
  expect(processingRequests).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'System design', exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test('an invoice needing a decision puts review and blockers before extracted fields', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start a new invoice' })).toBeEnabled();
  await runSample(page, 'invoice_1012.txt');
  const review = page.getByRole('region', { name: 'Simulated review' });
  await expect(review).toBeVisible();
  const before = await page.locator('.record-pane').evaluate(pane => {
    const reviewCard = pane.querySelector('.review-card')!;
    const findings = pane.querySelector('.findings-card')!;
    const details = pane.querySelector('.extracted-card')!;
    return Boolean(reviewCard.compareDocumentPosition(findings) & Node.DOCUMENT_POSITION_FOLLOWING)
      && Boolean(findings.compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING);
  });
  expect(before).toBe(true);
  await page.setViewportSize({ width: 390, height: 844 });
  const reviewBox = await review.boundingBox();
  const sourceBox = await page.locator('.review-layout > .document-pane').boundingBox();
  expect(reviewBox!.y).toBeLessThan(sourceBox!.y);
});

test('valid invoice is processed once, traced and isolated from another workspace', async ({ page, browser }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start a new invoice' })).toBeEnabled();
  const runId = await runSample(page, 'invoice_1001.txt');
  await expect(page.getByText('Mock payment recorded')).toBeVisible();
  await expect(page.getByText('Simulated model', { exact: true })).toBeVisible();
  await expect(page.getByText('Widgets Inc.', { exact: true }).first()).toBeVisible();
  const [report] = await Promise.all([page.waitForEvent('download'), page.getByRole('button',{name:'Download report CSV'}).click()]);
  expect(report.suggestedFilename()).toContain(runId);
  await page.emulateMedia({media:'print'});
  await expect(page.getByRole('heading', {name:'Invoice decision report'})).toBeVisible();
  await expect(page.locator('.decision-report')).toContainText('Simulated receipt');
  await expect(page.locator('.sidebar')).toBeHidden();
  await page.emulateMedia({media:'screen'});
  const [download] = await Promise.all([
    page.waitForEvent('download'), page.getByRole('button', { name: 'Export trace' }).click(),
  ]);
  expect(download.suggestedFilename()).toContain(runId);
  await page.getByRole('button', { name: 'Back to invoices' }).click();
  await page.reload();
  await expect(page.getByRole('button', { name: 'INV-1001', exact: true })).toBeVisible();

  const other = await browser.newContext();
  const second = await other.newPage();
  await second.goto('http://127.0.0.1:18940/');
  await expect(second.getByRole('button', { name: 'Start a new invoice' })).toBeEnabled();
  const status = await second.evaluate(async (id) => (await fetch(`/api/v1/runs/${id}`)).status, runId);
  expect(status).toBe(404);
  await other.close();
});

test('a malformed invoice is rejected with findings and never shows a payment', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start a new invoice' })).toBeEnabled();
  await runSample(page, 'invoice_1007.csv');
  await expect(page.getByText('Rejected', { exact: true })).toBeVisible();
  await expect(page.getByText('Mock payment recorded')).toHaveCount(0);
  await expect(page.getByText('Payment blocked')).toBeVisible();
  await expect(page.getByLabel('Correction request draft')).toHaveValue(/Please review invoice/);
  await expect(page.getByText(/No message is sent from this application/)).toBeVisible();
  await expect(page.getByText(/total mismatch/i).first()).toBeVisible();
});

test('mobile intake supports keyboard dismissal without starting work', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  const newInvoice = page.getByRole('button', { name: 'Start a new invoice' });
  await expect(newInvoice).toBeEnabled();
  await newInvoice.click();
  await expect(page.getByRole('dialog', { name: 'New invoice' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Process invoice', exact: true })).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(newInvoice).toBeFocused();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
});

test('a reviewer resolves the vendor-name hold before a simulated payment', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start a new invoice' })).toBeEnabled();
  await runSample(page, 'invoice_1012.txt');
  await expect(page.getByText('Needs review', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Mock payment recorded')).toHaveCount(0);
  await expect(page.getByRole('checkbox', { name: /vendor identity change/i })).toBeVisible();
  if (process.env.UPDATE_SCREENSHOTS === '1') {
    await page.screenshot({ path: 'docs/screenshots/simulated-review.png', fullPage: true, animations: 'disabled' });
  }
  await page.getByRole('checkbox', { name: /vendor identity change/i }).check();
  await page.getByRole('checkbox', { name: /checked the source/i }).check();
  await page.getByLabel('Decision reason').selectOption('vendor_verified');
  await page.getByRole('button', { name: /approve & simulate payment/i }).click();
  await expect(page.getByText('Mock payment recorded')).toBeVisible({ timeout: 15_000 });
});

test('unsupported currency cannot be cleared by the review button', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start a new invoice' })).toBeEnabled();
  await runSample(page, 'invoice_1014.xml');
  await expect(page.getByText('Cannot approve yet')).toBeVisible();
  await expect(page.getByRole('option', { name: 'Approve and simulate payment' })).toHaveAttribute('disabled', '');
  await page.getByLabel('Decision reason').selectOption('payment_policy');
  await page.getByRole('button', { name: 'Reject invoice' }).click();
  await expect(page.getByText('Rejected', { exact: true })).toBeVisible();
  await expect(page.getByText('Mock payment recorded')).toHaveCount(0);
});

test('PDF review preserves the original and the extracted text', async ({ page }, testInfo) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start a new invoice' })).toBeEnabled();
  const runId = await runSample(page, 'invoice_1011.pdf');
  await expect(page.getByAltText('Original invoice PDF page 1')).toBeVisible();
  const original = await page.request.get(`/api/v1/runs/${runId}/original`);
  expect(original.status()).toBe(200);
  expect(original.headers()['content-type']).toBe('application/pdf');
  expect((await original.body()).subarray(0, 4).toString()).toBe('%PDF');
  await expect(page.getByAltText('Original invoice PDF page 1')).toHaveJSProperty('complete', true);
  await expect.poll(() => page.getByAltText('Original invoice PDF page 1').evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await page.screenshot({ path: testInfo.outputPath('pdf-review.png'), fullPage: true, animations: 'disabled' });
  await page.getByRole('button', { name: 'Extracted text', exact: true }).click();
  await expect(page.locator('.document-sheet pre')).toContainText('INV-1011');
  await expect(page.getByAltText('Original invoice PDF page 1')).toHaveCount(0);
  await page.getByRole('button', { name: 'Original PDF', exact: true }).click();
  await expect(page.getByAltText('Original invoice PDF page 1')).toBeVisible();
});

 test('department handoff persists on reload and keeps payment on hold', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start a new invoice' })).toBeEnabled();
  await runSample(page, 'invoice_1012.txt');
  await expect(page.getByLabel('Review action')).toBeVisible();
  await expect(page.getByRole('option', {name: 'Assign to a department'})).not.toHaveAttribute('disabled');
  await page.getByLabel('Review action').selectOption('route');
  await page.getByRole('combobox', {name: 'Department', exact:true}).selectOption('procurement');
  await page.getByLabel('Decision reason').selectOption('inventory_check');
  await page.getByRole('button', { name: 'Record department handoff' }).click();
  await expect(page.getByText('Assigned to Procurement', {exact:true})).toBeVisible();
  await expect(page.getByText('Mock payment recorded')).toHaveCount(0);
  if (process.env.UPDATE_SCREENSHOTS === '1') {
    await page.screenshot({path: 'docs/screenshots/department-handoff.png', fullPage:true, animations:'disabled'});
  }
  await page.reload();
  await page.getByRole('button', {name: 'Open INV 1012', exact:true}).click();
  await expect(page.getByText('Assigned to Procurement', {exact:true})).toBeVisible();
  await expect(page.getByText('Needs review', {exact:true}).first()).toBeVisible();
  await expect(page.getByText('Internal workspace record only. No department notification was sent.')).toBeVisible();
  await page.getByRole('combobox', {name: 'Review action', exact:true}).selectOption('route');
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({width, height:1000});
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  }
  await page.getByRole('tab', {name: /^Activity/}).click();
  await expect(page.getByText('Assigned to Procurement for review. Payment remains on hold. No notification was sent.', {exact:true})).toBeVisible();
});

test('light is the first look even on a dark computer; a saved choice persists on every view', async ({page}) => {
  await page.emulateMedia({colorScheme:'dark'});
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('data-theme','light');
  await page.getByRole('button',{name:'Switch to dark mode'}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  if (process.env.UPDATE_SCREENSHOTS === '1') await page.screenshot({path:'docs/screenshots/dark-workspace.png', animations:'disabled'});
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  await page.getByRole('button',{name:'System design',exact:true}).click();
  await expect(page.getByRole('heading',{name:'System design',exact:true})).toBeVisible();
  await expect(page.locator('.diagram-node rect').first()).toHaveCSS('fill','rgb(28, 41, 35)');
  await page.getByRole('button',{name:'Presentation',exact:true}).click();
  await expect(page.locator('.slide-stage')).toHaveCSS('background-color','rgb(28, 41, 35)');
  await page.getByRole('button',{name:'Switch to light mode'}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme','light');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme','light');
});

test('guided walkthrough waits for explicit processing and never submits a review', async ({page}) => {
  const mutations:string[]=[];
  page.on('request',request=> { if(request.method()==='POST' && /\/api\/v1\/runs(?:\/|$)/.test(new URL(request.url()).pathname)) mutations.push(new URL(request.url()).pathname); });
  await page.goto('/');
  await page.getByRole('button',{name:'Guided walkthrough',exact:true}).click();
  const guide=page.getByRole('region',{name:'Guided walkthrough'});
  await expect(guide.getByText('Step 1 of 11')).toBeVisible();
  await guide.getByRole('button',{name:'Next',exact:true}).click();
  await expect(guide.getByRole('button',{name:'Next',exact:true})).toBeDisabled();
  expect(mutations).toEqual([]);
  await page.getByRole('button',{name:'Start a new invoice'}).click();
  await guide.getByRole('button',{name:'Next',exact:true}).click();
  await page.getByRole('button',{name:'Sample invoices',exact:true}).click();
  await page.getByLabel('Use a sample invoice').selectOption({label:'invoice_1012.txt'});
  await guide.getByRole('button',{name:'Next',exact:true}).click();
  await expect(guide.getByRole('button',{name:'Next',exact:true})).toBeDisabled();
  expect(mutations).toEqual([]);
  await page.getByRole('button',{name:'Process invoice',exact:true}).click();
  await expect(guide.getByRole('button',{name:'Next',exact:true})).toBeEnabled();
  await guide.getByRole('button',{name:'Next',exact:true}).click();
  await expect(guide.getByRole('button',{name:'Next',exact:true})).toBeEnabled({timeout:15000});
  await guide.getByRole('button',{name:'Next',exact:true}).click();
  await guide.getByRole('button',{name:'Next',exact:true}).click();
  await guide.getByRole('button',{name:'Next',exact:true}).click();
  await expect(guide.getByRole('heading',{name:'Understand the review actions'})).toBeVisible();
  await expect(guide.getByText(/You do not have to submit a decision/)).toBeVisible();
  await guide.getByRole('button',{name:'Next',exact:true}).click();
  await page.getByRole('tab',{name:/^Activity/}).click();
  await guide.getByRole('button',{name:'Next',exact:true}).click();
  await guide.getByRole('button',{name:'Next',exact:true}).click();
  await guide.getByRole('button',{name:'Finish tour',exact:true}).click();
  await expect(guide).toHaveCount(0);
  expect(mutations).toEqual(['/api/v1/runs']);
  await expect(page.locator('.tour-highlight')).toHaveCount(0);
});

test('guide and theme controls work on mobile with the sidebar collapsed', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await page.goto('/');
  await page.getByRole('button',{name:'Switch to dark mode'}).click();
  await page.getByRole('button',{name:'Guided walkthrough',exact:true}).click();
  const guide=page.getByRole('region',{name:'Guided walkthrough'});
  await guide.getByRole('button',{name:'Next',exact:true}).click();
  await page.getByRole('button',{name:'Start a new invoice'}).click();
  await guide.getByRole('button',{name:'Next',exact:true}).click();
  await page.getByRole('button',{name:'Sample invoices',exact:true}).click();
  await page.getByLabel('Use a sample invoice').selectOption({label:'invoice_1001.txt'});
  await guide.getByRole('button',{name:'Next',exact:true}).click();
  const process=page.getByRole('button',{name:'Process invoice',exact:true});
  const buttonRect=await process.boundingBox();const guideRect=await guide.boundingBox();
  expect(buttonRect!.y+buttonRect!.height).toBeLessThan(guideRect!.y);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await guide.getByRole('button',{name:'Close walkthrough'}).click();
  await expect(page.getByRole('dialog',{name:'New invoice'})).toBeVisible();
  await expect(page.getByRole('dialog',{name:'New invoice'})).toBeFocused();
  await page.getByRole('button',{name:'Close new invoice'}).click();
  await page.getByRole('button',{name:'Switch to light mode'}).click();
  for (const width of [320,390,768,1440]) {
    await page.setViewportSize({width,height:844});
    const account=page.getByRole('region',{name:'Workspace account'});
    await expect(account).toBeVisible();
    const rect=await account.boundingBox();
    expect(rect!.y).toBeGreaterThanOrEqual(0);
    expect(rect!.y+rect!.height).toBeLessThanOrEqual(844);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  }
});

test('related versions use a workspace-scoped ledger lookup', async ({page}) => {
  await page.goto('/');
  await expect(page.getByRole('button',{name:'Start a new invoice'})).toBeEnabled();
  await runSample(page,'invoice_1001.txt');
  await page.getByRole('button',{name:'Find related versions'}).click();
  await expect(page.getByRole('heading',{name:'Find invoice',exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'INV-1001 · Widgets Inc.'})).toBeVisible();
  await expect(page.getByText(/simulated payment is recorded for this invoice/)).toBeVisible();
  if (process.env.UPDATE_SCREENSHOTS === '1') await page.screenshot({path:'docs/screenshots/invoice-lookup.png', animations:'disabled'});
  await page.getByRole('button',{name:'Open record'}).click();
  await expect(page.getByText('Mock payment recorded')).toBeVisible();
});
