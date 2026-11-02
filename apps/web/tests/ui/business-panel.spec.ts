import { test, expect, type Page, type Locator } from '@playwright/test';

const longSku = 'PRODUCT-' + 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.repeat(3);
const items = Array.from({ length: 18 }, (_, index) => ({
  sku: `${longSku}-${index + 1}`, name: `完整商品名称-${index + 1}-多规格组合装`,
  quantity: 2, base_price: 100, discount: 1, unit_price: 100, line_total: 200,
  stock_before: 100, stock_after: 98,
}));
const draft = { draft_id: 'long-draft', revision: 1, customer: '商品明细验收客户',
  note: '最后一条备注：所有商品均须完整核对', items, total_amount: 3600,
  expires_at: '2099-01-01T00:00:00Z', status: 'open', order_id: null };

async function mock(page: Page, approval = false) {
  const operation = { operation_id: 'cancel-long-order', kind: 'cancel_order', status: 'open',
    expires_at: draft.expires_at, invalid_reason: null,
    summary: { order_id: 'SO-LONG', customer: draft.customer, status_before: '待付款',
      status_after: '已取消', total_amount: 3600, note: draft.note,
      items: items.map(item => ({ ...item, stock_after: 102, warehouse: '主仓' })) } };
  const pending = { call_id: 'cancel-call', pending_id: 'cancel-pending', tool: 'cancel_order',
    risk: 'single_confirm', arguments: { operation_id: operation.operation_id }, operation };
  const results = items.map((item, index) => ({ call_id: `stock-${index}`, name: 'get_stock',
    ok: true, content: '{}', display: { version: 1, kind: 'stock_query', outcome: 'succeeded',
      sku: item.sku, quantity: item.stock_before, available: true } }));
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/runtime') return route.fulfill({ json: { data_source: 'demo',
      writes_enabled: true, drafts_enabled: true, business_approvals_enabled: true } });
    if (path === '/api/sessions') return route.fulfill({ json: { items: [], has_more: false } });
    if (path.endsWith('/order-drafts')) return route.fulfill({ json: { items: [draft] } });
    if (path.includes('/operations/')) return route.fulfill({ json: operation });
    if (path.endsWith('/state')) return route.fulfill({ json: {
      session_id: path.split('/')[3], status: approval ? 'waiting_approval' : 'completed',
      pending_approvals: approval ? [pending] : [], tool_results: results,
      messages: [{ role: 'assistant', tool_calls: results.map(result => ({ id: result.call_id,
        function: { name: result.name, arguments: '{}' } })) }],
    } });
    return route.fulfill({ status: 404, json: { error: { message: path } } });
  });
}

// Use a real wheel gesture: scrollIntoView can move even a clipped overflow:hidden ancestor.
async function wheelToEnd(page: Page, panel: Locator) {
  await panel.hover(); // Wait for the drawer's entrance animation to settle.
  const box = await panel.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.wheel(0, 30000);
}

for (const width of [320, 1024, 1200, 1440, 1920]) {
  test(`${width}px 业务面板可滚动至全部商品和提交按钮，表格可横向查看`, async ({ page }) => {
    await page.setViewportSize({ width, height: 600 });
    await mock(page); await page.goto('/');
    if (width < 1200) await page.getByRole('button', { name: '业务面板', exact: true }).click();
    const panel = width < 1200 ? page.getByRole('dialog', { name: '业务面板', exact: true })
      : page.getByRole('complementary', { name: '业务面板' });
    await expect(panel.getByRole('textbox', { name: '第 18 行 SKU', exact: true })).toHaveValue(items[17].sku);
    await wheelToEnd(page, panel);
    const submit = panel.getByRole('button', { name: '请求提交版本 1 并审批', exact: true });
    await expect(submit).toBeInViewport();
    await expect(panel.getByRole('tab', { name: '订单草稿', exact: true })).toBeInViewport();
    await expect(panel.getByText('备注：' + draft.note, { exact: true })).toBeInViewport();
    const table = panel.locator('.draft-table .ant-table-content');
    await expect.poll(() => table.evaluate(el => el.scrollWidth > el.clientWidth)).toBe(true);
    await table.evaluate(el => { el.scrollLeft = el.scrollWidth; });
    await expect(panel.getByRole('cell', { name: '100 → 98', exact: true }).last()).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

for (const width of [320, 1440]) {
  test(`${width}px 审批和结果均可滚动，长商品编码完整换行`, async ({ page }) => {
    await page.setViewportSize({ width, height: 600 });
    await mock(page, true); await page.goto('/');
    if (width < 1200) await page.getByRole('button', { name: '业务面板', exact: true }).click();
    const panel = width < 1200 ? page.getByRole('dialog', { name: '业务面板', exact: true })
      : page.getByRole('complementary', { name: '业务面板' });
    await expect(panel.getByRole('tab', { name: /审批/ })).toHaveAttribute('aria-selected', 'true');
    await wheelToEnd(page, panel);
    await expect(panel.getByRole('button', { name: '批准', exact: true })).toBeInViewport();
    await expect(panel.getByRole('tab', { name: /审批/ })).toBeInViewport();
    await panel.getByRole('tab', { name: '业务结果', exact: true }).click();
    const title = panel.locator('.ant-card-head-title').first();
    await expect(title).toContainText(items[0].sku);
    await expect(title).toHaveCSS('white-space', 'normal');
    expect(await title.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await wheelToEnd(page, panel);
    await expect(panel.locator('.card').last()).toBeInViewport();
    await expect(panel.getByRole('tab', { name: '业务结果', exact: true })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
