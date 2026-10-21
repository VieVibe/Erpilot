import { test, expect, type Page, type Route } from '@playwright/test';

const sha = 'a'.repeat(64);
const unsafe = '<script>window.erpilotHistoryUnsafe=true</script>';
const draft = {draft_id: 'history-draft', revision: 3, customer: '完整客户甲', note: unsafe,
  items: [{sku: 'A', name: '测试商品', quantity: 5, base_price: 100, discount: 1,
    unit_price: 100, line_total: 500, stock_before: 1000, stock_after: 995}],
  total_amount: 500, expires_at: '2099-01-01T00:00:00Z', status: 'open', order_id: null};
const constraints = {schema_version: 1, fields: {
  customer: {value: '完整客户甲', verified: true,
    origin: {kind: 'user_literal', revision: 1, quote: '给完整客户甲保存报价草稿', source_user_sha256: sha}},
  note: {value: unsafe, verified: false, origin: {kind: 'unverified', revision: 1}},
}, unverified_fields: ['note', 'items']};
function historyItem(revision: number) {
  return {draft_id: draft.draft_id, revision, created_at: '2026-10-21T10:00:00Z', task_constraints: constraints,
    change_record: {schema_version: 1, revision, previous_revision: revision - 1, operation: 'revise_quantity',
      confirmed_fields: [], source: {kind: 'agent', user_request: `把第一行改成${revision + 2}件；${unsafe}`, source_user_sha256: sha},
      changes: [{field: 'quantity', sku: 'A', before: revision + 1, after: revision + 2}]}};
}
const history = {schema_version: 1, draft_id: draft.draft_id, latest_revision: 3,
  items: [historyItem(3), historyItem(2)], has_more: true, next_before_revision: 2};
async function mock(page: Page, historyRoute?: (route: Route) => Promise<void>) {
  const writes: string[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (request.method() !== 'GET') writes.push(path);
    const json = (body: unknown, status = 200) => route.fulfill({status, json: body});
    if (path === '/api/runtime') return json({data_source: 'erp', writes_enabled: true, drafts_enabled: true});
    if (path === '/api/sessions') return json({items: [{session_id: 'other-session', title: '另一个任务', status: 'completed', updated_at: '2026-10-21T10:00:00Z'}], has_more: false});
    if (path.endsWith('/state')) return json({session_id: path.split('/')[3], status: 'completed', messages: [], tool_results: [], pending_approvals: []});
    if (path.endsWith('/order-drafts')) return json({items: [path.includes('/other-session/') ? {...draft, draft_id: 'other-draft', customer: '客户乙'} : draft]});
    if (path.endsWith('/history')) {
      if (historyRoute) return historyRoute(route);
      if (!url.searchParams.has('before_revision')) return json(history);
      const legacy = {...historyItem(1), change_record: null};
      legacy.task_constraints = structuredClone(constraints);
      for (const field of ['customer', 'note'] as const) {
        Object.assign(legacy.task_constraints.fields[field], {verified: false, origin: {kind: 'legacy_snapshot', revision: 1}});
      }
      legacy.task_constraints.unverified_fields = ['customer', 'note', 'items'];
      return json({...history, items: [legacy], has_more: false, next_before_revision: null});
    }
    return json({error: {message: 'Unexpected route: ' + path}}, 404);
  });
  return writes;
}

for (const width of [320, 1440]) {
  test(`修改记录在 ${width}px 展示差异与来源，分页不写业务`, async ({page}) => {
    await page.setViewportSize({width, height: 1000});
    const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    const writes = await mock(page); await page.goto('/');
    if (width < 1200) await page.getByRole('button', {name: '业务面板', exact: true}).click();
    await page.getByRole('button', {name: '查看修改记录', exact: true}).click();
    const panel = page.getByRole('region', {name: '草稿修改记录'});
    const latest = panel.getByRole('article', {name: '草稿版本 3 修改记录'});
    await expect(latest.getByText('A · 数量：4 → 5', {exact: true})).toBeVisible();
    await expect(latest.getByText('用户原话', {exact: true})).toBeVisible();
    await expect(latest.getByText('来源未核验', {exact: true})).toBeVisible();
    await latest.getByText('本次用户请求', {exact: true}).click();
    await expect(latest.locator('blockquote')).toContainText(unsafe);
    await expect(panel.locator('script')).toHaveCount(0);
    await page.getByRole('button', {name: '查看更早版本', exact: true}).click();
    await expect(panel.getByText('此版本未记录修改来源，不能补推原始要求。')).toBeVisible();
    expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).erpilotHistoryUnsafe)).toBeUndefined();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(writes).toEqual([]); expect(errors).toEqual([]);
    await latest.scrollIntoViewIfNeeded();
    if (width >= 1200) {
      const header = await page.locator('.workspace-header').boundingBox();
      expect(header?.y).toBeGreaterThanOrEqual(0);
      expect(await page.locator('.workspace').evaluate(element => element.scrollTop)).toBe(0);
    }
    await page.screenshot({path: `../../.playwright-mcp/draft-history-${width}.png`});
  });
}

test('历史读取失败可重试，损坏记录不冒充有效来源', async ({page}) => {
  let requests = 0;
  await mock(page, async route => {
    requests++;
    return requests === 1 ? route.fulfill({status: 503, json: {error: {message: '历史暂时不可用'}}}) :
      requests === 2 ? route.fulfill({json: {...history, next_before_revision: 99}}) :
        route.fulfill({json: history});
  });
  await page.goto('/'); await page.getByRole('button', {name: '查看修改记录', exact: true}).click();
  await expect(page.getByText('历史暂时不可用', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: '重试', exact: true}).click();
  await expect(page.getByText('草稿修改记录格式无效', {exact: true})).toBeVisible();
  await expect(page.getByRole('article', {name: '草稿版本 3 修改记录'})).toHaveCount(0);
  await page.getByRole('button', {name: '重试', exact: true}).click();
  await expect(page.getByRole('article', {name: '草稿版本 3 修改记录'})).toBeVisible();
});

test('切换会话后迟到的旧修改记录不会串入新任务', async ({page}) => {
  let release!: () => void;
  let entered!: () => void;
  const started = new Promise<void>(resolve => {entered = resolve;});
  const gate = new Promise<void>(resolve => {release = resolve;});
  await mock(page, async route => {
    if (route.request().url().includes('/other-session/')) {
      const item = structuredClone(historyItem(3)); item.draft_id = 'other-draft';
      item.task_constraints.fields.customer.value = '客户乙';
      item.task_constraints.fields.customer.origin.quote = '给客户乙保存报价草稿';
      return route.fulfill({json: {...history, draft_id: 'other-draft', items: [item], has_more: false, next_before_revision: null}});
    }
    entered(); await gate;
    await route.fulfill({json: history}).catch(() => {});
  });
  await page.goto('/'); await page.getByRole('button', {name: '查看修改记录', exact: true}).click();
  await started;
  await page.getByText('另一个任务', {exact: true}).click();
  await expect(page.getByRole('textbox', {name: '草稿客户'})).toHaveValue('客户乙');
  await page.getByRole('button', {name: '查看修改记录', exact: true}).click();
  await expect(page.getByRole('region', {name: '草稿修改记录'}).getByText('客户乙', {exact: true})).toBeVisible();
  release();
  await expect(page.getByRole('region', {name: '草稿修改记录'}).getByText('完整客户甲', {exact: true})).toHaveCount(0);
});
