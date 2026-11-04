import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { resolve } from 'node:path';

const project = process.env.ERPILOT_DEPLOYMENT_PROJECT;
if (!project || !/^erpilot-[a-z0-9-]+$/.test(project) || !/(tests|readiness)/.test(project)) {
  throw new Error('Set ERPILOT_DEPLOYMENT_PROJECT to a dedicated erpilot-…-tests/readiness Compose project; the suite recreates its API.');
}
const root = resolve('../..');
const prefix = `test_${randomBytes(5).toString('hex')}`;
const password = randomBytes(24).toString('base64url');
const accounts = [
  { username: `${prefix}_alice`, role: 'approver' },
  { username: `${prefix}_bob`, role: 'approver' },
  { username: `${prefix}_reader`, role: 'operator' },
];

function compose(args: string[], input?: string) {
  return execFileSync('docker', ['compose', '--project-name', project!, ...args], {
    cwd: root, encoding: 'utf8', input, timeout: 90000, stdio: ['pipe', 'pipe', 'pipe'],
  }).trim();
}

test.beforeAll(() => {
  // Synthetic, random test credentials travel through stdin, never shell arguments or reports.
  compose(['exec', '-T', 'api', 'python', '-c',
    'import json,os,sys; from erpilot_api.auth_store import AuthStore; s=AuthStore(os.environ["ERPILOT_RUN_STORE_PATH"]); v=json.load(sys.stdin); [s.add_user(u["username"],v["password"],u["role"]) for u in v["accounts"]]'],
    JSON.stringify({ accounts, password }));
});

test.afterAll(() => {
  compose(['exec', '-T', 'api', 'python', '-c',
    'import json,os,sys; from erpilot_api.auth_store import AuthStore; s=AuthStore(os.environ["ERPILOT_RUN_STORE_PATH"]); [s.disable_user(u) for u in json.load(sys.stdin)]'],
    JSON.stringify(accounts.map(account => account.username)));
});

async function login(page: Page, account = 0) {
  await page.getByRole('textbox', { name: '账号', exact: true }).fill(accounts[account].username);
  await page.getByLabel('密码', { exact: true }).fill(password);
  await page.getByRole('button', { name: '登录工作台', exact: true }).click();
  await expect(page.locator('.account-bar')).toContainText(accounts[account].username);
  await expect(page.getByRole('textbox', { name: '消息', exact: true })).toBeEnabled();
  await expect(page.locator('.demo-notice')).toContainText('持久化演示数据');
}

async function sendDemo(page: Page, label: string) {
  const input = page.getByRole('textbox', { name: '消息', exact: true });
  await expect(input).toBeEnabled();
  await page.locator('.demo-notice').getByRole('button', { name: label, exact: true }).click();
  await input.press('Enter');
}

for (const width of [320, 1440]) {
  test(`同源登录、账号切换和只读角色在 ${width}px 可用`, async ({ page, context }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto('/');
    await login(page);
    const cookie = (await context.cookies()).find(item => item.name === 'erpilot_login');
    expect(cookie?.httpOnly).toBe(true);
    expect(cookie?.sameSite).toBe('Strict');
    await sendDemo(page, '创建草稿');
    await expect(page.locator('.md').last()).toContainText('第 1 版');
    await expect(page.getByRole('textbox', { name: '消息', exact: true })).toBeEnabled();
    const ownSession = await page.evaluate(() => {
      const key = Object.keys(localStorage).find(key => key.startsWith('erpilot-session-id:'))!;
      return localStorage.getItem(key)!;
    });
    const posts: string[] = [];
    page.on('request', request => { if (request.method() === 'POST') posts.push(request.url()); });
    await page.reload();
    await expect(page.locator('.md').last()).toContainText('第 1 版');
    await expect(page.getByRole('textbox', { name: '消息', exact: true })).toBeEnabled();
    expect(posts).toEqual([]);
    await page.getByRole('button', { name: '退出登录', exact: true }).click();
    await login(page, 1);
    await expect(page.locator('.md')).toHaveCount(0);
    expect((await context.request.get(`/api/sessions/${ownSession}/state`)).status()).toBe(404);
    await page.getByRole('button', { name: '退出登录', exact: true }).click();
    await login(page, 2);
    await expect(page.locator('.account-bar')).toContainText('查询与准备草稿');
    // Drafts remain useful for operators, while the server and UI prohibit execution.
    await sendDemo(page, '创建草稿');
    await expect(page.locator('.md').last()).toContainText('第 1 版');
    await expect(page.getByRole('textbox', { name: '消息', exact: true })).toBeEnabled();
    if (width === 320) {
      await page.screenshot({ path: testInfo.outputPath('workspace-320.png'), animations: 'disabled', fullPage: true });
      await page.getByRole('button', { name: '业务面板', exact: true }).click();
      await expect(page.getByRole('dialog', { name: '业务面板', exact: true })).toBeVisible();
    }
    await expect(page.getByRole('button', { name: '请求提交版本 1 并审批', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '请求提交版本 1 并审批', exact: true })).toBeDisabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const storage = await page.evaluate(() => JSON.stringify(localStorage));
    expect(storage).not.toContain(password);
    expect(storage).not.toContain('csrf');
    await page.screenshot({ path: testInfo.outputPath(`authenticated-${width}.png`), animations: 'disabled', fullPage: true });
  });
}

