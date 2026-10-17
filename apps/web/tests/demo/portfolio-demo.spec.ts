import { test, expect, type Page } from '@playwright/test';

async function chooseAndSend(page: Page, label: string) {
  const input = page.getByRole('textbox', { name: '消息', exact: true });
  await expect(input).toBeEnabled();
  await page.locator('.demo-notice').getByRole('button', { name: label, exact: true }).click();
  await input.press('Enter');
}

for (const width of [320, 1440]) {
  test(`真实离线服务在 ${width}px 展示证据、拒答及补货依据`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/');
    await expect(page.locator('.demo-notice')).toContainText('离线脚本化演示');
    await chooseAndSend(page, '政策证据');
    await expect(page.locator('.policy-search')).toContainText('找到政策依据');
    await expect(page.locator('.policy-search')).toContainText('pricing-policy.md');
    await chooseAndSend(page, '无依据拒答');
    await expect(page.locator('.policy-search').last()).toContainText('未找到相关依据');
    await chooseAndSend(page, '补货建议');
    await expect(page.locator('.replenishment-suggestion')).toContainText('37 件');
    await expect(page.locator('.replenishment-suggestion')).toContainText('采购周期 7 天');
    await expect(page.getByRole('textbox', { name: '消息', exact: true })).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const posts: string[] = [];
    page.on('request', request => { if (request.method() === 'POST') posts.push(request.url()); });
    await page.reload();
    await expect(page.locator('.replenishment-suggestion')).toContainText('37 件');
    await expect(page.getByRole('textbox', { name: '消息', exact: true })).toBeEnabled();
    expect(posts).toEqual([]);
    expect(errors).toEqual([]);
    await page.screenshot({ path: `../../.playwright-mcp/demo-${width}.png`, fullPage: true });
    await page.locator('.replenishment-suggestion').screenshot({ path: `../../.playwright-mcp/demo-replenishment-${width}.png` });
  });
}

test('草稿修改后断线恢复同版本审批，拒绝后可重新提交批准', async ({ page }) => {
  await page.goto('/');
  await chooseAndSend(page, '创建草稿');
  await expect(page.locator('.md').last()).toContainText('第 1 版');
  await chooseAndSend(page, '修改第二行');
  await expect(page.locator('.md').last()).toContainText('3355');
  await chooseAndSend(page, '提交草稿');
  const input = page.getByRole('textbox', { name: '消息', exact: true });
  const panel = page.getByRole('complementary', { name: '业务面板' });
  await expect(panel.getByRole('button', { name: '拒绝', exact: true })).toBeEnabled();
  await expect(input).toBeDisabled();
  await page.reload();
  await expect(panel.getByText('总金额 ¥3355.00')).toBeVisible();
  await expect(panel.getByRole('button', { name: '拒绝', exact: true })).toBeEnabled();
  await panel.getByRole('button', { name: '拒绝', exact: true }).click();
  await expect(input).toBeEnabled();
  await expect(page.locator('.md').last()).toContainText('操作未执行');
  await panel.getByRole('tab', { name: '订单草稿', exact: true }).click();
  await panel.getByRole('button', { name: '请求提交版本 2 并审批', exact: true }).click();
  await expect(panel.getByRole('button', { name: '批准', exact: true })).toBeEnabled();
  await panel.getByRole('button', { name: '批准', exact: true }).click();
  await expect(input).toBeEnabled();
  await expect(page.locator('.md').last()).toContainText('已创建');
  await expect(page.getByRole('button', { name: '新会话', exact: true })).toBeEnabled();
  await expect(page.locator('.demo-notice').getByRole('button', { name: '政策证据' })).toBeEnabled();
  await expect(page.locator('.md').filter({ hasText: '审批已拒绝' })).toHaveCount(1);
  await expect(page.locator('.session-history .history-meta').first()).toContainText('已完成');
  await page.screenshot({ path: '../../.playwright-mcp/demo-order-approved.png', fullPage: true });
});
