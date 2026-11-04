import {test, expect} from '@playwright/test';

test('候选额度不可用时仍展示对话和手动核对入口', async ({page}) => {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/runtime') return route.fulfill({json: {
      data_source: 'mcp', writes_enabled: false, drafts_enabled: true,
      intent_preview_enabled: true, intent_semantic_enabled: true,
      intent_semantic_available: false, intent_semantic_revision_enabled: true,
    }});
    if (path.endsWith('/state')) return route.fulfill({json: {
      session_id: path.split('/')[3], status: 'completed', messages: [],
      tool_results: [], pending_approvals: [],
    }});
    if (path.endsWith('/pending-intent/preview')) return route.fulfill({json: {preview: null}});
    return route.fulfill({json: {items: [], has_more: false}});
  });
  await page.goto('/');
  await expect(page.locator('.header-actions .runtime-label')).toContainText('智能解析暂不可用，可手动核对');
  await expect(page.getByRole('textbox', {name: '消息', exact: true})).toBeEnabled();
  await expect(page.getByRole('textbox', {name: '草稿客户', exact: true})).toBeVisible();
});