test('重建 API 容器后恢复原账号、同一版本待审批，业务只执行一次', async ({ page, context, request }, testInfo) => {
  await page.goto('/');
  await login(page);
  await sendDemo(page, '创建草稿');
  await expect(page.locator('.md').last()).toContainText('第 1 版');
  await sendDemo(page, '修改第二行');
  await expect(page.locator('.md').last()).toContainText('3355');
  await sendDemo(page, '提交草稿');
  const panel = page.getByRole('complementary', { name: '业务面板' });
  await expect(panel.getByRole('button', { name: '批准', exact: true })).toBeEnabled();
  const snapshot = await page.evaluate(async () => {
    const me = await (await fetch('/api/auth/me')).json();
    const sid = localStorage.getItem(`erpilot-session-id:${me.user.user_id}`)!;
    return { sid, state: await (await fetch(`/api/sessions/${sid}/state`)).json() };
  });
  const pending = snapshot.state.pending_approvals[0].pending_id;
  const draft = snapshot.state.order_task.draft;
  expect(draft.revision).toBe(2);
  compose(['up', '-d', '--no-deps', '--force-recreate', '--wait', 'api']);
  await expect.poll(async () => (await request.get('/readyz')).status()).toBe(200);
  await page.reload();
  await expect(page.locator('.account-bar')).toContainText(accounts[0].username);
  await expect(panel.getByRole('tabpanel', { name: /^审批/ }).getByText('总金额 ¥3355.00')).toBeVisible();
  await expect(panel.getByRole('button', { name: '批准', exact: true })).toBeEnabled();
  const after = await context.request.get(`/api/sessions/${snapshot.sid}/state`);
  expect((await after.json()).pending_approvals[0].pending_id).toBe(pending);
  await panel.getByRole('button', { name: '批准', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '消息', exact: true })).toBeEnabled();
  await expect(page.locator('.md').last()).toContainText('已创建');
  const me = await (await context.request.get('/api/auth/me')).json();
  const duplicate = await context.request.post('/api/chat/approve/stream', {
    headers: { 'X-CSRF-Token': me.csrf_token },
    data: { session_id: snapshot.sid, pending_id: pending, approved: true },
  });
  expect(duplicate.status()).toBe(409);
  const state = await (await context.request.get(`/api/sessions/${snapshot.sid}/state`)).json();
  expect(state.pending_approvals).toEqual([]);
  const submitted = await (await context.request.get(
    `/api/sessions/${snapshot.sid}/order-drafts/${draft.draft_id}`)).json();
  expect(submitted.status).toBe('submitted');
  expect(submitted.order_id).toBeTruthy();
  const audit = await (await context.request.get('/api/auth/audit')).json();
  expect(audit.items.some((row: any) => row.action === 'approval_request' && row.detail.pending_id === pending)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('restored-approved.png'), fullPage: true });
});
