import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const proof = JSON.parse(readFileSync(new URL('../replenishment-fixture.json', import.meta.url), 'utf8'));
const display = {version: 1, kind: 'replenishment_suggestion', outcome: 'succeeded', suggestion: proof};
const result = {call_id: 'b6', name: 'suggest_replenishment', ok: true, content: JSON.stringify(proof), display};

async function mock(page: Page) {
  const posts: string[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') posts.push(path);
    if (path === '/api/runtime') return route.fulfill({json: {data_source: 'erp', writes_enabled: true,
      drafts_enabled: true, business_approvals_enabled: true}});
    if (path === '/api/sessions') return route.fulfill({json: {items: [{session_id: 'b6-history',
      title: '补货建议历史', status: 'completed', updated_at: proof.assessed_at}], has_more: false}});
    if (path.endsWith('/order-drafts')) return route.fulfill({json: {items: []}});
    if (path.endsWith('/state')) {
      const id = path.split('/')[3];
      return route.fulfill({json: {session_id: id, status: 'completed', pending_approvals: [],
        messages: id === 'b6-history' ? [{role: 'user', content: '给我补货建议'},
          {role: 'assistant', tool_calls: [{id: 'b6', function: {name: result.name, arguments: '{"skus":["B6-A"],"lead_time_days":7}'}}]},
          {role: 'tool', tool_call_id: 'b6', content: result.content}] : [],
        tool_results: id === 'b6-history' ? [result] : []}});
    }
    if (path === '/api/chat/stream') return route.fulfill({contentType: 'text/event-stream', body: [
      ['start', {session_id: request.postDataJSON().session_id, model: 'scripted'}],
      ['tool_started', {id: 'b6', name: result.name, arguments: '{"skus":["B6-A"],"lead_time_days":7}'}],
      ['tool_finished', {...result, id: 'b6'}],
      ['done', {steps: 2, completed: true, duration_ms: 20, usage: null, cost: null, trace: null}],
    ].map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join('')});
    return route.fulfill({status: 404, json: {error: {message: path}}});
  });
  return posts;
}

for (const width of [320, 1440]) {
  test(`补货建议在 ${width}px 展示依据且仅产生查询对话`, async ({page}) => {
    await page.setViewportSize({width, height: 1000});
    const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    const posts = await mock(page);
    await page.goto('/');
    const input = page.getByRole('textbox', {name: '消息', exact: true});
    await expect(input).toBeEnabled(); await input.fill('给我补货建议'); await input.press('Enter');
    const card = page.locator('.replenishment-suggestion');
    await expect(card.getByText('只读测算', {exact: true})).toBeVisible();
    await expect(card.getByText('37 件', {exact: true})).toBeVisible();
    await expect(card.getByText('本次仅提供建议，未采购、未调整库存。')).toBeVisible();
    const summary = card.locator('summary'); await summary.focus(); await page.keyboard.press('Enter');
    await expect(card.locator('details')).toHaveAttribute('open', '');
    await expect(card.getByText(proof.sales_basis)).toBeVisible();
    await expect(card.getByRole('button')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(posts.filter(path => path !== '/api/chat/stream')).toEqual([]);
    expect(errors).toEqual([]);
    await page.screenshot({path: `../../.playwright-mcp/b6-${width}.png`, fullPage: true});
  });
}

test('历史补货建议恢复时展示原有快照并保持零变更请求', async ({page}) => {
  const posts = await mock(page); await page.goto('/');
  await page.getByText('补货建议历史', {exact: true}).click();
  await expect(page.locator('.replenishment-suggestion').getByText('37 件', {exact: true})).toBeVisible();
  expect(posts).toEqual([]);
});
