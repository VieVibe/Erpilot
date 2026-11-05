import { test as base, expect, type Page } from '@playwright/test';

const test = base.extend<{ browserDiagnostics: void }>({
  browserDiagnostics: [async ({ page }, use) => {
    const diagnostics: string[] = [];
    page.on('pageerror', error => diagnostics.push(error.message));
    page.on('console', message => {
      if (message.type() === 'warning' || message.type() === 'error') {
        diagnostics.push(`${message.type()}: ${message.text()}`);
      }
    });
    await use();
    expect(diagnostics, '正式离线演示不应产生浏览器错误或告警').toEqual([]);
  }, { auto: true }],
});

async function chooseAndSend(page: Page, label: string) {
  const input = page.getByRole('textbox', { name: '消息', exact: true });
  await expect(input).toBeEnabled();
  await page.locator('.demo-notice').getByRole('button', { name: label, exact: true }).click();
  await input.press('Enter');
}

test('商品名称和中文件数经正式 API 保存完整草稿并恢复原始要求',async({page})=>{
  await page.goto('/');
  await chooseAndSend(page,'商品名称下单');
  await expect(page.locator('.md').last()).toContainText('已按名称和中文件数保存第 1 版草稿');
  const panel=page.getByRole('region',{name:'订单草稿',exact:true});
  await expect(panel).toContainText('总金额 ¥350.00');
  await panel.getByText('查看原始商品要求',{exact:true}).click();
  await expect(panel).toContainText('青瓷茶具两件 → 青瓷茶具（A1001），2 件');
  await expect(panel).toContainText('三件宣纸礼盒 → 宣纸礼盒（B2001），3 件');
  await expect(panel.getByLabel('预算与待确认事项')).toContainText('¥500.00');
  const posts:string[]=[];
  page.on('request',request=>{if(request.method()==='POST')posts.push(request.url());});
  await page.reload();
  await page.getByRole('region',{name:'订单草稿',exact:true}).getByText('查看原始商品要求',{exact:true}).click();
  await expect(page.getByRole('region',{name:'订单草稿',exact:true})).toContainText('三件宣纸礼盒');
  expect(posts).toEqual([]);
});

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
    await expect(page.locator('.md').last()).toContainText('相关政策原文');
    await expect(page.locator('.md').last()).toContainText('数量梯度折扣');
    await expect(page.locator('.md').last()).not.toContainText('尚未完成逐问适用性判断');
    await expect(page.locator('.md').last()).not.toContainText('本轮检索未覆盖这一问题');
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
  const records = page.getByRole('region', { name: '草稿修改记录' });
  await page.getByRole('button', { name: '查看修改记录', exact: true }).click();
  await expect(records.getByRole('article', { name: '草稿版本 2 修改记录' })).toContainText('数量：20 → 50');
  await expect(records.getByRole('article', { name: '草稿版本 1 修改记录' })).toContainText('用户原话');
  await page.reload();
  await page.getByRole('button', { name: '查看修改记录', exact: true }).click();
  await expect(records.getByRole('article', { name: '草稿版本 2 修改记录' })).toContainText('数量：20 → 50');
  await chooseAndSend(page, '提交草稿');
  const input = page.getByRole('textbox', { name: '消息', exact: true });
  const panel = page.getByRole('complementary', { name: '业务面板' });
  await expect(panel.getByRole('button', { name: '拒绝', exact: true })).toBeEnabled();
  await expect(input).toBeDisabled();
  await page.reload();
  await expect(panel.getByRole('tabpanel', { name: /^审批/ }).getByText('总金额 ¥3355.00')).toBeVisible();
  await expect(panel.getByRole('button', { name: '拒绝', exact: true })).toBeEnabled();
  await panel.getByRole('button', { name: '拒绝', exact: true }).click();
  await expect(input).toBeEnabled();
  await expect(page.locator('.md').last()).toContainText('操作未执行');
  await panel.getByRole('tab', { name: '订单草稿', exact: true }).click();
  await panel.getByRole('button', { name: '请求提交版本 2 并审批', exact: true }).click();
  const confirmation = page.getByRole('dialog', {name: '核对草稿版本 2'});
  await expect(confirmation.getByText('总金额 ¥3355.00')).toBeVisible();
  await confirmation.getByRole('button', {name: '确认此版本并申请审批', exact: true}).click();
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

test('表单预算超限后明确提高预算，刷新恢复并批准正确版本',async({page})=>{
  await page.goto('/');
  const panel=page.getByRole('complementary',{name:'业务面板'});
  await panel.getByRole('textbox',{name:'草稿客户',exact:true}).fill('预算演示客户');
  await panel.getByRole('textbox',{name:'第 1 行 SKU',exact:true}).fill('A1001');
  await panel.getByRole('spinbutton',{name:'第 1 行数量',exact:true}).fill('10');
  await panel.getByRole('textbox',{name:'预算上限',exact:true}).fill('500');
  await panel.getByRole('button',{name:'创建草稿',exact:true}).click();
  await expect(panel.getByText('待确认：报价超出预算',{exact:true})).toBeVisible();
  await expect(panel.getByRole('button',{name:'请求提交版本 1 并审批',exact:true})).toBeDisabled();
  await page.reload();
  await expect(panel.getByRole('textbox',{name:'预算上限',exact:true})).toHaveValue('500');
  await panel.getByRole('textbox',{name:'预算上限',exact:true}).fill('1000');
  await panel.getByRole('button',{name:'保存 / 刷新报价',exact:true}).click();
  await panel.getByRole('button',{name:'请求提交版本 2 并审批',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'核对草稿版本 2'});
  await expect(dialog).toContainText('预算上限：¥1000.00');
  await dialog.getByRole('button',{name:'确认此版本并申请审批',exact:true}).click();
  await panel.getByRole('button',{name:'批准',exact:true}).click();
  await expect(page.locator('.md').last()).toContainText('已创建');
  await expect(page.locator('.md').last()).toContainText('980.00');
});
