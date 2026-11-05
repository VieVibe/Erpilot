// Real local API + temporary ERP, with timed Chinese captions. No paid services.
import { chromium } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdir, writeFile, copyFile, access } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const output = resolve(root, process.argv[2] ?? 'artifacts/portfolio');
await mkdir(output, { recursive: true });
try { await access(resolve(output, 'erpilot-demo.webm')); throw new Error('Video already exists; choose a new directory.'); }
catch (error) { if (error.code !== 'ENOENT') throw error; }
const python = process.env.ERPILOT_DEMO_PYTHON ?? resolve(root,
  process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const port = Number(process.env.ERPILOT_RECORD_PORT ?? '8767');
const base = `http://127.0.0.1:${port}`;
// Never reuse somebody else's running server or issue mutations against it.
try { await fetch(`${base}/healthz`); throw new Error('Recording port already occupied.'); }
catch (error) { if (error.message === 'Recording port already occupied.') throw error; }
const server = spawn(python, ['scripts/demo.py', '--serve', '--port', String(port)], {
  cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
});
let log = '';
server.stdout.on('data', data => { log += data; });
server.stderr.on('data', data => { log += data; });
let browser, context;
const chapters = [];
const delay = ms => new Promise(resolveDelay => setTimeout(resolveDelay, ms));
try {
  let ready = false;
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error(`Demo server exited: ${log.slice(-1000)}`);
    try { const res = await fetch(`${base}/healthz`); if (res.ok) { ready = true; break; } } catch { /* startup */ }
    await delay(250);
  }
  if (!ready) throw new Error('Demo server did not become ready.');
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 },
    recordVideo: { dir: resolve(output, 'raw'), size: { width: 1440, height: 1000 } } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const start = performance.now();
  await page.goto(base);
  async function caption(title, body, seconds = 12) {
    const item = { title, body, start_seconds: (performance.now() - start) / 1000 };
    chapters.push(item);
    await page.evaluate(({ title, body }) => {
      document.getElementById('recording-caption')?.remove();
      const el = document.createElement('div');
      el.id = 'recording-caption';
      Object.assign(el.style, { position: 'fixed', bottom: '18px', left: '24px', right: '24px',
        zIndex: '99999', padding: '16px 22px', background: '#152b35f5', color: '#fff',
        borderRadius: '8px', fontFamily: 'Microsoft YaHei, sans-serif', pointerEvents: 'none',
        boxShadow: '0 4px 20px #0003', borderLeft: '5px solid #74cfad' });
      const heading = document.createElement('div');
      heading.textContent = title; heading.style.cssText = 'font-size:22px;font-weight:700;margin-bottom:6px';
      const text = document.createElement('div'); text.textContent = body; text.style.fontSize = '17px';
      el.append(heading, text); document.body.append(el);
    }, { title, body });
    await delay(seconds * 1000);
    item.end_seconds = (performance.now() - start) / 1000;
  }
  async function send(label) {
    const input = page.getByRole('textbox', { name: '消息', exact: true });
    await input.waitFor({ state: 'visible' });
    await page.locator('.demo-notice').getByRole('button', { name: label, exact: true }).click();
    await input.press('Enter');
  }
  async function textVisible(selector, text) {
    await page.locator(selector).filter({ hasText: text }).last().waitFor({ state: 'visible', timeout: 20000 });
  }
  await caption('Erpilot · 会请示的 ERP 智能体', '本片使用脚本模型、固定演示向量与临时数据库，展示正式 API → LangGraph → MCP 链路。', 12);
  await send('创建草稿');
  await textVisible('.md', '第 1 版');
  await caption('01 / 保存草稿', 'A1001 × 10、B2001 × 20，备注分箱发货。保存草稿不预留、不扣减库存。', 14);
  await send('修改第二行');
  await textVisible('.md', '3355');
  await caption('02 / 按当前版本修改', '本轮查询持久化任务，将第二行改为 50 件。版本 2 金额 ¥3355，客户与备注保留。', 14);
  await send('提交草稿');
  const panel = page.getByRole('complementary', { name: '业务面板' });
  await panel.getByRole('button', { name: '拒绝', exact: true }).waitFor({ state: 'visible' });
  await caption('03 / 审批绑定明细与版本', '核对客户、商品、数量和金额后决定。模型申请操作，业务层与人工审批控制执行。', 14);
  await page.reload();
  await panel.getByRole('button', { name: '拒绝', exact: true }).waitFor({ state: 'visible' });
  await caption('04 / 页面刷新后继续审批', '刷新恢复同一个审批对象与版本。本片采用内存 checkpoint，仅展示当前进程内恢复。', 14);
  await panel.getByRole('button', { name: '拒绝', exact: true }).click();
  await textVisible('.md', '操作未执行');
  await caption('05 / 拒绝后保持业务未执行', '本次拒绝不建单、不扣库存。草稿仍可继续编辑或重新请求提交。', 12);
  await panel.getByRole('tab', { name: '订单草稿', exact: true }).click();
  await panel.getByRole('button', { name: '请求提交版本 2 并审批', exact: true }).click();
  await page.getByRole('dialog', { name: '核对草稿版本 2' })
    .getByRole('button', { name: '确认此版本并申请审批', exact: true }).click();
  await panel.getByRole('button', { name: '批准', exact: true }).click();
  await textVisible('.md', '已创建');
  await caption('06 / 明确批准后建单', '批准版本 2 后创建待付款订单，业务层再次校验价格、库存与版本。', 14);
  await page.screenshot({ path: resolve(output, 'order-approved.png'), fullPage: true });
  await send('政策证据');
  await textVisible('.policy-search', '找到政策依据');
  await page.locator('.policy-search').last().scrollIntoViewIfNeeded();
  await caption('07 / 政策证据可复核', '证据卡保留来源、版本和原文。检索命中与最终答案正确性分别验收。', 14);
  await send('无依据拒答');
  await textVisible('.policy-search', '未找到相关依据');
  await page.locator('.policy-search').last().scrollIntoViewIfNeeded();
  await caption('08 / 缺乏依据时明确拒答', '语料没有火星移民政策，展示证据缺口。本片不代表真实模型的未知问题准确率。', 12);
  await send('补货建议');
  await textVisible('.replenishment-suggestion', '37 件');
  await page.locator('.replenishment-suggestion').last().scrollIntoViewIfNeeded();
  await caption('09 / 只读补货建议', '最近 30 天销量 60、库存 5；采购周期 7 天、到货后覆盖 14 天，建议补 37 件。未采购、未调库存。', 14);
  await page.screenshot({ path: resolve(output, 'replenishment.png'), fullPage: true });
  await caption('实现、实测与限制各自留证', '真实模型与故障恢复证据见仓库独立报告。容器支持账号隔离与持久恢复；本片只演示临时数据与固定脚本流程。', 12);
  if (errors.length) throw new Error(`Browser errors: ${errors.join('; ')}`);
  const video = page.video();
  await context.close(); context = null;
  await copyFile(await video.path(), resolve(output, 'erpilot-demo.webm'));
  await writeFile(resolve(output, 'chapters.json'), JSON.stringify({
    mode: 'scripted-offline-demo', source: 'real-local-api', paid_requests: 0,
    duration_seconds: (performance.now() - start) / 1000, chapters,
  }, null, 2));
  const stamp = seconds => {
    const ms = Math.floor(seconds * 1000);
    return `${String(Math.floor(ms / 3600000)).padStart(2, '0')}:${String(Math.floor(ms / 60000) % 60).padStart(2, '0')}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}.${String(ms % 1000).padStart(3, '0')}`;
  };
  await writeFile(resolve(output, 'erpilot-demo.vtt'), 'WEBVTT\n\n' + chapters.map((c, i) =>
    `${i + 1}\n${stamp(c.start_seconds)} --> ${stamp(c.end_seconds)}\n${c.title}\n${c.body}\n`).join('\n'));
  console.log(JSON.stringify({ video: resolve(output, 'erpilot-demo.webm'), chapters: chapters.length }));
} finally {
  if (context) await context.close();
  if (browser) await browser.close();
  server.kill();
  await Promise.race([new Promise(resolveExit => server.once('exit', resolveExit)), delay(5000)]);
}
