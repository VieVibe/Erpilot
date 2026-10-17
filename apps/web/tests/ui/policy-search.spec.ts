import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const proof = JSON.parse(readFileSync(new URL('../policy-search-fixture.json', import.meta.url), 'utf8'));
const display = {version: 1, kind: 'policy_search', outcome: 'succeeded', query: proof.query, search: proof};

async function mock(page: Page, variant = 'matched') {
  const search = variant === 'no_match' ? {...proof, policy_state: 'no_match', matches: []} : proof;
  const shown = variant === 'failed' ? {version: 1, kind: 'policy_search', outcome: 'failed',
    query: proof.query, message: '政策检索暂不可用，请稍后重试'} :
    variant === 'legacy' ? null : {...display, search};
  const content = variant === 'legacy' ? JSON.stringify({matches: proof.matches, note: '旧版政策片段'}) : JSON.stringify(search);
  const result = {call_id: 'rag', name: 'search_policy', ok: true, content, display: shown};
  const posts: string[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') posts.push(path);
    if (path === '/api/runtime') return route.fulfill({json: {data_source: 'erp', writes_enabled: true,
      drafts_enabled: true, business_approvals_enabled: true}});
    if (path === '/api/sessions') return route.fulfill({json: {items: [{session_id: 'rag-history',
      title: '退货政策检索历史', status: 'completed', updated_at: proof.queried_at}], has_more: false}});
    if (path.endsWith('/order-drafts')) return route.fulfill({json: {items: []}});
    if (path.endsWith('/state')) {
      const id = path.split('/')[3];
      return route.fulfill({json: {session_id: id, status: 'completed', pending_approvals: [],
        messages: id === 'rag-history' ? [{role: 'user', content: proof.query},
          {role: 'assistant', tool_calls: [{id: 'rag', function: {name: result.name, arguments: JSON.stringify({query: proof.query})}}]},
          {role: 'tool', tool_call_id: 'rag', content: result.content}] : [],
        tool_results: id === 'rag-history' ? [result] : []}});
    }
    if (path === '/api/chat/stream') return route.fulfill({contentType: 'text/event-stream', body: [
      ['start', {session_id: request.postDataJSON().session_id, model: 'scripted'}],
      ['tool_started', {id: 'rag', name: result.name, arguments: JSON.stringify({query: proof.query})}],
      ['tool_finished', {...result, id: 'rag'}],
      ['done', {steps: 2, completed: true, duration_ms: 20, usage: null, cost: null, trace: null}],
    ].map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join('')});
    return route.fulfill({status: 404, json: {error: {message: path}}});
  });
  return posts;
}

for (const width of [320, 1440]) {
  test(`RAG 在 ${width}px 直接展示原文高亮及版本证据`, async ({page}) => {
    await page.setViewportSize({width, height: 1000});
    const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    const posts = await mock(page); await page.goto('/');
    const input = page.getByRole('textbox', {name: '消息', exact: true});
    await expect(input).toBeEnabled(); await input.fill(proof.query); await input.press('Enter');
    const card = page.locator('.policy-search');
    await expect(card.getByText('找到政策依据', {exact: true})).toBeVisible();
    await expect(card.getByText(proof.query, {exact: true})).toBeVisible();
    await expect(card.getByRole('region', {name: '政策依据 1'})).toBeVisible();
    const excerpts = card.locator('.policy-excerpt');
    expect(await excerpts.allTextContents()).toEqual(proof.matches.map((item: {text: string}) => item.text));
    await expect(excerpts.first().locator('mark').filter({hasText: '退货'}).first()).toBeVisible();
    await expect(card.getByText(/版本 2026-10-12.1/).first()).toBeVisible();
    await expect(card.getByText('检索相似度 0.8512')).toBeVisible();
    await expect(card.getByRole('button')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(posts).toEqual(['/api/chat/stream']); expect(errors).toEqual([]);
    await card.screenshot({path: `../../.playwright-mcp/rag-evidence-${width}.png`});
  });
}

test('历史回放保留原查询时间、引用和高亮，零新请求', async ({page}) => {
  const posts = await mock(page); await page.goto('/');
  await page.getByText('退货政策检索历史', {exact: true}).click();
  const card = page.locator('.policy-search');
  await expect(card.getByText(proof.queried_at, {exact: true})).toBeVisible();
  await expect(card.locator('mark').filter({hasText: '退货'}).first()).toBeVisible();
  expect(posts).toEqual([]);
});

for (const variant of ['no_match', 'failed']) {
  test(`${variant} 回显缺口，不展示引用或高亮`, async ({page}) => {
    const posts = await mock(page, variant); await page.goto('/');
    await page.getByText('退货政策检索历史', {exact: true}).click();
    const card = page.locator('.policy-search');
    await expect(card.getByText(variant === 'failed' ? '政策检索未完成' : '未找到相关依据', {exact: true})).toBeVisible();
    await expect(card.locator('blockquote, mark')).toHaveCount(0);
    expect(posts).toEqual([]);
  });
}

test('旧版结果仍能展开查看原始证据，不补造查询时间', async ({page}) => {
  const posts = await mock(page, 'legacy'); await page.goto('/');
  await page.getByText('退货政策检索历史', {exact: true}).click();
  await page.locator('details.tool > summary').click();
  await expect(page.locator('.result')).toContainText('旧版政策片段');
  await expect(page.locator('.policy-search')).toHaveCount(0);
  expect(posts).toEqual([]);
});
